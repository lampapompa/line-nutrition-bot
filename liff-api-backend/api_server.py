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
        # [MODIFIED] 更新 user_profiles 表格結構
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id VARCHAR(255) PRIMARY KEY,
                display_name VARCHAR(255),
                admin_nickname VARCHAR(255),
                height REAL, profile_weight REAL, age INTEGER, gender VARCHAR(10),
                activity_level REAL, target_calories INTEGER,
                water_goal INTEGER, personal_notes TEXT, 
                last_updated TIMESTAMPTZ, -- [MODIFIED] 修改為精確時間
                exercise_goal INTEGER, 
                exercise_goal_text TEXT, -- [NEW] 新增運動目標文字描述
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
        
        # [NEW] 新增：建立使用者自訂運動資料表
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

        # [MODIFIED] 檢查並新增所有需要的欄位
        profile_columns_to_check = {
            'admin_nickname': 'VARCHAR(255)',
            'membership_start_date': 'TIMESTAMPTZ',
            'service_termination_date': 'TIMESTAMPTZ',
            'exercise_goal_text': 'TEXT' # [NEW] 加入新欄位檢查
        }
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='user_profiles'")
        existing_cols = [row[0] for row in cur.fetchall()]
        for col, data_type in profile_columns_to_check.items():
            if col not in existing_cols:
                cur.execute(f"ALTER TABLE user_profiles ADD COLUMN {col} {data_type};")
                print(f"新增 {col} 欄位至 user_profiles")

        # [NEW] 檢查並升級 last_updated 欄位的資料型態
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

