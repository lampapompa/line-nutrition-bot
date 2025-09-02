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
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id VARCHAR(255) PRIMARY KEY,
                display_name VARCHAR(255),
                admin_nickname VARCHAR(255),
                height REAL, profile_weight REAL, age INTEGER, gender VARCHAR(10),
                activity_level REAL, target_calories INTEGER,
                water_goal INTEGER, personal_notes TEXT, last_updated DATE,
                exercise_goal INTEGER, capsule_goal INTEGER,
                admin_notes TEXT, status VARCHAR(50),
                expiry_timestamp TIMESTAMPTZ
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
        profile_columns_to_check = { 'admin_nickname': 'VARCHAR(255)' }
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='user_profiles'")
        existing_cols = [row[0] for row in cur.fetchall()]
        for col, data_type in profile_columns_to_check.items():
            if col not in existing_cols:
                cur.execute(f"ALTER TABLE user_profiles ADD COLUMN {col} {data_type};")
                print(f"新增 {col} 欄位至 user_profiles")
    conn.commit()
    conn.close()
    print("資料庫初始化檢查完成。")

# --- 權限與身份驗證輔助函式 ---
def is_admin(user_id):
    return user_id in ADMIN_USER_IDS

def check_user_active(user_id):
    if is_admin(user_id):
        return (True, "Admin")
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT expiry_timestamp FROM user_profiles WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
    conn.close()
    if not result or not result[0]:
        return (False, "Inactive")
    expiry = result[0]
    if expiry > datetime.now(pytz.utc):
        return (True, "Active")
    return (False, "Expired")

# --- 頁面路由 ---
@app.route('/liff')
def liff_page():
    return render_template('liff.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')

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
    
    # 權限檢查
    is_active, status = check_user_active(user_id)
    if request.method == 'GET' and not is_active:
        return jsonify({"error": "Access denied. Your subscription may have expired.", "status": status}), 403

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if request.method == 'POST':
            data = request.json['data']
            today = date.today()
            # 首次儲存或更新時，寫入 display_name
            cur.execute('''
                INSERT INTO user_profiles (user_id, display_name, height, profile_weight, age, gender, activity_level, target_calories, water_goal, exercise_goal, capsule_goal, personal_notes, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    display_name = COALESCE(EXCLUDED.display_name, user_profiles.display_name), 
                    height = EXCLUDED.height, profile_weight = EXCLUDED.profile_weight, age = EXCLUDED.age,
                    gender = EXCLUDED.gender, activity_level = EXCLUDED.activity_level,
                    target_calories = EXCLUDED.target_calories, water_goal = EXCLUDED.water_goal,
                    exercise_goal = EXCLUDED.exercise_goal, capsule_goal = EXCLUDED.capsule_goal,
                    personal_notes = EXCLUDED.personal_notes, last_updated = EXCLUDED.last_updated
            ''', (
                user_id, data.get('displayName'), data.get('height'), data.get('weight'), data.get('age'),
                data.get('gender'), data.get('activityLevel'), data.get('targetCalories'),
                data.get('waterGoal'), data.get('exerciseGoal'), data.get('capsuleGoal'),
                data.get('personalNotes'), today
            ))
            conn.commit()
            return jsonify({'status': 'success', 'message': 'Profile saved.'})

        if request.method == 'GET':
            cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
            profile = cur.fetchone()
            if profile:
                profile_dict = dict(profile)
                if profile_dict.get('last_updated'):
                    profile_dict['last_updated'] = profile_dict['last_updated'].strftime('%Y-%m-%d')
                if profile_dict.get('expiry_timestamp'):
                    profile_dict['expiry_timestamp'] = profile_dict['expiry_timestamp'].astimezone(TAIPEI_TZ).isoformat()
                return jsonify(profile_dict)
            return jsonify({})
    conn.close()

@app.route('/api/log', methods=['GET', 'POST'])
def handle_log():
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        operator_id = request.headers.get('X-Operator-User-Id')
        if not operator_id: return jsonify({"error": "X-Operator-User-Id header is required"}), 400
        
        # 權限檢查
        is_active, status = check_user_active(operator_id)
        if not is_active:
            return jsonify({"error": "Access denied. Your subscription may have expired.", "status": status}), 403

        if request.method == 'POST':
            req_data = request.json
            log_date = req_data.get('date')
            log_data = req_data.get('data')
            target_user_id = req_data.get('targetUserId', operator_id) # 管理員可指定目標

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
            log_date = request.args.get('date')
            cur.execute('SELECT * FROM daily_logs WHERE user_id = %s AND log_date = %s', (user_id, log_date))
            log_entry = cur.fetchone()
            return jsonify(dict(log_entry) if log_entry else None)
    conn.close()

