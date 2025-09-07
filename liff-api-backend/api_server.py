import os
import psycopg2
import psycopg2.extras
# [ADDED] 引入 Flask 的 g 物件用於儲存單次請求的連線，以及引入連線池
from flask import Flask, request, jsonify, render_template, g
from flask_cors import CORS
from psycopg2 import pool
from datetime import datetime, timedelta, date
import pytz

# --- 初始化設定 ---
app = Flask(__name__)
CORS(app)
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

DATABASE_URL = os.environ.get('DATABASE_URL')
ADMIN_USER_IDS_str = os.environ.get('ADMIN_USER_IDS', '')
ADMIN_USER_IDS = [uid.strip() for uid in ADMIN_USER_IDS_str.split(',') if uid.strip()]

# [ADDED] 建立全域的資料庫連線池
# 伺服器啟動時，會預先建立 1 個連線，最多可擴展至 5 個連線
try:
    connection_pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=5,
        dsn=DATABASE_URL,
        sslmode='require' # Render 的資料庫需要 SSL
    )
    print("資料庫連線池建立成功。")
except Exception as e:
    print(f"建立資料庫連線池失敗: {e}")
    connection_pool = None

# --- 資料庫輔助函式 ---
# [MODIFIED] get_db_connection 現在會從連線池中取得連線
# 並將其儲存在 Flask 的 g 物件中，確保在同一個請求中重複使用同一個連線
def get_db_connection():
    if 'db_conn' not in g:
        if connection_pool:
            g.db_conn = connection_pool.getconn()
        else:
            raise Exception("資料庫連線池不可用。")
    return g.db_conn

# [ADDED] 建立一個 teardown 函式，Flask 會在每次請求結束後自動呼叫它
# 無論請求成功或失敗，它都會確保連線被安全地歸還到池中
@app.teardown_appcontext
def close_db_connection(e=None):
    db_conn = g.pop('db_conn', None)
    if db_conn is not None and connection_pool:
        connection_pool.putconn(db_conn)

def init_db():
    print("正在檢查並初始化資料庫...")
    # [MODIFIED] init_db 現在也從 get_db_connection 獲取連線
    conn = get_db_connection()
    with conn.cursor() as cur:
        # [新增 is_vip 欄位]
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id VARCHAR(255) PRIMARY KEY,
                display_name VARCHAR(255),
                admin_nickname VARCHAR(255),
                height REAL, profile_weight REAL, age INTEGER, gender VARCHAR(10),
                activity_level REAL, target_calories INTEGER,
                water_goal INTEGER, personal_notes TEXT, 
                last_updated TIMESTAMPTZ,
                exercise_goal INTEGER, 
                exercise_goal_text TEXT,
                capsule_goal INTEGER,
                admin_notes TEXT, status VARCHAR(50),
                expiry_timestamp TIMESTAMPTZ,
                membership_start_date TIMESTAMPTZ,
                service_termination_date TIMESTAMPTZ
            );
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS daily_logs (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL, log_date DATE NOT NULL,
                breakfast_text TEXT, breakfast_kcal INTEGER,
                lunch_text TEXT, lunch_kcal INTEGER,
                dinner_text TEXT, dinner_kcal INTEGER,
                snacks_text TEXT, snacks_kcal INTEGER,
                drinks_text TEXT, drinks_kcal INTEGER,
                water_cc INTEGER,
                exercise_text TEXT, exercise_kcal INTEGER,
                daily_weight REAL,
                capsule_used BOOLEAN, capsule_qty INTEGER,
                UNIQUE(user_id, log_date)
            );
        ''')
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_exercises (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                slot_index INTEGER NOT NULL,
                exercise_name VARCHAR(255),
                kcal INTEGER,
                UNIQUE(user_id, slot_index)
            );
        ''')

        profile_columns_to_check = {
            'admin_nickname': 'VARCHAR(255)',
            'membership_start_date': 'TIMESTAMPTZ',
            'service_termination_date': 'TIMESTAMPTZ',
            'exercise_goal_text': 'TEXT',
            'is_vip': 'BOOLEAN DEFAULT FALSE' # [新增 is_vip 欄位檢查]
        }
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='user_profiles'")
        existing_cols = [row[0] for row in cur.fetchall()]
        for col, data_type in profile_columns_to_check.items():
            if col not in existing_cols:
                cur.execute(f"ALTER TABLE user_profiles ADD COLUMN {col} {data_type};")
                print(f"新增 {col} 欄位至 user_profiles")

        cur.execute("""
            SELECT data_type FROM information_schema.columns 
            WHERE table_name = 'user_profiles' AND column_name = 'last_updated';
        """)
        col_type_info = cur.fetchone()
        if col_type_info and col_type_info[0].lower() == 'date':
            print("正在將 user_profiles.last_updated 欄位型態從 DATE 修改為 TIMESTAMPTZ...")
            cur.execute("ALTER TABLE user_profiles ALTER COLUMN last_updated TYPE TIMESTAMPTZ USING last_updated::timestamp with time zone;")
            print("欄位型態修改完成。")

    conn.commit()
    print("資料庫初始化檢查完成。")

