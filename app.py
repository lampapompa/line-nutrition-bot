# --- Start of new app.py code ---

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
import database # [新增] 導入我們自己寫的 database 模組
from threading import Timer # [新增] 導入 Timer 用於計時

app = Flask(__name__)

# --- 環境變數與常數設定 ---
line_channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_channel_secret = os.getenv("LINE_CHANNEL_SECRET")
openai_api_key = os.getenv("OPENAI_API_KEY")
redis_url = os.getenv("REDIS_URL") # 新增 Redis URL 環境變數
database_url = os.getenv("DATABASE_URL") # [新增]

# [修改] 訊息捆綁與記憶相關常數
MESSAGE_BUNDLE_DELAY = 10.0  # 訊息捆綁處理的等待時間 (秒)
CONVERSATION_MEMORY_SECONDS = 86400  # [新功能] 24 小時的對話記憶
KEY_MESSAGE_BUNDLE = "message_bundle:{user_id}" # [新功能] Redis Key 範本
KEY_CONVERSATION_HISTORY = "conversation_history:{user_id}" # [新功能] Redis Key 範本


# --- 初始化 ---
print(f"DEBUG: LINE_CHANNEL_ACCESS_TOKEN loaded: {'Yes' if line_channel_access_token else 'No'}")
print(f"DEBUG: LINE_CHANNEL_SECRET loaded: {'Yes' if line_channel_secret else 'No'}")
print(f"DEBUG: OPENAI_API_KEY loaded: {'Yes' if openai_api_key else 'No'}")
print(f"DEBUG: REDIS_URL loaded: {'Yes' if redis_url else 'No'}")
print(f"DEBUG: DATABASE_URL loaded: {'Yes' if database_url else 'No'}")

# [修改] 簡化初始化流程，使用 try/except 處理缺少環境變數的情況
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

user_message_timers = {} # 用於存放每個用戶的 Timer 物件


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
        if "Invalid reply token" in e.message:
            print(f"WARN: Reply token for user {user_id} expired. Falling back to push_message.")
            line_bot_api.push_message(user_id, message_objects)
            print(f"DEBUG: Successfully pushed message to user {user_id}.")
        else:
            print(f"ERROR: Failed to send message to user {user_id} due to a LineBotApiError: {e}")
            traceback.print_exc()
    except Exception as e:
        print(f"ERROR: An unexpected error occurred in send_final_message for user {user_id}: {e}")
        traceback.print_exc()

