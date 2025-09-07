# --- Start of final app.py code (v4 - Refactored Logic) ---

import os
from flask import Flask, request, abort, render_template, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from openai import OpenAI, APIStatusError, APIConnectionError, AuthenticationError
import traceback
import time
import random
import base64
import requests
import redis # 導入 redis 庫
import json # 導入 json 庫用於序列化數據
import database # 導入我們自己寫的 database 模組
from threading import Timer # 導入 Timer 用於計時

app = Flask(__name__)

# --- 環境變數與常數設定 ---
line_channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_channel_secret = os.getenv("LINE_CHANNEL_SECRET")
openai_api_key = os.getenv("OPENAI_API_KEY")
redis_url = os.getenv("REDIS_URL")
database_url = os.getenv("DATABASE_URL")

MESSAGE_BUNDLE_DELAY = 10.0
CONVERSATION_MEMORY_SECONDS = 86400
KEY_MESSAGE_BUNDLE = "message_bundle:{user_id}"
KEY_CONVERSATION_HISTORY = "conversation_history:{user_id}"

# --- [新增] 您提供的三份高品質提示詞 ---
PROMPT_VISION_TASK = """你是一位友善且專業的營養師助理，專精於分析食物圖片的營養成分。
請根據圖片中的食物，提供以下詳細的營養分析：

1.  **分項營養素與份量估計：**
    -   請列出圖片中所有可識別的食物項目。
    -   對於每個食物項目，請根據**台灣的飲食指南**，將其歸類到「六大類食物」：**全穀雜糧類、豆魚蛋肉類、乳品類、蔬菜類、水果類、油脂與堅果種子類**。
    -   估計每種食物的**份量**，並盡量使用容易理解的日常比喻（例如：拳頭大小、掌心大小、一碗、一個馬克杯等），而不是模糊的「中等」、「適量」或「份」。
    -   估計每種食物所提供的**熱量 (卡路里)**。

2.  **總熱量加總：**
    -   計算並提供這份餐點的**總熱量粗估值**。

3.  **整體回覆格式：**
    -   **第一段 (簡潔總結)：** 直接給出這份餐點的**總熱量粗估值**，例如：「這份餐點大約XXX卡。」這段話應簡短有力，不帶任何表情符號，也不包含細節分析。
    -   **第二段 (詳細說明)：** 在第一段之後，請換行並列出圖片中所有食物的**六大類分類、估計份量、單項熱量**。請使用清晰的條列式或段落，讓資訊一目瞭然。
    -   回覆請用口語化、簡潔自然的語氣，就像在 LINE 上與朋友簡短聊天一樣。
    -   **非常重要：整個回覆請勿使用任何開場白、問候語或結尾語，例如『嘿』、『哈囉』、『您好』、『有問題再問我喔』、『希望有幫助』、『感謝』、『需要其他幫助嗎？』等。**
"""

PROMPT_NUTRITION_TASK = """你是一位友善、專業的營養師助理。
請以口語化、簡潔自然的語氣進行回覆，就像在 LINE 上與朋友簡短聊天一樣。
**非常重要：回覆務必簡潔，直接回答問題核心，請勿使用任何開場白、問候語或結尾語，例如『嘿』、『哈囉』、『您好』、『有問題再問我喔』、『希望有幫助』、『感謝』、『需要其他幫助嗎？』等，直接提供資訊即可。除非必要，否則不需要過度使用表情符號。**
在回答時，提供專業的營養知識，避免生硬的專業術語。
**在描述食物份量時，請盡量使用容易理解的日常比喻（例如：拳頭大小、掌心大小、一碗、一個馬克杯等），而不是模糊的「中等」或「適量」。**
"""

PROMPT_CHAT_TASK = """你是一位友善、貼心且支持性的營養師助理，以**極為簡潔**的方式回應。
用戶正在表達情緒或分享日常，請給予**簡短且直接**的支持、理解或鼓勵，就像你在 LINE 上對朋友說一句暖心的話。
保持同理心和鼓勵的語氣。如果語句內容隱含對減重或健康的沮喪，可以給予正向鼓勵。
**非常重要：回覆務必極其簡潔（目標在20-40字內完成），直接回答核心情緒或內容，請勿使用任何開場白、問候語或結尾語，例如『嘿』、『哈囉』、『您好』、『有問題再問我喔』、『希望有幫助』、『感謝』、『需要其他幫助嗎？』等。避免過度使用表情符號。**
"""

# --- 初始化 ---
print(f"DEBUG: LINE_CHANNEL_ACCESS_TOKEN loaded: {'Yes' if line_channel_access_token else 'No'}")
print(f"DEBUG: LINE_CHANNEL_SECRET loaded: {'Yes' if line_channel_secret else 'No'}")
print(f"DEBUG: OPENAI_API_KEY loaded: {'Yes' if openai_api_key else 'No'}")
print(f"DEBUG: REDIS_URL loaded: {'Yes' if redis_url else 'No'}")
print(f"DEBUG: DATABASE_URL loaded: {'Yes' if database_url else 'No'}")