# --- 權限與身份驗證輔助函式 ---
def is_admin(user_id):
    return user_id in ADMIN_USER_IDS

def get_user_status(user_profile):
    """
    根據使用者資料物件判斷其會員狀態。
    返回一個代表狀態的字串。
    """
    if not user_profile:
        return "Trial" 

    # [修改] VIP 狀態優先判斷
    if user_profile.get('is_vip'):
        return "VIP"

    now_utc = datetime.now(pytz.utc)
    term_date = user_profile.get('service_termination_date')
    start_date = user_profile.get('membership_start_date')
    end_date = user_profile.get('expiry_timestamp')

    if term_date and term_date <= now_utc:
        return "Terminated"
    if start_date and start_date > now_utc:
        return "Reserved" # [文字修改] "Attention" -> "Reserved" (預約)
    if end_date and end_date >= now_utc:
        return "Active"
    if end_date and end_date < now_utc:
        return "Expired"
    
    # [新增] 異常狀態判斷: 有結束日但沒有開始日
    if end_date and not start_date:
        return "Abnormal"

    return "Trial"

def check_user_active(user_id):
    """
    檢查使用者是否可用服務，返回 (布林值, 狀態字串)
    """
    if is_admin(user_id):
        return (True, "Admin")

    conn = get_db_connection()
    # [修改] 增加查詢 is_vip
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT membership_start_date, expiry_timestamp, service_termination_date, is_vip FROM user_profiles WHERE user_id = %s", (user_id,))
        user = cur.fetchone()

    status = get_user_status(user)
    is_active = status in ["Admin", "Active", "Trial", "Reserved", "VIP"]
    
    return (is_active, status)

# ******** 【新增的函式】 ********
# 這是在這裡新增的函式，用來產生前端需要的 banner_info 物件
def get_banner_info(user_profile):
    """
    根據使用者資料，產生前端 Banner 需要的文字和顏色 class。
    """
    status = get_user_status(user_profile)
    
    # 預設值
    banner_info = { "text": "會籍狀態不明", "color_class": "bg-gray-200 text-gray-800" }

    if status == "VIP":
        banner_info = { "text": "VIP 會員", "color_class": "bg-yellow-200 text-yellow-800" }
    elif status == "Active":
        days_remaining = None
        if user_profile.get('expiry_timestamp'):
            now_utc = datetime.now(pytz.utc)
            if user_profile['expiry_timestamp'] > now_utc:
                delta = user_profile['expiry_timestamp'] - now_utc
                days_remaining = delta.days
        
        if days_remaining is not None:
                banner_info = { "text": f"會籍有效 (剩 {days_remaining} 天)", "color_class": "bg-green-200 text-green-800" }
        else:
                banner_info = { "text": "會籍有效", "color_class": "bg-green-200 text-green-800" }

    elif status == "Trial":
        banner_info = { "text": "試用體驗中", "color_class": "bg-blue-200 text-blue-800" }
    elif status == "Reserved":
        start_date_str = user_profile.get('membership_start_date').astimezone(TAIPEI_TZ).strftime('%Y/%m/%d')
        banner_info = { "text": f"會籍待啟用 ({start_date_str} 開始)", "color_class": "bg-cyan-200 text-cyan-800" }
    elif status == "Expired":
        banner_info = { "text": "會籍已過期", "color_class": "bg-gray-400 text-gray-800" }
    elif status == "Terminated":
        banner_info = { "text": "服務已中止", "color_class": "bg-red-200 text-red-800" }
    elif status == "Abnormal":
        banner_info = { "text": "會籍狀態異常", "color_class": "bg-orange-200 text-orange-800" }
        
    return banner_info
