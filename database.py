import os
import psycopg2
import urllib.parse as urlparse
from datetime import datetime

# 從環境變數讀取資料庫 URL
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """建立並回傳一個資料庫連線"""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set")
    url = urlparse.urlparse(DATABASE_URL)
    conn = psycopg2.connect(
        dbname=url.path[1:],
        user=url.username,
        password=url.password,
        host=url.hostname,
        port=url.port
    )
    return conn

def init_db():
    """初始化資料庫，建立需要的資料表 (Table)"""
    conn = get_db_connection()
    cur = conn.cursor()
    # 建立使用者個人檔案資料表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id VARCHAR(255) PRIMARY KEY,
            height REAL,
            age INTEGER,
            gender VARCHAR(10),
            activity_level REAL,
            target_calories INTEGER
        );
    ''')
    # 建立每日記錄資料表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS daily_logs (
            user_id VARCHAR(255),
            log_date DATE,
            weight REAL,
            water INTEGER,
            exercise TEXT,
            breakfast TEXT,
            breakfast_cal INTEGER,
            lunch TEXT,
            lunch_cal INTEGER,
            dinner TEXT,
            dinner_cal INTEGER,
            snacks TEXT,
            snacks_cal INTEGER,
            PRIMARY KEY (user_id, log_date)
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()
    print("Database tables initialized.")

def save_user_log(data):
    """儲存使用者的個人檔案與每日記錄"""
    user_id = data.get('userId')
    if not user_id:
        raise ValueError("User ID is missing in data")

    today_str = datetime.now().strftime('%Y-%m-%d')
    conn = get_db_connection()
    cur = conn.cursor()

    # 使用 UPSERT 語法更新或插入 user_profiles
    cur.execute('''
        INSERT INTO user_profiles (user_id, height, age, gender, activity_level, target_calories)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            height = EXCLUDED.height,
            age = EXCLUDED.age,
            gender = EXCLUDED.gender,
            activity_level = EXCLUDED.activity_level,
            target_calories = EXCLUDED.target_calories;
    ''', (
        user_id, data.get('height'), data.get('age'), data.get('gender'), data.get('activityLevel'), data.get('targetCalories')
    ))

    # 使用 UPSERT 語法更新或插入 daily_logs
    cur.execute('''
        INSERT INTO daily_logs (user_id, log_date, weight, water, exercise, breakfast, breakfast_cal, lunch, lunch_cal, dinner, dinner_cal, snacks, snacks_cal)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, log_date) DO UPDATE SET
            weight = EXCLUDED.weight, water = EXCLUDED.water, exercise = EXCLUDED.exercise,
            breakfast = EXCLUDED.breakfast, breakfast_cal = EXCLUDED.breakfast_cal,
            lunch = EXCLUDED.lunch, lunch_cal = EXCLUDED.lunch_cal,
            dinner = EXCLUDED.dinner, dinner_cal = EXCLUDED.dinner_cal,
            snacks = EXCLUDED.snacks, snacks_cal = EXCLUDED.snacks_cal;
    ''', (
        user_id, today_str, data.get('weight'), data.get('water'), data.get('exercise'),
        data.get('breakfast'), data.get('breakfastCal'), data.get('lunch'), data.get('lunchCal'),
        data.get('dinner'), data.get('dinnerCal'), data.get('snacks'), data.get('snacksCal')
    ))

    conn.commit()
    cur.close()
    conn.close()

def load_user_log(user_id):
    """讀取使用者的個人檔案與今日記錄"""
    if not user_id:
        return {}

    today_str = datetime.now().strftime('%Y-%m-%d')
    conn = get_db_connection()
    cur = conn.cursor()
    response_data = {}

    # 讀取個人檔案
    cur.execute("SELECT height, age, gender, activity_level, target_calories FROM user_profiles WHERE user_id = %s", (user_id,))
    profile = cur.fetchone()
    if profile:
        response_data['height'] = profile[0]
        response_data['age'] = profile[1]
        response_data['gender'] = profile[2]
        response_data['activityLevel'] = profile[3]
        response_data['targetCalories'] = profile[4]

    # 讀取今日記錄
    cur.execute("""
        SELECT 
            weight, water, exercise, 
            breakfast, breakfast_cal, 
            lunch, lunch_cal, 
            dinner, dinner_cal, 
            snacks, snacks_cal 
        FROM daily_logs 
        WHERE user_id = %s AND log_date = %s
    """, (user_id, today_str))
    log = cur.fetchone()
    if log:
        response_data['weight'] = log[0]
        response_data['water'] = log[1]
        response_data['exercise'] = log[2]
        response_data['breakfast'] = log[3]
        response_data['breakfastCal'] = log[4]
        response_data['lunch'] = log[5]
        response_data['lunchCal'] = log[6]
        response_data['dinner'] = log[7]
        response_data['dinnerCal'] = log[8]
        response_data['snacks'] = log[9]
        response_data['snacksCal'] = log[10]

    cur.close()
    conn.close()
    return response_data