@app.route('/api/completion_dots', methods=['GET'])
def get_completion_dots():
    user_id = request.args.get('userId')
    is_active, status = check_user_active(user_id)
    if not is_active: return jsonify({"error": "Access denied.", "status": status}), 403
    
    # ... (其餘邏輯不變)
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

    # ... (其餘邏輯不變)
    range_param = request.args.get('range', '7days'); today = datetime.now().date()
    if range_param == 'this_month': start_date = today.replace(day=1)
    elif range_param == '28days': start_date = today - timedelta(days=27)
    else:
        try: start_date = datetime.strptime(range_param, '%Y-%m-%d').date()
        except ValueError: start_date = today - timedelta(days=6)
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('''SELECT log_date, daily_weight, COALESCE(breakfast_kcal,0) + COALESCE(lunch_kcal,0) + COALESCE(dinner_kcal,0) + COALESCE(snacks_kcal,0) + COALESCE(drinks_kcal,0) as calories, water_cc, exercise_kcal FROM daily_logs WHERE user_id = %s AND log_date BETWEEN %s AND %s ORDER BY log_date ASC''', (user_id, start_date, today))
        logs = cur.fetchall()
    conn.close()
    labels = [(start_date + timedelta(days=i)).strftime('%-m/%-d') for i in range((today - start_date).days + 1)]
    logs_dict = {log['log_date'].strftime('%Y-%m-%d'): log for log in logs}
    trend_data = {'weight': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('daily_weight') for i in range(len(labels))], 'calories': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('calories') for i in range(len(labels))], 'water': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('water_cc') for i in range(len(labels))], 'exercise': [logs_dict.get((start_date + timedelta(days=i)).strftime('%Y-%m-%d'), {}).get('exercise_kcal') for i in range(len(labels))]}
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
        cur.execute('SELECT user_id, display_name, admin_nickname, admin_notes, expiry_timestamp FROM user_profiles ORDER BY last_updated DESC NULLS LAST')
        users = []
        now_utc = datetime.now(pytz.utc)
        for row in cur.fetchall():
            user = dict(row)
            expiry = user.get('expiry_timestamp')
            if expiry:
                user['expiry_timestamp'] = expiry.astimezone(TAIPEI_TZ).isoformat()
                if expiry > now_utc: user['computed_status'] = 'Active'
                else: user['computed_status'] = 'Expired'
            else: user['computed_status'] = 'Inactive'
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
        if 'expiryAction' in data:
            action = data['expiryAction']
            new_expiry = None
            now_utc = datetime.now(pytz.utc)
            if action == 'clear': new_expiry = None
            elif action.endswith('h'): new_expiry = now_utc + timedelta(hours=int(action[:-1]))
            elif action.endswith('d'): new_expiry = now_utc + timedelta(days=int(action[:-1]))
            elif action == 'custom':
                try: new_expiry = datetime.fromisoformat(data['customExpiry']).astimezone(pytz.utc)
                except (ValueError, KeyError):
                    conn.close()
                    return jsonify({"error": "Invalid customExpiry format"}), 400
            if new_expiry is not None or action == 'clear':
                cur.execute('UPDATE user_profiles SET expiry_timestamp = %s WHERE user_id = %s', (new_expiry, target_user_id))
        conn.commit()
        
        # 返回更新後的完整用戶資料
        cur.execute('SELECT user_id, display_name, admin_nickname, admin_notes, expiry_timestamp FROM user_profiles WHERE user_id = %s', (target_user_id,))
        updated_user_raw = cur.fetchone()
        updated_user = dict(updated_user_raw) if updated_user_raw else {}
        if updated_user:
            expiry = updated_user.get('expiry_timestamp')
            if expiry:
                updated_user['expiry_timestamp'] = expiry.astimezone(TAIPEI_TZ).isoformat()
                if expiry > now_utc: updated_user['computed_status'] = 'Active'
                else: updated_user['computed_status'] = 'Expired'
            else: updated_user['computed_status'] = 'Inactive'
    conn.close()
    return jsonify({"status": "success", "user": updated_user})

# --- 啟動伺服器 ---
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