# ******** 【新增的函式結束】 ********


# --- 頁面路由 ---
@app.route('/liff')
def liff_page():
    return render_template('liff.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')


# --- Helper function ---
def to_int_or_none(value):
    if value == '' or value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None

def to_float_or_none(value):
    if value == '' or value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# --- API 端點 ---
@app.route('/api/check_status', methods=['GET'])
def check_status():
    user_id = request.args.get('userId')
    if not user_id:
        return jsonify({"error": "userId is required"}), 400
    is_active, status = check_user_active(user_id)
    return jsonify({"isActive": is_active, "status": status})

@app.route('/api/profile', methods=['GET', 'POST'])
def handle_profile():
    user_id = request.args.get('userId')
    if not user_id: return jsonify({"error": "userId is required"}), 400
    
    is_active, status = check_user_active(user_id)
    operator_id = request.headers.get('X-Operator-User-Id')
    
    if request.method == 'GET' and not is_active and not is_admin(operator_id):
        return jsonify({"error": "Access denied. Your subscription is inactive.", "status": status}), 403

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if request.method == 'POST':
            data = request.json['data']
            now_utc = datetime.now(pytz.utc)
            
            cur.execute('''
                INSERT INTO user_profiles (user_id, display_name, height, profile_weight, age, gender, activity_level, target_calories, water_goal, exercise_goal, exercise_goal_text, capsule_goal, personal_notes, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    display_name = COALESCE(EXCLUDED.display_name, user_profiles.display_name), 
                    height = EXCLUDED.height, profile_weight = EXCLUDED.profile_weight, age = EXCLUDED.age,
                    gender = EXCLUDED.gender, activity_level = EXCLUDED.activity_level,
                    target_calories = EXCLUDED.target_calories, water_goal = EXCLUDED.water_goal,
                    exercise_goal = EXCLUDED.exercise_goal, 
                    exercise_goal_text = EXCLUDED.exercise_goal_text,
                    capsule_goal = EXCLUDED.capsule_goal,
                    personal_notes = EXCLUDED.personal_notes, 
                    last_updated = EXCLUDED.last_updated
            ''', (
                user_id, data.get('displayName'), to_float_or_none(data.get('height')),
                to_float_or_none(data.get('weight')), to_int_or_none(data.get('age')),
                data.get('gender'), to_float_or_none(data.get('activityLevel')),
                to_int_or_none(data.get('targetCalories')), to_int_or_none(data.get('waterGoal')),
                to_int_or_none(data.get('exerciseGoal')),
                data.get('exerciseGoalText'),
                to_int_or_none(data.get('capsuleGoal')),
                data.get('personalNotes'), 
                now_utc
            ))
            conn.commit()
            return jsonify({'status': 'success', 'message': 'Profile saved.'})

        if request.method == 'GET':
            cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
            profile = cur.fetchone()

            if not profile:
                print(f"新使用者，ID: {user_id}，正在建立預設試用期...")
                default_trial_end_date = datetime.now(pytz.utc) + timedelta(days=7)
                cur.execute(
                    "INSERT INTO user_profiles (user_id, service_termination_date) VALUES (%s, %s)",
                    (user_id, default_trial_end_date)
                )
                conn.commit()
                print(f"使用者 {user_id} 已建立，服務終止日為 {default_trial_end_date}")
                cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
                profile = cur.fetchone()

            if profile:
                profile_dict = dict(profile)
                
                # ******** 【修改點】 ********
                # 將產生 banner_info 的程式碼加在這裡
                profile_dict['banner_info'] = get_banner_info(profile)
                # ******** 【修改點結束】 ********

                date_fields = ['expiry_timestamp', 'membership_start_date', 'service_termination_date', 'last_updated']
                for field in date_fields:
                    if profile_dict.get(field):
                        profile_dict[field] = profile_dict[field].astimezone(TAIPEI_TZ).isoformat()
                
                _is_active, status_string = check_user_active(user_id)
                profile_dict['computed_status'] = status_string # [修改] 使用 computed_status 

                profile_dict['days_remaining'] = None
                if profile.get('expiry_timestamp'):
                    now_utc = datetime.now(pytz.utc)
                    if profile['expiry_timestamp'] > now_utc:
                        delta = profile['expiry_timestamp'] - now_utc
                        profile_dict['days_remaining'] = delta.days

                return jsonify(profile_dict)
            
            return jsonify({})

