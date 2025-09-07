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
import redis # 導入 redis 庫
import json # 導入 json 庫用於序列化數據
import database # [新增] 導入我們自己寫的 database 模組
from threading import Timer # [新增] 導入 Timer 用於計時

app = Flask(__name__)

# 環境變數：從 Render 或 .env 自動抓取
line_channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_channel_secret = os.getenv("LINE_CHANNEL_SECRET")
openai_api_key = os.getenv("OPENAI_API_KEY")
redis_url = os.getenv("REDIS_URL") # 新增 Redis URL 環境變數
database_url = os.getenv("DATABASE_URL") # [新增]

# [新增] 訊息捆綁處理的等待時間 (秒)
MESSAGE_BUNDLE_DELAY = 10.0

# DEBUG: 檢查環境變數是否正確讀取
print(f"DEBUG: LINE_CHANNEL_ACCESS_TOKEN loaded: {'Yes' if line_channel_access_token else 'No'}")
print(f"DEBUG: LINE_CHANNEL_SECRET loaded: {'Yes' if line_channel_secret else 'No'}")
print(f"DEBUG: OPENAI_API_KEY loaded: {'Yes' if openai_api_key else 'No'}")
print(f"DEBUG: REDIS_URL loaded: {'Yes' if redis_url else 'No'}")
print(f"DEBUG: DATABASE_URL loaded: {'Yes' if database_url else 'No'}") # [新增]

# 初始化 LineBotApi 和 WebhookHandler
if line_channel_access_token and line_channel_secret:
    line_bot_api = LineBotApi(line_channel_access_token)
    handler = WebhookHandler(line_channel_secret)
else:
    print("ERROR: LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_SECRET is missing. Please set environment variables.")
    line_bot_api = None # 確保未初始化
    handler = None # 確保未初始化

# 初始化 OpenAI 客戶端
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
    print("ERROR: OPENAI_API_KEY is missing. OpenAI related features will be disabled.")
    
# 初始化 Redis 客戶端
r = None
if redis_url:
    try:
        r = redis.from_url(redis_url, decode_responses=True) # decode_responses=True 自動解碼為字符串
        # 嘗試 ping Redis 確保連線正常
        r.ping()
        print("DEBUG: Redis client initialized and connected successfully.")
    except Exception as e:
        print(f"ERROR: Failed to initialize or connect to Redis client: {e}")
        traceback.print_exc()
else:
    print("WARNING: REDIS_URL is not set. Session management will not be persistent.")

# --- [新增] 用戶訊息暫存機制與計時器管理 ---
user_message_timers = {} # 用於存放每個用戶的 Timer 物件

# 健康檢查用
@app.route("/", methods=['GET'])
def home():
    print("DEBUG: Received GET / request (Health Check)")
    return "OK", 200

# LINE Webhook 專用路徑
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    print(f"DEBUG: Received POST /callback request. Raw Body (first 200 chars): {body[:200]}...")
    print(f"DEBUG: X-Line-Signature: {signature}")

    if handler is None:
        print("ERROR: LINE Bot Handler not initialized. Aborting 500.")
        abort(500)

    try:
        print("DEBUG: Attempting to handle webhook event with handler...")
        handler.handle(body, signature)
        print("DEBUG: Webhook event handled successfully by handler.")
    except InvalidSignatureError:
        print("ERROR: InvalidSignatureError - Signature verification failed. Check LINE Channel Secret in Render and LINE Developers.")
        traceback.print_exc()
        abort(400)
    except Exception as e:
        print(f"CRITICAL ERROR: An unexpected error occurred during handler.handle: {e}")
        traceback.print_exc()
        abort(500)

    return "OK"

# --- [舊有] LIFF 頁面與 API 路由 (此區塊保持不變) ---

@app.route("/liff")
def liff_page():
    # 它會去 templates 資料夾中，找出 liff.html 這個檔案並回傳
    return render_template('liff.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')

@app.route("/init-db")
def init_database_route():
    try:
        database.init_db()
        return "Database tables initialized successfully!"
    except Exception as e:
        traceback.print_exc()
        return f"An error occurred during database initialization: {e}", 500

