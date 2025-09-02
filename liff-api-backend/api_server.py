import os
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, date

# --- 初始化設定 ---
app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get('DATABASE_URL')
# 從環境變數讀取管理員 ID 列表，並去除多餘的空格
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
        # 使用者個人檔案資料表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id VARCHAR(255) PRIMARY KEY,
                display_name VARCHAR(255),
                height REAL, profile_weight REAL, age INTEGER, gender VARCHAR(10),
                activity_level REAL, target_calories INTEGER,
                water_goal INTEGER, personal_notes TEXT, last_updated DATE,
                exercise_goal INTEGER, capsule_goal INTEGER,
                admin_notes TEXT, status VARCHAR(50)
            );
        ''')
        # 每日記錄資料表
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

        # 檢查並新增欄位 (向下相容)
        profile_columns_to_check = {
            'water_goal': 'INTEGER', 'personal_notes': 'TEXT', 'last_updated': 'DATE',
            'exercise_goal': 'INTEGER', 'capsule_goal': 'INTEGER',
            'display_name': 'VARCHAR(255)', 'admin_notes': 'TEXT', 'status': 'VARCHAR(50)' # V3.0 Mini 新增欄位
        }
        for col, data_type in profile_columns_to_check.items():
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='user_profiles' AND column_name=%s", (col,))
            if cur.fetchone() is None:
                cur.execute(f"ALTER TABLE user_profiles ADD COLUMN {col} {data_type};")
                print(f"新增 {col} 欄位至 user_profiles")

        log_columns_to_check = {'drinks_text': 'TEXT', 'drinks_kcal': 'INTEGER'}
        for col, data_type in log_columns_to_check.items():
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='daily_logs' AND column_name=%s", (col,))
            if cur.fetchone() is None:
                cur.execute(f"ALTER TABLE daily_logs ADD COLUMN {col} {data_type};")
                print(f"新增 {col} 欄位至 daily_logs")

    conn.commit()
    conn.close()
    print("資料庫初始化檢查完成。")

# --- 身份驗證輔助函式 ---
def is_admin(user_id):
    return user_id in ADMIN_USER_IDS

# --- API 端點 (Routes) ---

@app.route('/api/profile', methods=['GET', 'POST'])
def handle_profile():
    user_id = request.args.get('userId')
    if not user_id: return jsonify({"error": "userId is required"}), 400

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if request.method == 'POST':
            data = request.json['data']
            today = date.today()
            # 當使用者儲存自己的檔案時，同時更新 display_name
            cur.execute('''
                INSERT INTO user_profiles (user_id, display_name, height, profile_weight, age, gender, activity_level, target_calories, water_goal, exercise_goal, capsule_goal, personal_notes, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name, height = EXCLUDED.height, profile_weight = EXCLUDED.profile_weight, age = EXCLUDED.age,
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
                return jsonify(profile_dict)
            return jsonify({})
    conn.close()

@app.route('/api/log', methods=['GET', 'POST'])
def handle_log():
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if request.method == 'POST':
            req_data = request.json
            user_id = req_data.get('userId')
            log_date = req_data.get('date')
            log_data = req_data.get('data')
            # 安全性檢查：從 Header 取得操作者 ID
            operator_id = request.headers.get('X-Operator-User-Id')

            if not all([user_id, log_date, log_data, operator_id]):
                 return jsonify({"error": "userId, date, data, and X-Operator-User-Id header are required"}), 400

            # 只有本人或管理員可以修改資料
            if operator_id != user_id and not is_admin(operator_id):
                return jsonify({"error": "Permission denied. You can only modify your own data."}), 403

            fields = [
                'breakfast_text', 'breakfast_kcal', 'lunch_text', 'lunch_kcal',
                'dinner_text', 'dinner_kcal', 'snacks_text', 'snacks_kcal',
                'drinks_text', 'drinks_kcal',
                'water_cc', 'exercise_text', 'exercise_kcal', 'daily_weight',
                'capsule_used', 'capsule_qty'
            ]
            update_clause = ", ".join([f"{field} = EXCLUDED.{field}" for field in fields])

            cur.execute(f'''
                INSERT INTO daily_logs (user_id, log_date, {", ".join(fields)})
                VALUES (%s, %s, {", ".join(["%s"]*len(fields))})
                ON CONFLICT (user_id, log_date) DO UPDATE SET {update_clause}
            ''', tuple([user_id, log_date] + [log_data.get(field) for field in fields]))

            conn.commit()
            return jsonify({'status': 'success', 'message': f'Log for {log_date} saved.'})

        if request.method == 'GET':
            user_id = request.args.get('userId')
            log_date = request.args.get('date')
            if not all([user_id, log_date]): return jsonify({"error": "userId and date are required"}), 400
            cur.execute('SELECT * FROM daily_logs WHERE user_id = %s AND log_date = %s', (user_id, log_date))
            log_entry = cur.fetchone()
            return jsonify(dict(log_entry) if log_entry else None)
    conn.close()

@app.route('/api/completion_dots', methods=['GET'])
def get_completion_dots():
    user_id = request.args.get('userId')
    year = request.args.get('year')
    month = request.args.get('month')
    if not all([user_id, year, month]): return jsonify({"error": "userId, year, and month are required"}), 400

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('''
            SELECT DISTINCT EXTRACT(DAY FROM log_date) FROM daily_logs
            WHERE user_id = %s AND EXTRACT(YEAR FROM log_date) = %s AND EXTRACT(MONTH FROM log_date) = %s
            AND (breakfast_kcal IS NOT NULL OR lunch_kcal IS NOT NULL OR dinner_kcal IS NOT NULL OR drinks_kcal IS NOT NULL)
        ''', (user_id, year, month))
        days_with_logs = [int(item[0]) for item in cur.fetchall()]
    conn.close()
    return jsonify(days_with_logs)

@app.route('/api/trends', methods=['GET'])
def get_trends():
    user_id = request.args.get('userId')
    range_param = request.args.get('range', '7days')
    if not user_id: return jsonify({"error": "userId is required"}), 400

    today = datetime.now().date()
    start_date = today - timedelta(days=6) # Default to 7 days

    if range_param == 'this_month':
        start_date = today.replace(day=1)
    elif range_param == '28days':
        start_date = today - timedelta(days=27)
    else: # Handles '7days' or a custom date string like 'YYYY-MM-DD'
        try:
            start_date = datetime.strptime(range_param, '%Y-%m-%d').date()
        except ValueError:
            start_date = today - timedelta(days=6)

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('''
            SELECT log_date, daily_weight,
                   COALESCE(breakfast_kcal,0) + COALESCE(lunch_kcal,0) + COALESCE(dinner_kcal,0) + COALESCE(snacks_kcal,0) + COALESCE(drinks_kcal,0) as calories,
                   water_cc, exercise_kcal
            FROM daily_logs WHERE user_id = %s AND log_date BETWEEN %s AND %s ORDER BY log_date ASC
        ''', (user_id, start_date, today))
        logs = cur.fetchall()
    conn.close()

    labels = []
    current_date_for_labels = start_date
    while current_date_for_labels <= today:
        labels.append(current_date_for_labels.strftime('%-m/%-d'))
        current_date_for_labels += timedelta(days=1)

    logs_dict = {log['log_date'].strftime('%Y-%m-%d'): log for log in logs}
    trend_data = {'weight': [], 'calories': [], 'water': [], 'exercise': []}

    current_date_for_data = start_date
    while current_date_for_data <= today:
        date_str = current_date_for_data.strftime('%Y-%m-%d')
        if date_str in logs_dict:
            log = logs_dict[date_str]
            trend_data['weight'].append(log['daily_weight'])
            trend_data['calories'].append(log['calories'])
            trend_data['water'].append(log['water_cc'])
            trend_data['exercise'].append(log['exercise_kcal'])
        else:
            trend_data['weight'].append(None); trend_data['calories'].append(None);
            trend_data['water'].append(None); trend_data['exercise'].append(None);
        current_date_for_data += timedelta(days=1)

    return jsonify({ 'labels': labels, **trend_data })

# --- 管理員專用 API ---

@app.route('/api/admin/check', methods=['GET'])
def admin_check():
    user_id = request.args.get('userId')
    if not user_id:
        return jsonify({"error": "userId is required"}), 400
    return jsonify({"isAdmin": is_admin(user_id)})

@app.route('/api/admin/users', methods=['GET'])
def get_all_users():
    operator_id = request.headers.get('X-Operator-User-Id')
    if not is_admin(operator_id):
        return jsonify({"error": "Permission denied"}), 403

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('SELECT user_id, display_name, status, last_updated FROM user_profiles ORDER BY last_updated DESC')
        users = [dict(row) for row in cur.fetchall()]
        for user in users:
            if user.get('last_updated'):
                user['last_updated'] = user['last_updated'].strftime('%Y-%m-%d')
    conn.close()
    return jsonify(users)

@app.route('/api/admin/profile', methods=['POST'])
def update_admin_profile():
    operator_id = request.headers.get('X-Operator-User-Id')
    if not is_admin(operator_id):
        return jsonify({"error": "Permission denied"}), 403

    data = request.json
    target_user_id = data.get('userId')
    admin_notes = data.get('adminNotes')
    status = data.get('status')

    if not target_user_id:
        return jsonify({"error": "Target userId is required"}), 400

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('''
            UPDATE user_profiles
            SET admin_notes = %s, status = %s
            WHERE user_id = %s
        ''', (admin_notes, status, target_user_id))
    conn.commit()
    conn.close()

    return jsonify({"status": "success", "message": f"Profile for {target_user_id} updated."})


# --- 啟動伺服器 ---
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