try:
    if not (line_channel_access_token and line_channel_secret):
        raise ValueError("LINE Bot credentials missing")
    line_bot_api = LineBotApi(line_channel_access_token)
    handler = WebhookHandler(line_channel_secret)
    print("DEBUG: Line Bot SDK initialized successfully.")
except (ValueError, TypeError):
    print("ERROR: LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_SECRET is missing.")
    line_bot_api = None
    handler = None

try:
    if not openai_api_key:
        raise ValueError("OpenAI API key missing")
    client = OpenAI(api_key=openai_api_key)
    print("DEBUG: OpenAI client initialized successfully.")
except (ValueError, TypeError):
    print("ERROR: OPENAI_API_KEY is missing.")
    client = None

try:
    if not redis_url:
        raise ValueError("Redis URL is not set")
    r = redis.from_url(redis_url, decode_responses=True)
    r.ping()
    print("DEBUG: Redis client initialized and connected successfully.")
except Exception as e:
    print(f"ERROR: Failed to connect to Redis: {e}")
    r = None

user_message_timers = {}


# --- 路由 ---
@app.route("/", methods=['GET'])
def home():
    print("DEBUG: Received GET / request (Health Check)")
    return "OK", 200

@app.route("/callback", methods=['POST'])
def callback():
    if not handler:
        print("ERROR: LINE Bot Handler not initialized. Aborting 500.")
        abort(500)
        
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    print(f"DEBUG: Received POST /callback request. Raw Body (first 200 chars): {body[:200]}...")
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("ERROR: InvalidSignatureError - Signature verification failed.")
        traceback.print_exc()
        abort(400)
    except Exception as e:
        print(f"CRITICAL ERROR: An unexpected error occurred during handler.handle: {e}")
        traceback.print_exc()
        abort(500)
    return 'OK'

# --- [舊有] LIFF 頁面與 API 路由 (此區塊保持不變) ---
@app.route("/liff")
def liff_page():
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

# [新功能] 建立一個統一的發送函式，以應對 reply_token 可能失效的問題
def send_final_message(user_id, reply_token, message_objects):
    try:
        line_bot_api.reply_message(reply_token, message_objects)
        print(f"DEBUG: Successfully replied to user {user_id} using reply_token.")
    except LineBotApiError as e:
        if "Invalid reply token" in str(e): # 使用 str(e) 讓判斷更穩定
            print(f"WARN: Reply token for user {user_id} expired. Falling back to push_message.")
            line_bot_api.push_message(user_id, message_objects)
            print(f"DEBUG: Successfully pushed message to user {user_id}.")
        else:
            print(f"ERROR: Failed to send message to user {user_id} due to a LineBotApiError: {e}")
            traceback.print_exc()
    except Exception as e:
        print(f"ERROR: An unexpected error occurred in send_final_message for user {user_id}: {e}")
        traceback.print_exc()