@app.route("/api/save_log", methods=['POST'])
def save_log_route():
    try:
        data = request.get_json()
        database.save_user_log(data)
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/load_log", methods=['GET'])
def load_log_route():
    try:
        user_id = request.args.get('userId')
        user_data = database.load_user_log(user_id)
        return jsonify({"status": "success", "data": user_data})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# --- [刪除] 舊的 send_delayed_response 函式 ---
# 舊的 send_delayed_response 函式已完全刪除，其功能被新的 process_message_bundle 取代。

# --- [新增] 訊息捆綁處理與回覆函式 ---
def process_message_bundle(user_id, reply_token):
    print(f"DEBUG: ⏰ Timer expired for user {user_id}. Starting to process message bundle.")
    
    # 1. 從 Redis 撈取該用戶的所有訊息
    redis_key = f"message_bundle:{user_id}"
    try:
        messages_str = r.get(redis_key)
        if not messages_str:
            print(f"WARNING: No message bundle found in Redis for user {user_id}. Aborting.")
            return
        
        # 將 JSON 字符串反序列化為 Python 列表
        message_bundle = json.loads(messages_str)
        print(f"DEBUG: Retrieved message bundle for user {user_id}: {len(message_bundle)} items.")
        
        # 處理完畢後，立即從 Redis 刪除，避免重複處理
        r.delete(redis_key)
        print(f"DEBUG: Deleted message bundle from Redis for user {user_id}.")

    except Exception as e:
        print(f"ERROR: Failed to retrieve or delete message bundle from Redis for user {user_id}: {e}")
        traceback.print_exc()
        return

    # 2. 智慧整理訊息，建構 OpenAI 的請求
    # 預設回覆
    reply_text = "目前無法回覆，請稍後再試 🧘"

    if not client:
        print("ERROR: OpenAI client is not initialized. Cannot call GPT.")
        # 直接回覆錯誤訊息，不再需要舊的延遲函式
        line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
        return

    # 準備給 OpenAI 的內容列表
    openai_content = []
    user_text_parts = []
    
    # 分離文字和圖片
    for msg in message_bundle:
        if msg['type'] == 'text':
            user_text_parts.append(msg['content'])
        elif msg['type'] == 'image':
            # 將圖片加入 content 列表
            openai_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{msg['content']}"
                }
            })
    
    # 將所有文字訊息合併成一個字串
    combined_text = "\n".join(user_text_parts)
    # 將合併後的文字加入 content 列表的最前面
    openai_content.insert(0, {"type": "text", "text": combined_text})
    
    # 3. 呼叫 OpenAI 進行分析 (將所有之前的 Prompt 邏輯整合於此)
    try:
        # --- 判斷意圖 ---
        print(f"DEBUG: Stage 1 (Bundle): Classifying combined text '{combined_text[:50]}...' intent.")
        judgment_response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": """你是一個訊息分類器。請根據用戶的文字內容（忽略圖片），判斷訊息屬於以下哪一種類型：
- 『營養/健康相關』：直接提問營養、飲食、熱量、減重等事實性或建議性內容。也包含對圖片的分析請求。
- 『情緒/閒聊/非營養提問』：表達情緒（如沮喪、開心）、分享生活日常，或是與營養健康主題無關但仍想與人聊天的內容。
- 『無關』：與營養健康主題完全無關，也不是表達情緒或想聊天的內容（例如隨意打字、廣告）。

只回覆分類名稱，不要有其他文字。
"""},
                {"role": "user", "content": combined_text or "（使用者只傳送了圖片）"}
            ],
            temperature=0
        )
        judgment_category = judgment_response.choices[0].message.content.strip()
        print(f"DEBUG: Judgment result: '{judgment_category}'")

        # --- 根據意圖選擇對應的 System Prompt ---
        system_prompt_content = ""
        has_image = any(msg['type'] == 'image' for msg in message_bundle)

        if has_image:
             # 如果有圖片，無論文字是什麼，都優先使用營養分析 Prompt
            print("DEBUG: Bundle contains image. Using vision analysis prompt.")
            system_prompt_content = """
你是一位友善且專業的營養師助理，專精於分析食物圖片的營養成分，並能結合使用者的文字問題進行綜合回覆。

