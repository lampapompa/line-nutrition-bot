import os
import psycopg2
import psycopg2.extras # 引入 DictCursor
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta

# --- 您的核心程式碼 (引擎) ---

# 從 Render 的環境變數中讀取資料庫連線 URL
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    """建立並回傳一個資料庫連線"""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """初始化資料庫，建立需要的資料表 (Table)"""
    print("正在檢查並初始化資料庫...")
    conn = get_db_connection()
    # 使用 with 語句確保資源被正確關閉
    with conn.cursor() as cur:
        # 您的 user_profiles 表格結構 (我加上了 profile_weight)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id VARCHAR(255) PRIMARY KEY,
                height REAL,
                profile_weight REAL, -- 為了 BMR/TDEE 計算而新增
                age INTEGER,
                gender VARCHAR(10),
                activity_level REAL,
                target_calories INTEGER
            );
        ''')
        # 您的 daily_logs 表格結構 (我加上了新欄位並調整了名稱)
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
    conn.commit()
    conn.close()
    print("資料庫初始化檢查完成。")


# --- 我的 Flask API 程式碼 (車體) ---

app = Flask(__name__)
CORS(app)

# --- API 端點：將您的函式包裝成 API ---

# API 1: 處理個人檔案
@app.route('/api/profile', methods=['GET', 'POST'])
def handle_profile():
    user_id = request.args.get('userId')
    if not user_id:
        return jsonify({"error": "userId is required"}), 400
        
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if request.method == 'POST':
            data = request.json['data']
            cur.execute('''
                INSERT INTO user_profiles (user_id, height, profile_weight, age, gender, activity_level, target_calories)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    height = EXCLUDED.height, profile_weight = EXCLUDED.profile_weight, age = EXCLUDED.age,
                    gender = EXCLUDED.gender, activity_level = EXCLUDED.activity_level, target_calories = EXCLUDED.target_calories
            ''', (
                user_id, data.get('height'), data.get('weight'), data.get('age'), 
                data.get('gender'), data.get('activityLevel'), data.get('targetCalories')
            ))
            conn.commit()
            return jsonify({'status': 'success', 'message': 'Profile saved.'})

        if request.method == 'GET':
            cur.execute('SELECT * FROM user_profiles WHERE user_id = %s', (user_id,))
            profile = cur.fetchone()
            return jsonify(dict(profile) if profile else {})
    conn.close()

# API 2: 處理每日記錄
@app.route('/api/log', methods=['GET', 'POST'])
def handle_log():
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if request.method == 'POST':
            req_data = request.json
            user_id = req_data.get('userId')
            log_date = req_data.get('date')
            log_data = req_data.get('data')
            
            if not all([user_id, log_date, log_data]):
                return jsonify({"error": "userId, date, and data are required"}), 400

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
            if not all([user_id, log_date]):
                return jsonify({"error": "userId and date are required"}), 400
                
            cur.execute('SELECT * FROM daily_logs WHERE user_id = %s AND log_date = %s', (user_id, log_date))
            log_entry = cur.fetchone()
            return jsonify(dict(log_entry) if log_entry else None)
    conn.close()

# API 3: 處理圖表數據
@app.route('/api/trends', methods=['GET'])
def get_trends():
    user_id = request.args.get('userId')
    if not user_id:
        return jsonify({"error": "userId is required"}), 400
        
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        seven_days_ago = datetime.now() - timedelta(days=6)
        cur.execute('''
            SELECT 
                log_date, daily_weight, 
                COALESCE(breakfast_kcal,0) + COALESCE(lunch_kcal,0) + COALESCE(dinner_kcal,0) + COALESCE(snacks_kcal,0) as calories,
                water_cc, exercise_kcal
            FROM daily_logs 
            WHERE user_id = %s AND log_date >= %s
            ORDER BY log_date ASC
        ''', (user_id, seven_days_ago.date()))
        logs = cur.fetchall()
    conn.close()
    
    labels = [(datetime.now() - timedelta(days=i)).strftime('%-m/%-d') for i in range(6, -1, -1)]
    logs_dict = {log['log_date'].strftime('%Y-%m-%d'): log for log in logs}
    trend_data = {'weight': [], 'calories': [], 'water': [], 'exercise': []}
    
    for i in range(6, -1, -1):
        date_str = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
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
            
    return jsonify({ 'labels': labels, **trend_data })

# --- 啟動伺服器 ---

# 在應用程式啟動時執行一次資料庫初始化檢查
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
