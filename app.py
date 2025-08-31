import os
from flask import Flask, request, abort, render_template, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from openai import OpenAI, APIStatusError, APIConnectionError, AuthenticationError
import traceback
import time
import random
import base64
import requests
import redis
import json
from datetime import datetime
import psycopg2
import urllib.parse as urlparse

app = Flask(__name__)

# --- 環境變數：從 Render 或 .env 自動抓取 ---
line_channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_channel_secret = os.getenv("LINE_CHANNEL_SECRET")
openai_api_key = os.getenv("OPENAI_API_KEY")
redis_url = os.getenv("REDIS_URL")
database_url = os.getenv("DATABASE_URL") # 新增 DATABASE_URL

# --- DEBUG: 檢查環境變數是否正確讀取 ---
print(f"DEBUG: LINE_CHANNEL_ACCESS_TOKEN loaded: {'Yes' if line_channel_access_token else 'No'}")
print(f"DEBUG: LINE_CHANNEL_SECRET loaded: {'Yes' if line_channel_secret else 'No'}")
print(f"DEBUG: OPENAI_API_KEY loaded: {'Yes' if openai_api_key else 'No'}")
print(f"DEBUG: REDIS_URL loaded: {'Yes' if redis_url else 'No'}")
print(f"DEBUG: DATABASE_URL loaded: {'Yes' if database_url else 'No'}")

# --- 初始化 LineBotApi 和 WebhookHandler ---
if line_channel_access_token and line_channel_secret:
    line_bot_api = LineBotApi(line_channel_access_token)
    handler = WebhookHandler(line_channel_secret)
else:
    print("ERROR: LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_SECRET is missing.")
    line_bot_api = None
    handler = None

# --- 初始化 OpenAI 客戶端 ---
client = None
if openai_api_key:
    try:
        client = OpenAI(api_key=openai_api_key)
        print("DEBUG: OpenAI client initialized successfully.")
    except Exception as e:
        print(f"ERROR: Failed to initialize OpenAI client: {e}")
        traceback.print_exc()
        client = None
else:
    print("ERROR: OPENAI_API_KEY is missing.")
    
# --- 初始化 Redis 客戶端 (用於圖片快取) ---
r = None
if redis_url:
    try:
        r = redis.from_url(redis_url, decode_responses=True)
        r.ping()
        print("DEBUG: Redis client connected successfully.")
    except Exception as e:
        print(f"ERROR: Failed to connect to Redis client: {e}")
        traceback.print_exc()
else:
    print("WARNING: REDIS_URL is not set. Image caching will be disabled.")

# --- [新功能] PostgreSQL 資料庫連線 ---
def get_db_connection():
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set")
    url = urlparse.urlparse(database_url)
    conn = psycopg2.connect(
        dbname=url.path[1:],
        user=url.username,
        password=url.password,
        host=url.hostname,
        port=url.port
    )
    return conn