1.  **分析圖片：**
    -   請根據**台灣的飲食指南**，將圖片中所有食物歸類到「六大類食物」。
    -   估計每種食物的**份量**（用拳頭、掌心等日常比喻）與**熱量**。
    -   計算整份餐點的**總熱量**。

2.  **結合文字回答：**
    -   閱讀使用者的文字問題，理解他的情境或額外需求。
    -   將圖片分析結果，融入到對他問題的回覆中。例如，如果他問「運動完吃這個好嗎？」，你應該結合餐點的蛋白質和碳水化合物含量來回答。

3.  **回覆格式：**
    -   **若使用者只想分析熱量**，請遵循「先總結總熱量，後條列細項」的格式。
    -   **若使用者有其他問題**，請自然地將營養分析融入回答，不需死板地條列。
    -   語氣口語化、簡潔自然。
    -   **非常重要：整個回覆請勿使用任何開場白、問候語或結尾語。**
"""
        elif judgment_category == '營養/健康相關':
            print("DEBUG: Bundle is nutrition related (text only).")
            system_prompt_content = """
你是一位友善、專業的營養師助理。
請以口語化、簡潔自然的語氣進行回覆，就像在 LINE 上與朋友簡短聊天一樣。
**非常重要：回覆務必簡潔，直接回答問題核心，請勿使用任何開場白、問候語或結尾語。**
在回答時，提供專業的營養知識，避免生硬的專業術語。
**在描述食物份量時，請盡量使用容易理解的日常比喻（例如：拳頭大小、掌心大小、一碗、一個馬克杯等）。**
"""
        elif judgment_category == '情緒/閒聊/非營養提問':
            print("DEBUG: Bundle is emotional/chat (text only).")
            system_prompt_content = """
