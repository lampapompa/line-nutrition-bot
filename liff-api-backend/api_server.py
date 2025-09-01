import os
import psycopg2
import psycopg2.extras # 引入 DictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta, date

# --- 初始化設定 ---
app = Flask(__name__)
CORS(app) 

DATABASE_URL = os.environ.get('DATABASE_URL')

# --- 資料庫輔助函式 ---

def get_db_connection():
    """建立並返回一個 PostgreSQL 資料庫連線"""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """初始化資料庫，建立或更新所需的資料表"""
    print("正在檢查並初始化資料庫...")
    conn = get_db_connection()
    with conn.cursor() as cur:
        # 使用者個人檔案資料表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id VARCHAR(255) PRIMARY KEY,
                height REAL,
                profile_weight REAL,
                age INTEGER,
                gender VARCHAR(10),
                activity_level REAL,
                target_calories INTEGER,
                water_goal INTEGER,      -- 新增: 飲水目標
                personal_notes TEXT,   -- 新增: 個人備註
                last_updated DATE      -- 新增: 上次更新日期
            );
        ''')
        # 每日記錄資料表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS daily_logs (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                log_date DATE NOT NULL,
                breakfast_text TEXT, breakfast_kcal INTEGER,
                lunch_text TEXT, lunch_kcal INTEGER,
                dinner_text TEXT, dinner_kcal INTEGER,
                snacks_text TEXT, snacks_kcal INTEGER,
                water_cc INTEGER,
                exercise_text TEXT, exercise_kcal INTEGER,
                daily_weight REAL,
                capsule_used BOOLEAN, capsule_qty INTEGER,
                UNIQUE(user_id, log_date)
            );
        ''')
        # 檢查並新增欄位 (為了向下相容)
        # 檢查 user_profiles 是否有 water_goal 欄位
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='user_profiles' AND column_name='water_goal'")
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE user_profiles ADD COLUMN water_goal INTEGER;")
            print("新增 water_goal 欄位至 user_profiles")
        
        # 檢查 user_profiles 是否有 personal_notes 欄位
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='user_profiles' AND column_name='personal_notes'")
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE user_profiles ADD COLUMN personal_notes TEXT;")
            print("新增 personal_notes 欄位至 user_profiles")

        # 檢查 user_profiles 是否有 last_updated 欄位
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='user_profiles' AND column_name='last_updated'")
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE user_profiles ADD COLUMN last_updated DATE;")
            print("新增 last_updated 欄位至 user_profiles")

    conn.commit()
    conn.close()
    print("資料庫初始化檢查完成。")


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
            cur.execute('''
                INSERT INTO user_profiles (user_id, height, profile_weight, age, gender, activity_level, target_calories, water_goal, personal_notes, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    height = EXCLUDED.height, profile_weight = EXCLUDED.profile_weight, age = EXCLUDED.age,
                    gender = EXCLUDED.gender, activity_level = EXCLUDED.activity_level, 
                    target_calories = EXCLUDED.target_calories, water_goal = EXCLUDED.water_goal, 
                    personal_notes = EXCLUDED.personal_notes, last_updated = EXCLUDED.last_updated
            ''', (
                user_id, data.get('height'), data.get('weight'), data.get('age'), 
                data.get('gender'), data.get('activityLevel'), data.get('targetCalories'),
                data.get('waterGoal'), data.get('personalNotes'), today
            ))
            conn.commit()
            return jsonify({'status': 'success', 'message': 'Profile saved.'})

        if request.method == 'GET':
            cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
            profile = cur.fetchone()
            if profile and profile['last_updated']:
                profile_dict = dict(profile)
                profile_dict['last_updated'] = profile['last_updated'].strftime('%Y-%m-%d')
                return jsonify(profile_dict)
            return jsonify(dict(profile) if profile else {})
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
            if not all([user_id, log_date, log_data]): return jsonify({"error": "userId, date, and data are required"}), 400

            fields = [
                'breakfast_text', 'breakfast_kcal', 'lunch_text', 'lunch_kcal',
                'dinner_text', 'dinner_kcal', 'snacks_text', 'snacks_kcal',
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

# ===== 新增: 完成圓點 API =====
@app.route('/api/completion_dots', methods=['GET'])
def get_completion_dots():
    user_id = request.args.get('userId')
    year = request.args.get('year')
    month = request.args.get('month')
    if not all([user_id, year, month]): return jsonify({"error": "userId, year, and month are required"}), 400

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('''
            SELECT DISTINCT EXTRACT(DAY FROM log_date) 
            FROM daily_logs 
            WHERE user_id = %s 
              AND EXTRACT(YEAR FROM log_date) = %s 
              AND EXTRACT(MONTH FROM log_date) = %s
        ''', (user_id, year, month))
        # 將 (day,) 格式的元組列表轉換為 [day] 格式的數字列表
        days_with_logs = [item[0] for item in cur.fetchall()]
    conn.close()
    return jsonify(days_with_logs)

# ===== 升級: 趨勢圖表 API =====
@app.route('/api/trends', methods=['GET'])
def get_trends():
    user_id = request.args.get('userId')
    range_type = request.args.get('range', '7days') # 預設為 7天
    if not user_id: return jsonify({"error": "userId is required"}), 400
    
    today = datetime.now().date()
    if range_type == '30days':
        start_date = today - timedelta(days=29)
        labels = [(today - timedelta(days=i)).strftime('%-m/%-d') for i in range(29, -1, -1)]
    elif range_type == 'this_month':
        start_date = today.replace(day=1)
        days_in_month = (today.replace(month=today.month % 12 + 1, day=1) - timedelta(days=1)).day
        labels = [d for d in range(1, days_in_month + 1)]
    else: # 預設 7 天
        start_date = today - timedelta(days=6)
        labels = [(today - timedelta(days=i)).strftime('%-m/%-d') for i in range(6, -1, -1)]

    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('''
            SELECT 
                log_date, daily_weight, 
                COALESCE(breakfast_kcal,0) + COALESCE(lunch_kcal,0) + COALESCE(dinner_kcal,0) + COALESCE(snacks_kcal,0) as calories,
                water_cc, exercise_kcal
            FROM daily_logs 
            WHERE user_id = %s AND log_date >= %s
            ORDER BY log_date ASC
        ''', (user_id, start_date))
        logs = cur.fetchall()
    conn.close()
    
    logs_dict = {log['log_date'].strftime('%Y-%m-%d'): log for log in logs}
    trend_data = {'weight': [], 'calories': [], 'water': [], 'exercise': []}
    
    current_date = start_date
    while current_date <= today:
        date_str = current_date.strftime('%Y-%m-%d')
        if date_str in logs_dict:
            log = logs_dict[date_str]
            trend_data['weight'].append(log['daily_weight'])
            trend_data['calories'].append(log['calories'])
            trend_data['water'].append(log['water_cc'])
            trend_data['exercise'].append(log['exercise_kcal'])
        else:
            trend_data['weight'].append(None)
            trend_data['calories'].append(None)
            trend_data['water'].append(None)
            trend_data['exercise'].append(None)
        current_date += timedelta(days=1)
            
    return jsonify({ 'labels': labels, **trend_data })


# --- 啟動伺服器 ---

with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