# --- [核心修改] 採用「程式做路由，AI 當專家」的新流程 ---
def process_message_bundle(user_id, reply_token):
    print(f"DEBUG: ⏰ Timer expired for user {user_id}. Starting bundle processing.")
    
    if not r or not client or not line_bot_api:
        print("ERROR: Redis, OpenAI client, or Line Bot API is not available.")
        return

    # 1. 資料大集合
    bundle_key = KEY_MESSAGE_BUNDLE.format(user_id=user_id)
    history_key = KEY_CONVERSATION_HISTORY.format(user_id=user_id)
    
    try:
        messages_str = r.get(bundle_key)
        message_bundle = json.loads(messages_str) if messages_str else []
        if not message_bundle:
            print(f"WARNING: No message bundle found for user {user_id}. Aborting.")
            return
        r.delete(bundle_key)
        
        history_list_json = r.lrange(history_key, 0, -1)
        conversation_history = [json.loads(item) for item in history_list_json]
        
    except Exception as e:
        print(f"ERROR: Failed to retrieve data from Redis for user {user_id}: {e}")
        traceback.print_exc()
        return

    # 2. 準備當前訊息
    has_image = any(msg['type'] == 'image' for msg in message_bundle)
    current_user_content = []
    user_text_parts = []

    for msg in message_bundle:
        if msg['type'] == 'text':
            user_text_parts.append(msg['content'])
        elif msg['type'] == 'image':
            current_user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{msg['content']}",
                    "detail": "high"
                }
            })
    
    combined_text = "\n".join(user_text_parts)
    if combined_text:
        current_user_content.insert(0, {"type": "text", "text": combined_text})

    # 為了儲存歷史紀錄，需要一個簡潔的表示
    if has_image:
        current_input_for_history = "[使用者傳送了圖片]\n" + combined_text
    else:
        current_input_for_history = combined_text

    try:
        system_prompt = None
        
        # 3. 【程式路由】根據是否有圖片決定任務
        if has_image:
            # --- 有圖路徑 ---
            print(f"DEBUG: [Router] Image detected. Assigning Vision Task to Nutritionist.")
            system_prompt = PROMPT_VISION_TASK
        else:
            # --- 純文字路徑 ---
            print(f"DEBUG: [Router] Text only. Using classifier to determine task for Nutritionist.")
            # 使用 GPT-3.5 進行意圖分類
            classifier_response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "你是一個訊息分類器。請判斷用戶的訊息屬於以下哪一種類型：\n- 『營養/健康相關』\n- 『情緒/閒聊/非營養提問』\n\n只回覆分類名稱，不要有其他文字。"},
                    {"role": "user", "content": combined_text or "（使用者沒有輸入文字）"}
                ],
                temperature=0
            )
            intent = classifier_response.choices[0].message.content.strip()
            print(f"DEBUG: [Classifier] Intent is '{intent}'")

            if '營養/健康相關' in intent:
                system_prompt = PROMPT_NUTRITION_TASK
            else: # 包含 '情緒/閒聊' 或其他無法判斷的情況
                system_prompt = PROMPT_CHAT_TASK
        
        # 4. 組合最終請求並呼叫 OpenAI
        messages_to_openai = [{"role": "system", "content": system_prompt}] + conversation_history
        messages_to_openai.append({"role": "user", "content": current_user_content})
        
        response = client.chat.completions.create(
            model="gpt-4o", 
            messages=messages_to_openai, 
            temperature=0.7, 
            max_tokens=1024
        )
        reply_text = response.choices[0].message.content.strip()

        # 5. 更新記憶
        r.rpush(history_key, json.dumps({"role": "user", "content": current_input_for_history}))
        r.rpush(history_key, json.dumps({"role": "assistant", "content": reply_text}))
        r.expire(history_key, CONVERSATION_MEMORY_SECONDS)

        # 6. 統一發送最終訊息
        send_final_message(user_id, reply_token, TextSendMessage(text=reply_text))
        
    except Exception as e:
        print(f"ERROR: An error occurred during OpenAI call or memory update for user {user_id}: {e}")
        traceback.print_exc()
        error_message = "抱歉，我好像有點累了，請稍後再試一次喔！"
        send_final_message(user_id, reply_token, TextSendMessage(text=error_message))


# 處理文字訊息的函式
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    user_input = event.message.text
    print(f"DEBUG: 🧾 Received text message from user {user_id}: '{user_input}'")

    if not r:
        print("ERROR: Redis is not available.")
        return

    try:
        redis_key = KEY_MESSAGE_BUNDLE.format(user_id=user_id)
        new_message = {"type": "text", "content": user_input}
        
        messages_str = r.get(redis_key)
        message_list = json.loads(messages_str) if messages_str else []
        message_list.append(new_message)
        
        r.set(redis_key, json.dumps(message_list), ex=120) 
        
        if user_id in user_message_timers and user_message_timers[user_id].is_alive():
            user_message_timers[user_id].cancel()
        
        timer = Timer(MESSAGE_BUNDLE_DELAY, process_message_bundle, args=[user_id, event.reply_token])
        user_message_timers[user_id] = timer
        timer.start()
        print(f"DEBUG: Started new {MESSAGE_BUNDLE_DELAY}s timer for user {user_id}.")

    except Exception as e:
        print(f"ERROR: An error occurred in handle_text_message for user {user_id}: {e}")
        traceback.print_exc()

# 處理圖片訊息的函式
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    print(f"DEBUG: 🖼️ Received image message from user {user_id}")
    
    if not r or not line_bot_api:
        print("ERROR: Redis or Line Bot API not available.")
        return
        
    try:
        message_content = line_bot_api.get_message_content(event.message.id)
        image_data = b''.join(message_content.iter_content())
        base64_image = base64.b64encode(image_data).decode('utf-8')

        redis_key = KEY_MESSAGE_BUNDLE.format(user_id=user_id)
        new_message = {"type": "image", "content": base64_image}

        messages_str = r.get(redis_key)
        message_list = json.loads(messages_str) if messages_str else []
        message_list.append(new_message)
        
        r.set(redis_key, json.dumps(message_list), ex=120)

        if user_id in user_message_timers and user_message_timers[user_id].is_alive():
            user_message_timers[user_id].cancel()
        
        timer = Timer(MESSAGE_BUNDLE_DELAY, process_message_bundle, args=[user_id, event.reply_token])
        user_message_timers[user_id] = timer
        timer.start()
        print(f"DEBUG: Started new {MESSAGE_BUNDLE_DELAY}s timer for user {user_id}.")
        
    except Exception as e:
        print(f"ERROR: An error occurred in handle_image_message for user {user_id}: {e}")
        traceback.print_exc()

# --- 伺服器啟動 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"DEBUG: Starting Flask app on host 0.0.0.0, port {port}")
    app.run(host="0.0.0.0", port=port)

# --- End of final app.py code (v4 - Refactored Logic) ---