@app.route('/api/log', methods=['GET', 'POST'])
def handle_log():
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        operator_id = request.headers.get('X-Operator-User-Id')
        if not operator_id: return jsonify({"error": "X-Operator-User-Id header is required"}), 400
        
        is_active, status = check_user_active(operator_id)
        if not is_active:
            return jsonify({"error": "Access denied. Your subscription may have expired.", "status": status}), 403

        if request.method == 'POST':
            req_data = request.json
            log_date = req_data.get('date')
            log_data = req_data.get('data')
            target_user_id = req_data.get('targetUserId', operator_id) 

            fields = ['breakfast_text', 'breakfast_kcal', 'lunch_text', 'lunch_kcal', 'dinner_text', 'dinner_kcal', 'snacks_text', 'snacks_kcal', 'drinks_text', 'drinks_kcal', 'water_cc', 'exercise_text', 'exercise_kcal', 'daily_weight', 'capsule_qty']
            update_clause = ", ".join([f"{field} = EXCLUDED.{field}" for field in fields])
            cur.execute(f'''
                INSERT INTO daily_logs (user_id, log_date, {", ".join(fields)})
                VALUES (%s, %s, {", ".join(["%s"]*len(fields))})
                ON CONFLICT (user_id, log_date) DO UPDATE SET {update_clause}
            ''', tuple([target_user_id, log_date] + [log_data.get(field) for field in fields]))
            conn.commit()
            return jsonify({'status': 'success'})

        if request.method == 'GET':
            user_id = request.args.get('userId')
            log_date_str = request.args.get('date')
            
            cur.execute('SELECT * FROM daily_logs WHERE user_id = %s AND log_date = %s', (user_id, log_date_str))
            log_entry = cur.fetchone()
            
            try:
                current_date = datetime.strptime(log_date_str, '%Y-%m-%d').date()
                previous_date = current_date - timedelta(days=1)
                cur.execute('SELECT daily_weight FROM daily_logs WHERE user_id = %s AND log_date = %s', (user_id, previous_date))
                prev_day_log = cur.fetchone()
                previous_day_weight = prev_day_log['daily_weight'] if prev_day_log and prev_day_log['daily_weight'] is not None else None
            except (ValueError, TypeError):
                previous_day_weight = None

            response_data = {
                "log_data": dict(log_entry) if log_entry else None,
                "previous_day_weight": previous_day_weight
            }
            return jsonify(response_data)