# [修改] 全面重寫 process_message_bundle 函式，實現最終版的「高 EQ 陪伴教練」混合策略
def process_message_bundle(user_id, reply_token):
    print(f"DEBUG: ⏰ Timer expired for user {user_id}. Starting bundle processing.")
    
    if not r or not client or not line_bot_api:
        print("ERROR: Redis, OpenAI client, or Line Bot API is not available.")
        return

    # 1. 資料大集合：從 Redis 撈取捆綁包和歷史紀錄
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

    # 2. 智慧分流判斷 & 準備當前訊息
    has_image = any(msg['type'] == 'image' for msg in message_bundle)
    
    current_user_content = []
    user_text_parts = []

    for msg in message_bundle:
        if msg['type'] == 'text':
            user_text_parts.append(msg['content'])
        elif msg['type'] == 'image':
            current_user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{msg['content']}"}
            })
    
    combined_text = "\n".join(user_text_parts)
    # 只有當有圖片時，才需要將文字和圖片內容放在同一個 content 列表中
    if has_image:
        current_user_content.insert(0, {"type": "text", "text": combined_text})
        current_input_for_history = current_user_content
    else:
        current_input_for_history = combined_text


    try:
        reply_text = ""
        system_prompt = ""
        messages_to_openai = []
        
        if has_image:
            # --- 路線一：有圖片，啟用「全能陪伴系教練」 ---
            print(f"DEBUG: User {user_id} has images. Using 'Companion Coach' mode.")
            system_prompt = """你是一位高 EQ、帶有幽默感和同理心的頂尖營養師助理。你的溝通風格溫暖、專業且簡潔扼要。
# 你的主要任務：
針對使用者傳送的內容，辨識出所有「真實世界的食物或餐點」，並提供專業、詳細的營養與熱量分析。
# 你的次要任務（非常重要）：
如果內容中包含「非食物」的圖片或與營養無關的閒聊文字，你需要進行簡短的互動。請嚴格遵循以下的『通用互動指導原則』：
1. **正面觀察原則**：簡短描述你看到的內容，並給予一個正面的、欣賞性的評價。
2. **幽默自嘲原則**：如果遇到你不懂的專業領域（數學、科學等），就幽默地承認自己的極限，並與你的營養師身份做對比。
3. **價值連結原則**：盡可能將互動與「好心情」、「動力」、「幸福感」等正面價值連結，輕輕點出這對健康生活和減重過程的重要性。
4. **安全邊界原則**：
    * 絕對避免對人物的「外貌、身材」做直接評論。應將評論的角度轉向「活力」、「開心的氛圍」等安全角度。
    * 避免對爭議性話題發表評論。
# 最終回覆格式（極度重要）：
你的回覆必須是一氣呵成的單一訊息，並嚴格遵循此格式：
1. 【營養分析】：優先、完整地完成所有食物的分析。這是回覆的主體。
2. 【分隔線】：在分析內容結束後，加上一行 `---`。
3. 【P.S. 互動】：在分隔線下方，針對「每一項非食物內容」，只用「一句話」進行溫暖或幽默的互動。這部分必須非常簡潔，目的是禮貌地將話題推開，然後結束。
## 情境二：內容中【完全沒有】可辨識的食物
如果使用者傳來的內容（無論是圖片還是文字），你判斷**完全不含任何可分析的食物**，在這種情況下，你的目標是**禮貌地說明情況，並主動提供替代方案**，讓對話得以繼續。
你的回覆應自然地包含以下**三個核心主軸**，但請用**你自己的、每次略有不同的口語化方式**來表達，**絕對不要使用一模一樣的罐頭訊息**：
1. **核心主軸1 - 承認看不懂**：友善地表明你無法從中辨識出食物內容。
2. **核心主軸2 - 表明能力範圍**：告訴使用者，你非常擅長分析「食品包裝」上的「營養成分」或「成分表」。
3. **核心主軸3 - 引導下一步**：鼓勵使用者若有相關資訊，可以提供給你。
"""
            messages_to_openai = [{"role": "system", "content": system_prompt}] + conversation_history + [{"role": "user", "content": current_user_content}]
            
            response = client.chat.completions.create(model="gpt-4o", messages=messages_to_openai, temperature=0.7, max_tokens=800)
            reply_text = response.choices[0].message.content.strip()

        else: # --- 路線二：只有純文字，先進行分類 ---
            print(f"DEBUG: User {user_id} is text-only. Using classification mode.")
            classification_prompt = """你是一個訊息分類器。請根據用戶的文字內容，判斷訊息屬於以下哪一種類型：
- 『營養/健康相關』：直接提問營養、飲食、熱量、減重等事實性或建議性內容。
- 『情緒/閒聊/非營養提問』：表達情緒（如沮喪、開心）、分享生活日常，或是想與人聊天的內容。
- 『無關』：與營養健康主題完全無關，也不是表達情緒或想聊天的內容（例如隨意打字、廣告）。
只回覆分類名稱，不要有其他文字。"""
            
            judgment_response = client.chat.completions.create(model="gpt-3.5-turbo", messages=[{"role": "system", "content": classification_prompt}, {"role": "user", "content": combined_text}], temperature=0)
            judgment_category = judgment_response.choices[0].message.content.strip()
            print(f"DEBUG: Text-only classification result: '{judgment_category}'")

            if judgment_category == '無關':
                positive_emojis = ["😍"]
                reply_text = random.choice(positive_emojis)
                send_final_message(user_id, reply_token, TextSendMessage(text=reply_text))
                return # 結束函式，不記錄到歷史
            else:
                system_prompt = """你是一位高 EQ、帶有幽默感和同理心的頂尖營養師助理。你的任務是回應使用者的提問或閒聊，並巧妙地連結回減重和健康生活的主軸，給予精神支持。回覆務必簡潔、溫暖。"""
                messages_to_openai = [{"role": "system", "content": system_prompt}] + conversation_history + [{"role": "user", "content": combined_text}]

                response = client.chat.completions.create(model="gpt-4o", messages=messages_to_openai, temperature=0.7, max_tokens=500)
                reply_text = response.choices[0].message.content.strip()

        # 只要不是「無關」的純文字，就更新記憶
        r.rpush(history_key, json.dumps({"role": "user", "content": current_input_for_history}))
        r.rpush(history_key, json.dumps({"role": "assistant", "content": reply_text}))
        r.expire(history_key, CONVERSATION_MEMORY_SECONDS)

        # 統一發送最終訊息
        send_final_message(user_id, reply_token, TextSendMessage(text=reply_text))
        
    except Exception as e:
        print(f"ERROR: An error occurred during OpenAI call or memory update for user {user_id}: {e}")
        traceback.print_exc()
        error_message = "抱歉，我好像有點累了，請稍後再試一次喔！"
        send_final_message(user_id, reply_token, TextSendMessage(text=error_message))

# [修改] 處理文字訊息的函式
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
        
        # [修改] 將最新的 reply_token 傳給計時器，以利 send_final_message 優先使用
        timer = Timer(MESSAGE_BUNDLE_DELAY, process_message_bundle, args=[user_id, event.reply_token])
        user_message_timers[user_id] = timer
        timer.start()
        print(f"DEBUG: Started new {MESSAGE_BUNDLE_DELAY}s timer for user {user_id}.")

    except Exception as e:
        print(f"ERROR: An error occurred in handle_text_message for user {user_id}: {e}")
        traceback.print_exc()

# [修改] 處理圖片訊息的函式
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

        # [修改] 將最新的 reply_token 傳給計時器
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

# --- End of new app.py code ---