# [MODIFIED] 重寫會員狀態判斷邏輯
def check_user_active(user_id):
    if is_admin(user_id):
        return (True, "Admin")

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        # 查詢所有相關日期欄位
        cur.execute("SELECT membership_start_date, expiry_timestamp, service_termination_date FROM user_profiles WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
    conn.close()

    # 如果用戶不存在於資料庫，視為預設試用期
    if not user:
        return (True, "Trial")

    now_utc = datetime.now(pytz.utc)

    # 1. 最高優先級：檢查服務是否已被手動終止
    if user['service_termination_date'] and user['service_termination_date'] <= now_utc:
        return (False, "Terminated")

    # 2. 檢查是否為有效付費會員 (只看迄日)
    if user['expiry_timestamp'] and user['expiry_timestamp'] >= now_utc:
        return (True, "Active")
        
    # 3. 如果以上條件都不滿足，則視為試用期
    # (此處包含新用戶、迄日為空、或迄日已過期的情況，他們都算試用)
    return (True, "Trial")


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
        return jsonify({"error": "Access denied. Your subscription has been terminated.", "status": status}), 403

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
                default_termination_date = datetime.now(pytz.utc) + timedelta(days=7)
                cur.execute(
                    "INSERT INTO user_profiles (user_id, service_termination_date) VALUES (%s, %s)",
                    (user_id, default_termination_date)
                )
                conn.commit()
                print(f"使用者 {user_id} 已建立，服務終止日為 {default_termination_date}")
                cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
                profile = cur.fetchone()

            if profile:
                profile_dict = dict(profile)
                
                date_fields = ['expiry_timestamp', 'membership_start_date', 'service_termination_date', 'last_updated']
                for field in date_fields:
                    if profile_dict.get(field):
                        profile_dict[field] = profile_dict[field].astimezone(TAIPEI_TZ).isoformat()
                
                # [MODIFIED] 確保 liff.html 能拿到正確且同步的 status
                _is_active, status_string = check_user_active(user_id)
                profile_dict['status'] = status_string

                # [NEW] 新增：計算並回傳剩餘天數
                profile_dict['days_remaining'] = None
                if profile.get('expiry_timestamp'):
                    now_utc = datetime.now(pytz.utc)
                    if profile['expiry_timestamp'] > now_utc:
                        delta = profile['expiry_timestamp'] - now_utc
                        profile_dict['days_remaining'] = delta.days

                return jsonify(profile_dict)
            
            return jsonify({})
    conn.close()


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
    conn.close()

# [NEW] 新增：個人化運動資料庫 API
@app.route('/api/user-exercises', methods=['GET', 'POST'])
def handle_user_exercises():
    # 權限驗證
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

            # 遍歷並儲存三組運動
            for i, ex in enumerate(exercises):
                slot_index = i + 1
                # 伺服器端基本驗證
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
    is_active, status = check_user_active(user_id)
    if not is_active: return jsonify({"error": "Access denied.", "status": status}), 403
    
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
    is_active, status = check_user_active(user_id)
    if not is_active: return jsonify({"error": "Access denied.", "status": status}), 403

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
        # [MODIFIED] 新增查詢 capsule_qty 欄位給圖表使用
        cur.execute('''
            SELECT log_date, daily_weight, 
                   COALESCE(breakfast_kcal,0) + COALESCE(lunch_kcal,0) + COALESCE(dinner_kcal,0) + COALESCE(snacks_kcal,0) + COALESCE(drinks_kcal,0) as calories, 
                   water_cc, exercise_kcal, capsule_qty
            FROM daily_logs 
            WHERE user_id = %s AND log_date BETWEEN %s AND %s 
            ORDER BY log_date ASC
        ''', (user_id, start_date, end_date))
        logs = cur.fetchall()
    conn.close()

    total_days = (end_date - start_date).days + 1
    labels = [(start_date + timedelta(days=i)).strftime('%-m/%-d') for i in range(total_days)]
    logs_dict = {log['log_date'].strftime('%Y-%m-%d'): log for log in logs}
    
    trend_data = {
        'weight': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('daily_weight') for i in range(total_days)],
        'calories': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('calories') for i in range(total_days)],
        'water': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('water_cc') for i in range(total_days)],
        'exercise': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('exercise_kcal') for i in range(total_days)],
        'capsule': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('capsule_qty') for i in range(total_days)]
    }
    return jsonify({ 'labels': labels, **trend_data })


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
        cur.execute('SELECT user_id, display_name, admin_nickname, admin_notes, expiry_timestamp, membership_start_date, service_termination_date, last_updated FROM user_profiles ORDER BY last_updated DESC NULLS LAST')
        users = []
        now_utc = datetime.now(pytz.utc)
        for row in cur.fetchall():
            user = dict(row)
            
            term_date = user.get('service_termination_date')
            start_date = user.get('membership_start_date')
            end_date = user.get('expiry_timestamp')

            # [MODIFIED] 修正並優化後台狀態判斷邏輯，確保優先級正確
            if term_date and term_date <= now_utc:
                user['computed_status'] = 'Terminated'
            elif start_date and start_date > now_utc:
                user['computed_status'] = 'Attention' # 優先判斷未來會員
            elif end_date and end_date >= now_utc:
                user['computed_status'] = 'Active'
            else:
                user['computed_status'] = 'Trial'
            
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
        
        cur.execute('SELECT user_id, display_name, admin_nickname, admin_notes, expiry_timestamp, membership_start_date, service_termination_date FROM user_profiles WHERE user_id = %s', (target_user_id,))
        updated_user_raw = cur.fetchone()
        updated_user = dict(updated_user_raw) if updated_user_raw else {}
        
        if updated_user:
            now_utc_for_status = datetime.now(pytz.utc)
            term_date = updated_user.get('service_termination_date')
            start_date = updated_user.get('membership_start_date')
            end_date = updated_user.get('expiry_timestamp')
            
            # [MODIFIED] 同步更新此處的狀態判斷邏輯，確保與會員列表一致
            if term_date and term_date <= now_utc_for_status:
                updated_user['computed_status'] = 'Terminated'
            elif start_date and start_date > now_utc_for_status:
                updated_user['computed_status'] = 'Attention'
            elif end_date and end_date >= now_utc_for_status:
                updated_user['computed_status'] = 'Active'
            else:
                updated_user['computed_status'] = 'Trial'

            date_fields_to_format_return = ['expiry_timestamp', 'membership_start_date', 'service_termination_date']
            for field in date_fields_to_format_return:
                if updated_user.get(field):
                    updated_user[field] = updated_user[field].astimezone(TAIPEI_TZ).isoformat()
    
    conn.close()
    return jsonify({"status": "success", "user": updated_user})


# --- 啟動伺服器 ---
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