@app.route('/api/user-exercises', methods=['GET', 'POST'])
def handle_user_exercises():
    operator_id = request.headers.get('X-Operator-User-Id')
    if not operator_id:
        return jsonify({"error": "X-Operator-User-Id header is required"}), 400
    
    is_active, status = check_user_active(operator_id)
    if not is_active:
        return jsonify({"error": "Access denied.", "status": status}), 403

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if request.method == 'GET':
            user_id = request.args.get('userId')
            if not user_id:
                return jsonify({"error": "userId is required"}), 400
            
            cur.execute('SELECT slot_index, exercise_name, kcal FROM user_exercises WHERE user_id = %s ORDER BY slot_index ASC', (user_id,))
            exercises = cur.fetchall()
            return jsonify([dict(row) for row in exercises])

        if request.method == 'POST':
            data = request.json
            user_id = data.get('userId')
            exercises = data.get('exercises')

            if not user_id or not isinstance(exercises, list) or len(exercises) != 3:
                return jsonify({"error": "Invalid payload. Required: userId and a list of 3 exercises."}), 400

            for i, ex in enumerate(exercises):
                slot_index = i + 1
                name = ex.get('name')
                kcal = to_int_or_none(ex.get('kcal'))
                
                cur.execute('''
                    INSERT INTO user_exercises (user_id, slot_index, exercise_name, kcal)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, slot_index) DO UPDATE SET
                        exercise_name = EXCLUDED.exercise_name,
                        kcal = EXCLUDED.kcal
                ''', (user_id, slot_index, name, kcal))
            
            conn.commit()
            return jsonify({"status": "success", "message": "User exercises saved."})
    
    return jsonify({"error": "Method not allowed"}), 405


@app.route('/api/completion_dots', methods=['GET'])
def get_completion_dots():
    user_id = request.args.get('userId')
    is_active, status = check_user_active(user_id)
    if not is_active: return jsonify({"error": "Access denied.", "status": status}), 403
    
    year = request.args.get('year'); month = request.args.get('month')
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('''SELECT DISTINCT EXTRACT(DAY FROM log_date) FROM daily_logs WHERE user_id = %s AND EXTRACT(YEAR FROM log_date) = %s AND EXTRACT(MONTH FROM log_date) = %s AND (breakfast_kcal IS NOT NULL OR lunch_kcal IS NOT NULL OR dinner_kcal IS NOT NULL OR snacks_kcal IS NOT NULL OR drinks_kcal IS NOT NULL)''', (user_id, year, month))
        days_with_logs = [int(item[0]) for item in cur.fetchall()]
    return jsonify(days_with_logs)

