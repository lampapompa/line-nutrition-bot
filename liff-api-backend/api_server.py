import os
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from datetime import datetime, timedelta, date
import pytz

# --- 初始化設定 ---
app = Flask(__name__)
CORS(app)
TAIPEI_TZ = pytz.timezone('Asia/Taipei')

DATABASE_URL = os.environ.get('DATABASE_URL')
ADMIN_USER_IDS_str = os.environ.get('ADMIN_USER_IDS', '')
ADMIN_USER_IDS = [uid.strip() for uid in ADMIN_USER_IDS_str.split(',') if uid.strip()]

# --- 資料庫輔助函式 ---
def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    print("正在檢查並初始化資料庫...")
    conn = get_db_connection()
    with conn.cursor() as cur:
        # [MODIFIED] 在 user_profiles 表中新增 is_vip 欄位
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
                service_termination_date TIMESTAMPTZ,
                is_vip BOOLEAN DEFAULT FALSE
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

        # [MODIFIED] 將 is_vip 加入欄位檢查列表
        profile_columns_to_check = {
            'admin_nickname': 'VARCHAR(255)',
            'membership_start_date': 'TIMESTAMPTZ',
            'service_termination_date': 'TIMESTAMPTZ',
            'exercise_goal_text': 'TEXT',
            'is_vip': 'BOOLEAN DEFAULT FALSE'
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
    conn.close()
    print("資料庫初始化檢查完成。")

# --- 權限與身份驗證輔助函式 ---
def is_admin(user_id):
    return user_id in ADMIN_USER_IDS

# [REMOVED] 舊的狀態判斷邏輯，將被新的 get_user_status_info 取代
# def get_user_status(user_profile): ...
# def check_user_active(user_id): ...

# [ADDED] 全新的、統一的會員狀態判斷核心函式
def get_user_status_info(user_profile):
    """
    根據使用者資料物件判斷其會員狀態，並產生對應的顯示資訊。
    返回一個包含 (is_active, status_code, banner_info) 的字典。
    """
    now_utc = datetime.now(pytz.utc)
    
    # 預設的 banner 資訊
    banner_info = {
        "text": "會籍狀態未知",
        "color_class": "bg-gray-100 text-gray-800"
    }

    # 優先級 0: VIP 檢查 (如果我們決定採用方案B)
    # 這裡我們預設採用方案B，如果不需要，可以移除這段
    if user_profile and user_profile.get('is_vip'):
        banner_info = {"text": "VIP 尊榮會員", "color_class": "bg-green-100 text-green-800"}
        return {"is_active": True, "status_code": "VIP", "banner_info": banner_info}

    # 如果沒有 profile 紀錄，視為新來的試用者
    if not user_profile:
        remaining_time = timedelta(hours=12)
        remaining_hours = int(remaining_time.total_seconds() / 3600)
        banner_info = {"text": f"體驗中｜剩下 {remaining_hours} 小時...", "color_class": "bg-yellow-100 text-yellow-800"}
        return {"is_active": True, "status_code": "Trial", "banner_info": banner_info}

    term_date = user_profile.get('service_termination_date')
    start_date = user_profile.get('membership_start_date')
    end_date = user_profile.get('expiry_timestamp')

    # 優先級 1: 中止 (Terminated)
    if term_date and now_utc >= term_date:
        banner_info = {"text": "服務已中止", "color_class": "bg-red-100 text-red-800"}
        return {"is_active": False, "status_code": "Terminated", "banner_info": banner_info}

    # 優先級 2: 異常 (Abnormal)
    if (end_date and start_date and end_date < start_date) or \
       (term_date and end_date and term_date < end_date):
        days_remaining = (end_date - now_utc).days if end_date and end_date > now_utc else 0
        banner_info = {"text": f"會籍有效｜剩下 {days_remaining} 天...", "color_class": "bg-green-100 text-green-800"}
        return {"is_active": True, "status_code": "Abnormal", "banner_info": banner_info}

    # 優先級 3: 預約 (Reserved)
    if start_date and now_utc < start_date:
        start_date_local = start_date.astimezone(TAIPEI_TZ)
        banner_info = {"text": f"會籍已預約｜將於 {start_date_local.strftime('%Y/%m/%d')} 開始", "color_class": "bg-blue-100 text-blue-800"}
        return {"is_active": True, "status_code": "Reserved", "banner_info": banner_info}

    # 優先級 4: 有效 (Active)
    if start_date and end_date and start_date <= now_utc < end_date:
        days_remaining = (end_date - now_utc).days
        banner_info = {"text": f"會籍有效｜剩下 {days_remaining} 天...", "color_class": "bg-green-100 text-green-800"}
        return {"is_active": True, "status_code": "Active", "banner_info": banner_info}
    
    # 優先級 5: 試用 (Trial) - 包含付費到期和新用戶
    # 付費到期變試用
    if end_date and now_utc >= end_date:
        banner_info = {"text": "體驗中", "color_class": "bg-yellow-100 text-yellow-800"}
        return {"is_active": True, "status_code": "Trial", "banner_info": banner_info}
    
    # 新用戶/只有中止日期的試用
    if term_date and now_utc < term_date:
        remaining_time = term_date - now_utc
        remaining_hours = int(remaining_time.total_seconds() / 3600)
        banner_info = {"text": f"體驗中｜剩下 {remaining_hours} 小時...", "color_class": "bg-yellow-100 text-yellow-800"}
        return {"is_active": True, "status_code": "Trial", "banner_info": banner_info}
    
    # 預設狀態，通常是三日期皆為空的用戶
    banner_info = {"text": "體驗中", "color_class": "bg-yellow-100 text-yellow-800"}
    return {"is_active": True, "status_code": "Trial", "banner_info": banner_info}


# --- 頁面路由 ---
@app.route('/liff')
def liff_page():
    return render_template('liff.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')


# --- Helper function to convert empty strings to None ---
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
@app.route('/api/profile', methods=['GET', 'POST'])
def handle_profile():
    user_id = request.args.get('userId')
    if not user_id: return jsonify({"error": "userId is required"}), 400
    
    operator_id = request.headers.get('X-Operator-User-Id')
    
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if request.method == 'GET':
            cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
            profile = cur.fetchone()

            # 統一的權限與狀態檢查
            if is_admin(operator_id):
                status_info = get_user_status_info(profile)
                status_info["is_active"] = True # 管理員永遠可以讀取資料
            else:
                status_info = get_user_status_info(profile)

            if not status_info["is_active"]:
                 return jsonify({"error": "Access denied.", "status": status_info["status_code"]}), 403

            if not profile:
                print(f"新使用者，ID: {user_id}，正在建立預設試用期...")
                # [MODIFIED] 新用戶試用期改為 12 小時
                default_trial_end_date = datetime.now(pytz.utc) + timedelta(hours=12)
                cur.execute(
                    "INSERT INTO user_profiles (user_id, service_termination_date) VALUES (%s, %s)",
                    (user_id, default_trial_end_date)
                )
                conn.commit()
                print(f"使用者 {user_id} 已建立，服務終止日為 {default_trial_end_date}")
                cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
                profile = cur.fetchone()

            profile_dict = dict(profile)
            
            date_fields = ['expiry_timestamp', 'membership_start_date', 'service_termination_date', 'last_updated']
            for field in date_fields:
                if profile_dict.get(field):
                    profile_dict[field] = profile_dict[field].astimezone(TAIPEI_TZ).isoformat()
            
            # [MODIFIED] 將後端算好的狀態碼和 banner 資訊加入回傳
            profile_dict['status'] = status_info["status_code"]
            profile_dict['banner_info'] = status_info["banner_info"]
            
            return jsonify(profile_dict)

        if request.method == 'POST':
            # POST 請求也需要權限檢查
            cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
            profile = cur.fetchone()
            status_info = get_user_status_info(profile)
            if not status_info["is_active"] and not is_admin(operator_id):
                 return jsonify({"error": "Access denied.", "status": status_info["status_code"]}), 403

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

    conn.close()


@app.route('/api/log', methods=['GET', 'POST'])
def handle_log():
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        operator_id = request.headers.get('X-Operator-User-Id')
        if not operator_id: return jsonify({"error": "X-Operator-User-Id header is required"}), 400
        
        cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (operator_id,))
        profile = cur.fetchone()
        status_info = get_user_status_info(profile)
        if not status_info["is_active"] and not is_admin(operator_id):
            return jsonify({"error": "Access denied.", "status": status_info["status_code"]}), 403

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
    conn.close()

