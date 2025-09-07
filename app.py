# --- Start of final app.py code ---

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
    if has_image:
        current_user_content.insert(0, {"type": "text", "text": combined_text})
        current_input_for_history = current_user_content
    else:
        current_input_for_history = combined_text

    try:
        reply_text = ""
        system_prompt = ""
        messages_to_openai = []
        
        # --- 路線二：只有純文字，先進行分類 (保留 emoji 測試機制) ---
        if not has_image:
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
        
        # --- 路線一 & 有意義的純文字：啟用「全能陪伴教練」 ---
        print(f"DEBUG: User {user_id} entering 'Companion Coach' main logic.")
        
        # [修改] 植入整合所有討論結果的最終版「超級指令」
        system_prompt = """# 核心身份與使命
你是一位頂尖的營養師助理，同時也是一位高 EQ、帶有幽默感和同理心的減重夥伴。
**你的回覆對象是正在參加減重課程的付費學員。**
因此，你所有的分析和建議，都必須以**『幫助學員成功減重』**為最高指導原則。你的目標是提供有價值的、可執行的建議，而不僅僅是數據。

# 主要任務：綜合處理使用者輸入
你的任務是處理使用者傳來的所有內容（圖片和文字）。請按照以下**優先級順序**來理解和回應：

## 優先級1：識別與處理【使用者修正】
如果使用者的最新訊息是在**修正**你上一輪的回覆（例如：指正食物名稱、提供包裝上的確切熱量），你必須執行**『錯誤修正協定』**：
1. **誠懇感謝與承認**：立刻感謝使用者的指正，並用輕鬆的語氣承認自己的估算有誤。
2. **採納使用者數據**：明確表示將以**使用者提供的數據為準**。
3. **基於新數據提供價值**：根據修正後的準確數據，重新提供有價值的分析或建議，並將其與減重目標連結。

## 優先級2：分析與建議【食物內容】
對於所有你能辨識為「食物」的內容，在提供營養和熱量分析的基礎上，你必須**主動**完成以下兩件事：
1. **提出減重優化建議**：主動提出**具體的食物代換建議**（例如：建議將炸物換成烤物、精緻澱粉換成全穀雜糧、增加蔬菜份量等）。
2. **解釋減重策略**：在建議中，要簡短說明**為什麼**要這樣代換，並扣回減重核心概念，例如：『...這樣可以**提高蛋白質與膳食纖維，增加飽足感**，讓您在減重期間比較不容易感到飢餓。』

## 優先級3：互動與引導【非食物內容】
對於所有非修正、非食物的內容（如寵物照、風景照、閒聊等），請遵循**『通用互動原則』**，用**一句話**進行簡短、溫暖或幽默的互動，然後自然地結束話題。

# 附加指令：上下文關聯
當使用者在傳送了新的圖片或內容後，緊接著提出問題時，如果問題中使用了『這個』、『那張圖』、『它』等模糊的代名詞，你**必須優先**將其關聯到**本輪對話中最新收到的內容**進行回答。只有當使用者明確指出了編號或你之前給過的標籤（如『第一道菜』）時，才以該指定為準。

# 最終回覆格式
你的回覆必須是一氣呵成的單一訊息。
- 如果是**修正回覆**，格式應為：【感謝與承認】->【基於新數據的分析與建議】。
- 如果是**常規分析**，格式應為：【營養分析與減重建議】 -> 【--- 分隔線】 -> 【P.S. 溫馨互動】。
- 所有互動都必須簡潔扼要。

## 情境：內容中【完全沒有】可辨識的食物
如果使用者傳來的內容，你判斷**完全不含任何可分析的食物**，你的目標是**禮貌地說明情況，並主動提供替代方案**，讓對話得以繼續。你的回覆應自然地包含以下**三個核心主軸**，但請用**你自己的、每次略有不同的口語化方式**來表達，**絕對不要使用一模一樣的罐頭訊息**：
1. **核心主軸1 - 承認看不懂**：友善地表明你無法從中辨識出食物內容。
2. **核心主軸2 - 表明能力範圍**：告訴使用者，你非常擅長分析「食品包裝」上的「營養成分」或「成分表」。
3. **核心主軸3 - 引導下一步**：鼓勵使用者若有相關資訊，可以提供給你。
"""
        
        if has_image:
            messages_to_openai = [{"role": "system", "content": system_prompt}] + conversation_history + [{"role": "user", "content": current_user_content}]
        else: # 對於有意義的純文字
            messages_to_openai = [{"role": "system", "content": system_prompt}] + conversation_history + [{"role": "user", "content": combined_text}]
        
        response = client.chat.completions.create(model="gpt-4o", messages=messages_to_openai, temperature=0.7, max_tokens=1024)
        reply_text = response.choices[0].message.content.strip()

        # 更新記憶
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

# --- End of final app.py code ---