@app.route('/api/trends', methods=['GET'])
def get_trends():
    user_id = request.args.get('userId')
    is_active, status = check_user_active(user_id)
    if not is_active: return jsonify({"error": "Access denied.", "status": status}), 403

    # ===== ▼▼▼ START: 此函式為本次唯一修改處 ▼▼▼ =====
    
    today = datetime.now(TAIPEI_TZ).date()
    
    # 優先處理新的自訂起訖日期參數
    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')
    
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            # 為避免惡意查詢過大範圍，可以加上一個範圍限制，例如最多查詢365天
            if (end_date - start_date).days > 365:
                 end_date = start_date + timedelta(days=365)
        except ValueError:
            # 如果日期格式錯誤，就退回預設值 (最近7天)
            start_date = today - timedelta(days=6)
            end_date = today
    else:
        # 如果沒有收到起訖日，則沿用舊的 range 邏輯 (向下相容)
        range_param = request.args.get('range', '7days')
        end_date = today

        if range_param == '14days':
            start_date = today - timedelta(days=13)
        elif range_param == '28days':
            start_date = today - timedelta(days=27)
        else:
            try:
                # 處理單一日期或 'membership_period' 的情況
                start_date = datetime.strptime(range_param, '%Y-%m-%d').date()
                potential_end_date = start_date + timedelta(days=27)
                end_date = min(potential_end_date, today)
            except ValueError:
                # 預設為 '7days'
                start_date = today - timedelta(days=6)
    
    # ===== ▲▲▲ END: 此函式為本次唯一修改處 ▲▲▲ =====

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('''
            WITH PeriodLogs AS (
                SELECT 
                    log_date, 
                    daily_weight, 
                    (COALESCE(breakfast_kcal,0) + COALESCE(lunch_kcal,0) + COALESCE(dinner_kcal,0) + COALESCE(snacks_kcal,0) + COALESCE(drinks_kcal,0)) as calories, 
                    COALESCE(water_cc, 0) as water_cc, 
                    COALESCE(exercise_kcal, 0) as exercise_kcal, 
                    COALESCE(capsule_qty, 0) as capsule_qty
                FROM daily_logs 
                WHERE user_id = %s AND log_date BETWEEN %s AND %s
            )
            SELECT 
                (SELECT json_agg(t) FROM PeriodLogs t) as logs,
                (SELECT AVG(daily_weight) FROM PeriodLogs WHERE daily_weight IS NOT NULL AND daily_weight > 0) as avg_weight,
                (SELECT AVG(calories) FROM PeriodLogs) as avg_calories,
                (SELECT AVG(water_cc) FROM PeriodLogs) as avg_water,
                (SELECT AVG(capsule_qty) FROM PeriodLogs) as avg_capsule
        ''', (user_id, start_date, end_date))
        result = cur.fetchone()
    
    logs = result['logs'] if result and result['logs'] else []
    
    averages = {
        "weight": result['avg_weight'] if result else None,
        "calories": result['avg_calories'] if result else None,
        "water": result['avg_water'] if result else None,
        "capsule": result['avg_capsule'] if result else None,
    }

    total_days = (end_date - start_date).days + 1
    labels = [(start_date + timedelta(days=i)).strftime('%-m/%-d') for i in range(total_days)]
    
    # [BUG FIX] 直接使用從 JSON 來的日期字串當 key
    logs_dict = {log['log_date']: log for log in logs}
    
    trend_data = {
        'weight': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('daily_weight') for i in range(total_days)],
        'calories': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {'calories': 0}).get('calories') for i in range(total_days)],
        'water': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {'water_cc': 0}).get('water_cc') for i in range(total_days)],
        'exercise': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {'exercise_kcal': 0}).get('exercise_kcal') for i in range(total_days)],
        'capsule': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {'capsule_qty': 0}).get('capsule_qty') for i in range(total_days)]
    }

    return jsonify({ 'labels': labels, **trend_data, 'averages': averages })


# --- 管理員專用 API ---
@app.route('/api/admin/check', methods=['GET'])
def admin_check():
    user_id = request.args.get('userId')
    if not user_id: return jsonify({"error": "userId is required"}), 400
    return jsonify({"isAdmin": is_admin(user_id)})

@app.route('/api/admin/users', methods=['GET'])
def get_all_users():
    operator_id = request.headers.get('X-Operator-User-Id')
    if not is_admin(operator_id): return jsonify({"error": "Permission denied"}), 403
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        # [修改] 增加查詢 is_vip
        cur.execute('SELECT user_id, display_name, admin_nickname, admin_notes, expiry_timestamp, membership_start_date, service_termination_date, last_updated, is_vip FROM user_profiles ORDER BY last_updated DESC NULLS LAST')
        users = []
        for row in cur.fetchall():
            user = dict(row)
            
            user['computed_status'] = get_user_status(user)
            
            date_fields_to_format = ['expiry_timestamp', 'membership_start_date', 'service_termination_date', 'last_updated']
            for field in date_fields_to_format:
                if user.get(field):
                    user[field] = user[field].astimezone(TAIPEI_TZ).isoformat()

            users.append(user)
    return jsonify(users)