# --- [新功能] 建立資料表的函式 (只需要執行一次) ---
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # 建立一個儲存使用者個人檔案的 table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id VARCHAR(255) PRIMARY KEY,
            height REAL,
            age INTEGER,
            gender VARCHAR(10),
            target_calories INTEGER
        );
    ''')
    # 建立一個儲存每日記錄的 table
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

# --- [新功能] 觸發建立資料表的臨時路由 ---
@app.route("/init-db")
def init_database_route():
    try:
        init_db()
        return "Database tables initialized successfully!"
    except Exception as e:
        print(f"ERROR in /init-db: {e}")
        traceback.print_exc()
        return f"An error occurred during database initialization: {e}", 500


# --- 健康檢查用 ---
@app.route("/", methods=['GET'])
def home():
    print("DEBUG: Received GET / request (Health Check)")
    return "OK", 200

# --- LINE Webhook 專用路徑 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    if handler is None:
        abort(500)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        print(f"CRITICAL ERROR: An unexpected error occurred during handler.handle: {e}")
        traceback.print_exc()
        abort(500)
    return "OK"

# --- LIFF 頁面專用路由 ---
@app.route("/liff")
def liff_page():
    return render_template('liff.html')

# --- 共用的回覆邏輯 (延遲和分段) ---
def send_delayed_response(event, reply_text):
    messages_to_send = []
    
    reply_length = len(reply_text)
    delay_seconds = 0
    if reply_length <= 30:
        delay_seconds = random.uniform(3, 5)
    elif 30 < reply_length <= 60:
        delay_seconds = random.uniform(5, 7)
    elif 60 < reply_length <= 100:
        delay_seconds = random.uniform(7, 9)
    else:
        delay_seconds = random.uniform(7, 9) + ((reply_length - 100) / 50) * random.uniform(1, 2)
        delay_seconds = min(delay_seconds, 30)

    print(f"DEBUG: Calculated reply delay: {delay_seconds:.2f} seconds.")
    time.sleep(delay_seconds)

    messages_to_send.append(TextSendMessage(text=reply_text.strip()))

    try:
        if line_bot_api is None:
            print("ERROR: line_bot_api is not initialized. Cannot reply.")
            return
        line_bot_api.reply_message(
            event.reply_token,
            messages_to_send
        )
        print("DEBUG: Reply sent successfully to LINE.")
    except Exception as e:
        print(f"ERROR: Failed to reply to LINE user: {e}.")
        traceback.print_exc()


# --- 處理文字訊息 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    user_input = event.message.text
    print(f"DEBUG: 🧾 Received text message from user {user_id}: '{user_input}'")

    reply_text = "目前無法回覆，請稍後再試 🧘" 

    if not client:
        print("ERROR: OpenAI client is not initialized.")
        send_delayed_response(event, reply_text)
        return

    pending_image_data_str = None
    if r:
        try:
            pending_image_data_str = r.get(f"pending_image:{user_id}")
        except Exception as redis_e:
            print(f"ERROR: Failed to get from Redis for user {user_id}: {redis_e}")
            
    if pending_image_data_str:
        image_analysis_keywords = ["熱量", "卡路里", "算", "估", "分析", "看", "這是什麼", "照片", "圖"]
        is_image_analysis_intent = any(keyword in user_input for keyword in image_analysis_keywords)
        
        if is_image_analysis_intent:
            if r:
                try:
                    r.delete(f"pending_image:{user_id}")
                except Exception as redis_e:
                    print(f"ERROR: Failed to delete from Redis for user {user_id}: {redis_e}")
            try:
                pending_image_data = json.loads(pending_image_data_str)
                base64_image = pending_image_data['base64_image']
                
                vision_system_prompt = """
                你是一位友善且專業的營養師助理，專精於分析食物圖片的營養成分。
                請根據圖片中的食物，提供以下詳細的營養分析：
                1.  **分項營養素與份量估計：**
                    -   請列出圖片中所有可識別的食物項目。
                    -   對於每個食物項目，請根據**台灣的飲食指南**，將其歸類到「六大類食物」。
                    -   估計每種食物的**份量**，並盡量使用容易理解的日常比喻（例如：拳頭大小、掌心大小）。
                    -   估計每種食物所提供的**熱量 (卡路里)**。
                2.  **總熱量加總：**
                    -   計算並提供這份餐點的**總熱量粗估值**。
                3.  **整體回覆格式：**
                    -   **第一段 (簡潔總結)：** 直接給出這份餐點的**總熱量粗估值**。
                    -   **第二段 (詳細說明)：** 在第一段之後，換行列出詳細分析。
                    -   **非常重要：整個回覆請勿使用任何開場白、問候語或結尾語。**
                """
                vision_response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_system_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }],
                    max_tokens=500,
                    temperature=0.7 
                )
                reply_text = vision_response.choices[0].message.content.strip()
                send_delayed_response(event, reply_text)
            except Exception as e:
                print(f"ERROR: ❌ An unexpected error occurred during GPT Vision call: {e}.")
                traceback.print_exc()
                reply_text = "抱歉，分析圖片時遇到問題，請稍後再試。😢"
                send_delayed_response(event, reply_text)
            return
        else:
            reply_text = "👍"
            send_delayed_response(event, reply_text)
            return

    try:
        judgment_response = client.chat.completions.create(
            model="gpt-3.5-turbo", 
            messages=[
                {"role": "system", "content": """你是一個訊息分類器。請判斷用戶的訊息屬於以下哪一種類型：
                - 『營養/健康相關』
                - 『情緒/閒聊/非營養提問』
                - 『無關』
                只回覆分類名稱，不要有其他文字。
                """},
                {"role": "user", "content": user_input}
            ],
            temperature=0
        )
        judgment_category = judgment_response.choices[0].message.content.strip()

        if judgment_category == '營養/健康相關':
            system_prompt_content = """
            你是一位友善、專業的營養師助理。
            請以口語化、簡潔自然的語氣進行回覆，就像在 LINE 上與朋友簡短聊天一樣。
            **非常重要：回覆務必簡潔，直接回答問題核心，請勿使用任何開場白、問候語或結尾語。**
            在描述食物份量時，請盡量使用容易理解的日常比喻。
            """
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt_content},
                    {"role": "user", "content": user_input}
                ],
                temperature=0.7,
                max_tokens=250 
            )
            reply_text = response.choices[0].message.content.strip()
            send_delayed_response(event, reply_text)
        elif judgment_category == '情緒/閒聊/非營養提問':
            sympathy_prompt_content = """
            你是一位友善、貼心且支持性的營養師助理，以**極為簡潔**的方式回應。
            用戶正在表達情緒或分享日常，請給予**簡短且直接**的支持、理解或鼓勵。
            **非常重要：回覆務必極其簡潔（目標在20-40字內完成），請勿使用任何開場白、問候語或結尾語。**
            """
            sympathy_response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": sympathy_prompt_content},
                    {"role": "user", "content": user_input}
                ],
                temperature=0.7,
                max_tokens=100
            )
            reply_text = sympathy_response.choices[0].message.content.strip()
            send_delayed_response(event, reply_text)
        elif judgment_category == '無關':
            positive_emojis = ["😊"]
            reply_text_emoji = random.choice(positive_emojis)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text_emoji))
        else:
            reply_text = "抱歉，我還不太明白您的意思，您可以再說清楚一點嗎？"
            send_delayed_response(event, reply_text)
    except Exception as e:
        print(f"ERROR: ❌ An unexpected error occurred during GPT call: {e}.")
        traceback.print_exc()
        send_delayed_response(event, reply_text)

# --- 處理圖片訊息 ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    print(f"DEBUG: >>> Entering handle_image_message function. User ID: {user_id}")
    
    reply_text = "抱歉，圖片處理服務目前無法使用，請稍後再試。😅"

    if not client:
        print("ERROR: OpenAI client is not initialized.")
        send_delayed_response(event, reply_text)
        return

    try:
        message_content = line_bot_api.get_message_content(event.message.id)
        image_data = b''.join(chunk for chunk in message_content.iter_content())
        base64_image = base64.b64encode(image_data).decode('utf-8')

        if r:
            image_info = {"base64_image": base64_image}
            r.set(f"pending_image:{user_id}", json.dumps(image_info), ex=300) 
            initial_reply_text = "照片收到囉。請問有什麼想問的嗎？"
            send_delayed_response(event, initial_reply_text)
        else:
            print("WARNING: Redis not initialized. Processing image directly.")
            vision_system_prompt = """
            你是一位友善且專業的營養師助理，專精於分析食物圖片的營養成分。
            請根據圖片中的食物，提供詳細的營養分析，包含六大類食物分類、份量估計、總熱量。
            **非常重要：回覆請勿使用任何開場白、問候語或結尾語。**
            """
            vision_response = client.chat.completions.create(
                model="gpt-4o", 
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_system_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }],
                max_tokens=500,
                temperature=0.7 
            )
            reply_text = vision_response.choices[0].message.content.strip()
            send_delayed_response(event, reply_text)
    except Exception as e:
        print(f"ERROR: ❌ An unexpected error occurred during image processing: {e}.")
        traceback.print_exc()
        reply_text = "處理圖片時遇到問題，請稍後再試 🧘"
        send_delayed_response(event, reply_text)
    
# --- [新功能] LIFF API (PostgreSQL 版本) ---
@app.route("/api/save_log", methods=['POST'])
def save_log():
    try:
        data = request.get_json()
        user_id = data.get('userId')
        if not user_id:
            return jsonify({"status": "error", "message": "User ID is missing"}), 400

        today_str = datetime.now().strftime('%Y-%m-%d')
        conn = get_db_connection()
        cur = conn.cursor()

        # 使用 UPSERT 語法：如果 user_id 已存在就更新，不存在就新增
        cur.execute('''
            INSERT INTO user_profiles (user_id, height, age, gender, target_calories)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                height = EXCLUDED.height,
                age = EXCLUDED.age,
                gender = EXCLUDED.gender,
                target_calories = EXCLUDED.target_calories;
        ''', (
            user_id, data.get('height'), data.get('age'), data.get('gender'), data.get('targetCalories')
        ))

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
        return jsonify({"status": "success"})

    except Exception as e:
        print(f"ERROR in save_log: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/load_log", methods=['GET'])
def load_log():
    try:
        user_id = request.args.get('userId')
        if not user_id:
            return jsonify({"status": "error", "message": "User ID is missing"}), 400

        today_str = datetime.now().strftime('%Y-%m-%d')
        conn = get_db_connection()
        cur = conn.cursor()
        response_data = {}

        # 讀取個人檔案
        cur.execute("SELECT height, age, gender, target_calories FROM user_profiles WHERE user_id = %s", (user_id,))
        profile = cur.fetchone()
        if profile:
            response_data['height'] = profile[0]
            response_data['age'] = profile[1]
            response_data['gender'] = profile[2]
            response_data['targetCalories'] = profile[3]

        # 讀取今日記錄
        cur.execute("SELECT weight, water, exercise, breakfast, breakfast_cal, lunch, lunch_cal, dinner, dinner_cal, snacks, snacks_cal FROM daily_logs WHERE user_id = %s AND log_date = %s", (user_id, today_str))
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
        return jsonify({"status": "success", "data": response_data})

    except Exception as e:
        print(f"ERROR in load_log: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
        
# --- 程式的進入點 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"DEBUG: Starting Flask app on host 0.0.0.0, port {port}")
    app.run(host="0.0.0.0", port=port)