@app.route('/api/user-exercises', methods=['GET', 'POST'])
def handle_user_exercises():
    operator_id = request.headers.get('X-Operator-User-Id')
    if not operator_id:
        return jsonify({"error": "X-Operator-User-Id header is required"}), 400
    
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (operator_id,))
        profile = cur.fetchone()
        status_info = get_user_status_info(profile)
        if not status_info["is_active"] and not is_admin(operator_id):
            return jsonify({"error": "Access denied.", "status": status_info["status_code"]}), 403

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
    
    conn.close()
    return jsonify({"error": "Method not allowed"}), 405


@app.route('/api/completion_dots', methods=['GET'])
def get_completion_dots():
    user_id = request.args.get('userId')
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
        profile = cur.fetchone()
    conn.close()
    status_info = get_user_status_info(profile)
    if not status_info["is_active"]: return jsonify({"error": "Access denied.", "status": status_info["status_code"]}), 403
    
    year = request.args.get('year'); month = request.args.get('month')
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('''SELECT DISTINCT EXTRACT(DAY FROM log_date) FROM daily_logs WHERE user_id = %s AND EXTRACT(YEAR FROM log_date) = %s AND EXTRACT(MONTH FROM log_date) = %s AND (breakfast_kcal IS NOT NULL OR lunch_kcal IS NOT NULL OR dinner_kcal IS NOT NULL OR snacks_kcal IS NOT NULL OR drinks_kcal IS NOT NULL)''', (user_id, year, month))
        days_with_logs = [int(item[0]) for item in cur.fetchall()]
    conn.close()
    return jsonify(days_with_logs)