你是一位友善、貼心且支持性的營養師助理，以**極為簡潔**的方式回應。
用戶正在表達情緒或分享日常，請給予**簡短且直接**的支持、理解或鼓勵，就像你在 LINE 上對朋友說一句暖心的話。
保持同理心和鼓勵的語氣。
**非常重要：回覆務必極其簡潔（目標在20-40字内完成），直接回答核心情緒或內容，請勿使用任何開場白、問候語或結尾語。**
"""
        elif judgment_category == '無關':
            print(f"DEBUG: Bundle is NOT nutrition related. Replying with emoji.")
            positive_emojis = ["😍"]
            reply_text_emoji = random.choice(positive_emojis)
            line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text_emoji))
            return
        
        else: # 處理未知的分類結果
            print(f"WARNING: Unexpected judgment category: '{judgment_category}'. Falling back to generic reply.")
            reply_text = "抱歉，我還不太明白您的意思，您可以再說清楚一點嗎？"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
            return

        # --- 執行最終的 OpenAI API 呼叫 ---
        print("DEBUG: Calling final OpenAI API with bundled content.")
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt_content},
                {"role": "user", "content": openai_content}
            ],
            temperature=0.7,
            max_tokens=500
        )
        reply_text = response.choices[0].message.content.strip()

    except AuthenticationError as e:
        print(f"ERROR: OpenAI Authentication Error: {e}. Check your API key and billing status.")
        reply_text = "GPT 驗證失敗，請檢查 API 金鑰和帳戶。🔐"
        traceback.print_exc()
    except (APIStatusError, APIConnectionError) as e:
        print(f"ERROR: OpenAI API Status/Connection Error: {e}. An issue occurred with OpenAI's servers or network.")
        reply_text = "GPT 服務暫時不穩定，請稍後再試。🌐"
        traceback.print_exc()
    except Exception as e:
        print(f"ERROR: ❌ An unexpected error occurred during GPT bundle call: {e}.")
        traceback.print_exc()
        # 使用預設錯誤訊息
        
    # 4. 發送最終回覆 (直接發送，無延遲)
    try:
        if line_bot_api is None:
            print("ERROR: line_bot_api is not initialized. Cannot reply.")
            return
        
        print(f"DEBUG: Sending final bundled reply to user {user_id}: '{reply_text[:50]}...'")
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=reply_text.strip())
        )
        print("DEBUG: Bundled reply sent successfully to LINE.")
    except Exception as e:
        print(f"ERROR: Failed to send bundled reply to LINE user {user_id}: {e}.")
        traceback.print_exc()


# --- [修改] 處理文字訊息 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id # 獲取用戶 ID
    user_input = event.message.text
    print(f"DEBUG: 🧾 Received text message from user {user_id}: '{user_input}'")

    if not r:
        print("ERROR: Redis is not available. The new message bundling logic cannot proceed.")
        # 可以選擇回覆一個錯誤訊息，或者直接忽略
        return

    try:
        # 1. 將訊息存入 Redis 的暫存列表
        redis_key = f"message_bundle:{user_id}"
        new_message = {"type": "text", "content": user_input}
        
        # 使用 Redis 的列表 (list) 來存放訊息會更直觀，但為了簡單起見，我們先用字串存 JSON
        messages_str = r.get(redis_key)
        if messages_str:
            message_list = json.loads(messages_str)
        else:
            message_list = []
        message_list.append(new_message)
        
        # 存回去，並設定過期時間 (例如 1 分鐘)，避免用戶不再發言導致資料殘留
        r.set(redis_key, json.dumps(message_list), ex=60) 
        print(f"DEBUG: Appended text message to bundle for user {user_id}.")

        # 2. 重置該用戶的計時器
        # 如果已有計時器，先取消它
        if user_id in user_message_timers and user_message_timers[user_id].is_alive():
            user_message_timers[user_id].cancel()
            print(f"DEBUG: Cancelled existing timer for user {user_id}.")

        # 建立一個新的計時器
        # 注意：reply_token 會在 Webhook 回應後失效，所以我們需要傳入最新的 reply_token
        timer = Timer(MESSAGE_BUNDLE_DELAY, process_message_bundle, args=[user_id, event.reply_token])
        user_message_timers[user_id] = timer
        timer.start()
        print(f"DEBUG: Started new {MESSAGE_BUNDLE_DELAY}s timer for user {user_id}.")

    except Exception as e:
        print(f"ERROR: An error occurred in handle_text_message for user {user_id}: {e}")
        traceback.print_exc()

# --- [修改] 處理圖片訊息 ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id # 獲取用戶 ID
    print(f"DEBUG: 🖼️ Received image message from user {user_id}")
    
    if not r:
        print("ERROR: Redis is not available. The new message bundling logic cannot proceed.")
        return
        
    try:
        # 1. 獲取圖片內容並 Base64 編碼
        message_content = line_bot_api.get_message_content(event.message.id)
        image_data = b''
        for chunk in message_content.iter_content():
            image_data += chunk
        base64_image = base64.b64encode(image_data).decode('utf-8')
        print(f"DEBUG: Image from user {user_id} Base64 encoded.")

        # 2. 將圖片數據存入 Redis 的暫存列表
        redis_key = f"message_bundle:{user_id}"
        new_message = {"type": "image", "content": base64_image}

        messages_str = r.get(redis_key)
        if messages_str:
            message_list = json.loads(messages_str)
        else:
            message_list = []
        message_list.append(new_message)
        
        r.set(redis_key, json.dumps(message_list), ex=60)
        print(f"DEBUG: Appended image message to bundle for user {user_id}.")

        # 3. 重置該用戶的計時器
        if user_id in user_message_timers and user_message_timers[user_id].is_alive():
            user_message_timers[user_id].cancel()
            print(f"DEBUG: Cancelled existing timer for user {user_id}.")

        timer = Timer(MESSAGE_BUNDLE_DELAY, process_message_bundle, args=[user_id, event.reply_token])
        user_message_timers[user_id] = timer
        timer.start()
        print(f"DEBUG: Started new {MESSAGE_BUNDLE_DELAY}s timer for user {user_id}.")
        
        # [刪除] 不再需要立即回覆「照片收到囉」

    except Exception as e:
        print(f"ERROR: An error occurred in handle_image_message for user {user_id}: {e}")
        traceback.print_exc()

# 正確的 Render 啟動方式：讀取 port 並綁定 0.0.0.0
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"DEBUG: Starting Flask app on host 0.0.0.0, port {port}")
    app.run(host="0.0.0.0", port=port)