@app.route('/api/admin/profile', methods=['POST'])
def update_admin_profile():
    operator_id = request.headers.get('X-Operator-User-Id')
    if not is_admin(operator_id): return jsonify({"error": "Permission denied"}), 403
    data = request.json
    target_user_id = data.get('userId')
    if not target_user_id: return jsonify({"error": "Target userId is required"}), 400
    
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if 'adminNotes' in data:
            cur.execute('UPDATE user_profiles SET admin_notes = %s WHERE user_id = %s', (data['adminNotes'], target_user_id))
        if 'adminNickname' in data:
            cur.execute('UPDATE user_profiles SET admin_nickname = %s WHERE user_id = %s', (data['adminNickname'], target_user_id))
        
        # [新增] 處理 VIP 狀態
        if 'setVip' in data:
            is_vip = data.get('setVip')
            cur.execute('UPDATE user_profiles SET is_vip = %s WHERE user_id = %s', (is_vip, target_user_id))
            if is_vip:
                print(f"使用者 {target_user_id} 已被設為 VIP。")
            else:
                print(f"使用者 {target_user_id} 的 VIP 資格已被取消。")


        date_fields = {
            'membershipStartDate': 'membership_start_date',
            'expiryTimestamp': 'expiry_timestamp',
            'serviceTerminationDate': 'service_termination_date'
        }
        for key, column in date_fields.items():
            if key in data:
                try:
                    date_val = datetime.fromisoformat(data[key]).astimezone(pytz.utc) if data[key] else None
                    cur.execute(f'UPDATE user_profiles SET {column} = %s WHERE user_id = %s', (date_val, target_user_id))
                except (ValueError, KeyError):
                    return jsonify({"error": f"Invalid format for {key}"}), 400

        if data.get('terminate') is True:
            past_time = datetime.now(pytz.utc) - timedelta(minutes=1)
            cur.execute('UPDATE user_profiles SET service_termination_date = %s WHERE user_id = %s', (past_time, target_user_id))
            print(f"使用者 {target_user_id} 已被管理員終止服務。")

        conn.commit()
        
        # [修改] 增加查詢 is_vip
        cur.execute('SELECT user_id, display_name, admin_nickname, admin_notes, expiry_timestamp, membership_start_date, service_termination_date, is_vip FROM user_profiles WHERE user_id = %s', (target_user_id,))
        updated_user_raw = cur.fetchone()
        updated_user = dict(updated_user_raw) if updated_user_raw else {}
        
        if updated_user:
            updated_user['computed_status'] = get_user_status(updated_user)

            date_fields_to_format_return = ['expiry_timestamp', 'membership_start_date', 'service_termination_date']
            for field in date_fields_to_format_return:
                if updated_user.get(field):
                    updated_user[field] = updated_user[field].astimezone(TAIPEI_TZ).isoformat()
    
    return jsonify({"status": "success", "user": updated_user})

# [新增] 刪除使用者的 API Endpoint
@app.route('/api/admin/user/<string:user_id>', methods=['DELETE'])
def delete_user(user_id):
    operator_id = request.headers.get('X-Operator-User-Id')
    if not is_admin(operator_id):
        return jsonify({"error": "Permission denied"}), 403

    conn = get_db_connection()
    with conn.cursor() as cur:
        try:
            # 確保先刪除有參考 user_id 的資料表紀錄
            cur.execute("DELETE FROM daily_logs WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM user_exercises WHERE user_id = %s", (user_id,))
            
            # 最後再刪除 user_profiles 中的主紀錄
            cur.execute("DELETE FROM user_profiles WHERE user_id = %s", (user_id,))
            
            conn.commit()
            print(f"使用者 {user_id} 的所有資料已被管理員 {operator_id} 成功刪除。")
            return jsonify({"status": "success", "message": "User deleted successfully."})

        except Exception as e:
            conn.rollback() # 如果中途出錯，復原所有操作
            print(f"刪除使用者 {user_id} 時發生錯誤: {e}")
            return jsonify({"error": "Database error during deletion."}), 500


# --- 啟動伺服器 ---
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