@app.route('/api/trends', methods=['GET'])
def get_trends():
    user_id = request.args.get('userId')
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
        profile = cur.fetchone()
    conn.close()
    status_info = get_user_status_info(profile)
    if not status_info["is_active"]: return jsonify({"error": "Access denied.", "status": status_info["status_code"]}), 403

    range_param = request.args.get('range', '7days')
    today = datetime.now(TAIPEI_TZ).date()
    end_date = today

    if range_param == '14days':
        start_date = today - timedelta(days=13)
    elif range_param == '28days':
        start_date = today - timedelta(days=27)
    else:
        try:
            start_date = datetime.strptime(range_param, '%Y-%m-%d').date()
            potential_end_date = start_date + timedelta(days=27)
            end_date = min(potential_end_date, today)
        except ValueError:
            start_date = today - timedelta(days=6)

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        # [MODIFIED] 優化：SQL查詢現在同時計算平均值，提升效能
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
    conn.close()
    
    logs = result['logs'] if result and result['logs'] else []
    
    # [ADDED] 優化：將計算好的平均值打包起來
    averages = {
        "weight": result['avg_weight'] if result else None,
        "calories": result['avg_calories'] if result else None,
        "water": result['avg_water'] if result else None,
        "capsule": result['avg_capsule'] if result else None,
    }

    total_days = (end_date - start_date).days + 1
    labels = [(start_date + timedelta(days=i)).strftime('%-m/%-d') for i in range(total_days)]
    logs_dict = {log['log_date'].strftime('%Y-%m-%d'): log for log in logs}
    
    trend_data = {
        'weight': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('daily_weight') for i in range(total_days)],
        'calories': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {'calories': 0}).get('calories') for i in range(total_days)],
        'water': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {'water_cc': 0}).get('water_cc') for i in range(total_days)],
        'exercise': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {'exercise_kcal': 0}).get('exercise_kcal') for i in range(total_days)],
        'capsule': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {'capsule_qty': 0}).get('capsule_qty') for i in range(total_days)]
    }

    # [MODIFIED] 優化：將平均值物件加入最終回傳的 JSON 中
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
        cur.execute('SELECT * FROM user_profiles ORDER BY last_updated DESC NULLS LAST')
        users = []
        for row in cur.fetchall():
            user = dict(row)
            
            # [MODIFIED] 優化：呼叫統一的狀態判斷函式，確保邏輯一致
            status_info = get_user_status_info(user)
            user['computed_status'] = status_info["status_code"]
            
            date_fields_to_format = ['expiry_timestamp', 'membership_start_date', 'service_termination_date', 'last_updated']
            for field in date_fields_to_format:
                if user.get(field):
                    user[field] = user[field].astimezone(TAIPEI_TZ).isoformat()

            users.append(user)
    conn.close()
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
        
        # [ADDED] 新增設為 VIP 的邏輯
        if data.get('setVip') is True:
            cur.execute('''
                UPDATE user_profiles 
                SET is_vip = TRUE, 
                    membership_start_date = NULL, 
                    expiry_timestamp = NULL, 
                    service_termination_date = NULL 
                WHERE user_id = %s
            ''', (target_user_id,))
            print(f"管理員 {operator_id} 已將用戶 {target_user_id} 設為 VIP。")
        # [ADDED] 新增取消 VIP 的邏輯
        elif data.get('setVip') is False:
            cur.execute('UPDATE user_profiles SET is_vip = FALSE WHERE user_id = %s', (target_user_id,))
            print(f"管理員 {operator_id} 已取消用戶 {target_user_id} 的 VIP 身份。")


        if 'adminNotes' in data:
            cur.execute('UPDATE user_profiles SET admin_notes = %s WHERE user_id = %s', (data['adminNotes'], target_user_id))
        if 'adminNickname' in data:
            cur.execute('UPDATE user_profiles SET admin_nickname = %s WHERE user_id = %s', (data['adminNickname'], target_user_id))

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
                    conn.close()
                    return jsonify({"error": f"Invalid format for {key}"}), 400

        if data.get('terminate') is True:
            past_time = datetime.now(pytz.utc) - timedelta(minutes=1)
            cur.execute('UPDATE user_profiles SET service_termination_date = %s WHERE user_id = %s', (past_time, target_user_id))
            print(f"使用者 {target_user_id} 已被管理員終止服務。")

        conn.commit()
        
        cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (target_user_id,))
        updated_user_raw = cur.fetchone()
        updated_user = dict(updated_user_raw) if updated_user_raw else {}
        
        if updated_user:
            # [MODIFIED] 優化：呼叫統一的狀態判斷函式，確保邏輯一致
            status_info = get_user_status_info(updated_user)
            updated_user['computed_status'] = status_info["status_code"]

            date_fields_to_format_return = ['expiry_timestamp', 'membership_start_date', 'service_termination_date']
            for field in date_fields_to_format_return:
                if updated_user.get(field):
                    updated_user[field] = updated_user[field].astimezone(TAIPEI_TZ).isoformat()
    
    conn.close()
    return jsonify({"status": "success", "user": updated_user})

# [ADDED] 新增刪除用戶的專用 API
@app.route('/api/admin/user/<target_user_id>', methods=['DELETE'])
def delete_user(target_user_id):
    operator_id = request.headers.get('X-Operator-User-Id')
    if not is_admin(operator_id):
        return jsonify({"error": "Permission denied"}), 403
    
    if not target_user_id:
        return jsonify({"error": "Target user ID is required"}), 400

    conn = get_db_connection()
    try:
        with conn: # 使用 with conn 自動處理交易 (transaction)
            with conn.cursor() as cur:
                # 依序刪除所有相關資料，確保資料庫乾淨
                cur.execute("DELETE FROM daily_logs WHERE user_id = %s", (target_user_id,))
                cur.execute("DELETE FROM user_exercises WHERE user_id = %s", (target_user_id,))
                cur.execute("DELETE FROM user_profiles WHERE user_id = %s", (target_user_id,))
        print(f"管理員 {operator_id} 已成功刪除用戶 {target_user_id} 的所有資料。")
        return jsonify({"status": "success", "message": f"User {target_user_id} deleted successfully."})
    except Exception as e:
        print(f"刪除用戶 {target_user_id} 時發生錯誤: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


# --- 啟動伺服器 ---
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
