# --- Start of final app.py code (附有 image_count 邏輯的最終修正版) ---

import os
from flask import Flask, request, abort, render_template, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from openai import OpenAI
import traceback
import base64
import redis
import json
from threading import Timer

app = Flask(__name__)

# --- 環境變數與常數設定 ---
line_channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_channel_secret = os.getenv("LINE_CHANNEL_SECRET")
openai_api_key = os.getenv("OPENAI_API_KEY")
redis_url = os.getenv("REDIS_URL")

MESSAGE_BUNDLE_DELAY = 10.0
CONVERSATION_MEMORY_SECONDS = 86400
KEY_MESSAGE_BUNDLE = "message_bundle:{user_id}"
KEY_CONVERSATION_HISTORY = "conversation_history:{user_id}"


# --- [最終修正版] 提示詞拆分 ---
PROMPT_IDENTITY = """# 你的身份：你是一位友善熱情且專業又有同理心，在**台灣台中**執業的專業營養師，專精於分析食物圖片的營養成分，你的所有回覆都必須使用**台灣在地口語**與**正體中文**。

# 用詞規範：你必須嚴格遵守台灣的用詞習慣，絕對不可以使用中國大陸用語。舉例如下：
  - **必須用**「鮪魚」，**不可以用**「金枪鱼」。
  - **必須用**「馬鈴薯」，**不可以用**「土豆」。
  - **必須用**「鳳梨」，**不可以用**「菠蘿」。
"""

PROMPT_TASK_SINGLE_IMAGE = """## 任務：分析單張食物圖片
【你必須嚴格按照以下的範例和格式進行回覆，絕對不可偏離。】

1.  **分項營養素與份量估計**：
    * 請列出圖片中所有可識別的食物項目。
    * 對於每個食物項目，請根據**台灣的飲食指南**，將其歸類到「六大類食物」。
    * 估計每個食物的**份量**（用日常比喻），而不是模糊的詞。
    * 估計每個食物所提供的**熱量 (卡)**。

2.  **【回覆格式與範例】**：
    * **第一段 (簡潔總結)**：直接給出總熱量。
    * **第二段 (詳細說明)**：換行後，【必須使用以下條列式格式】，清楚列出所有食物項目。
    * --- 範例 START ---
    * 這份餐點大約450大卡。

        - 烤雞腿 (豆魚蛋肉類): 約 250 大卡
        - 白飯一碗 (全穀雜糧類): 約 200 大卡
        - 燙花椰菜 (蔬菜類): 約 20 大卡 (熱量很低，主要是調味料)

    **亮點與建議**：這餐的烤雞腿提供了很棒的優質蛋白質，有助於增加飽足感、維持肌肉量，非常棒喔！**減重期間**如果能將部分白飯換成糙米或地瓜，就能增加更多纖維質，讓血糖更穩定，對減重會更有幫助！
    * --- 範例 END ---
"""

PROMPT_TASK_MULTI_IMAGE = """## 任務：分析多張食物圖片
【你必須嚴格按照以下的範例和格式進行回覆，絕對不可偏離。】

1.  **多圖處理原則**：
    * 你必須**依序、獨立地**對每一張含有食物的圖片進行詳細的熱量拆解。
    * 在回覆時，請用「**圖一：[餐點名稱]**」、「**圖二：[餐點名稱]**」的格式來清晰區分。
    * 在全部分析完畢後，才計算**【本次紀錄總熱量】**，並針對**所有餐點的組合**給出整體的「亮點與建議」。

2.  **【回覆格式與範例】**：
    * --- 範例 START ---
    * 這份餐點圖一加圖二大約620大卡。

    * **圖一：烤雞腿便當**
        - 烤雞腿 (豆魚蛋肉類): 約 250 大卡
        - 白飯一碗 (全穀雜糧類): 約 200 大卡
        - 燙花椰菜 (蔬菜類): 約 20 大卡

    * **圖二：烤玉米**
        - 烤玉米一支 (全穀雜糧類): 約 150 大卡

    **亮點與建議**：這兩餐組合起來有優質的蛋白質和蔬菜，很不錯！玉米是好的澱粉來源，但**減重期間**如果跟便當的白飯搭配，澱粉量會稍微多一些，建議可以把其中一餐的澱粉減半，會更符合減重目標喔！
    * --- 範例 END ---
"""

PROMPT_TASK_NON_FOOD = """## 備註任務：處理特殊內容
1.  **處理非食物圖片**：如果你判斷**所有圖片**中都**完全沒有**可分析的食物（例如：風景、寵物），請不要執行分析任務。你必須改為用輕鬆、口語化的方式，說明你沒有在照片中看到食物，並鼓勵學員傳送飲食照片給你分析。
2.  **處理混合內容**：如果有多張圖片中，有部分不是食物，請正常分析食物圖片，並在回覆的最後用 P.S. 的方式輕鬆帶到非食物內容即可。例如：「P.S. 您家的貓咪好可愛喔！」。
"""

PROMPT_NUTRITION_TASK = """# 你的身份：你是一位在**台灣台中**執業的友善熱情、專業又有同理心的營養師。你的所有回覆都必須使用**台灣在地口語**與**正體中文**。

# 用詞規範：你必須嚴格遵守台灣的用詞習慣，絕對不可以使用中國大陸用語。舉例如下：
  - **必須用**「鮪魚」，**不可以用**「金枪鱼」。
  - **必須用**「馬鈴薯」，**不可以用**「土豆」。
  - **必須用**「鳳梨」，**不可以用**「菠蘿」。

# 回覆原則：
1. **核心立場**：你所有的回答，都必須基於「正在幫助減重學員」這個前提。
2. **風格與長度**：以口語化、自然的語氣進行回覆。**整體回覆必須控制在 2-3 句話內，簡潔有力。**
3. **格式**：絕不使用任何開場白或結尾語。

# 【核心任務與結尾格式】
你的任務是以**減重期間**的考量出發，用 2-3 句話回答減重學員的文字問題，並帶給他們支持和動力。

--- 範例 START ---
[學員提問]：可以吃火鍋嗎？

[你的回覆]：
當然可以！**減重期間**吃火鍋只要多燙點蔬菜、少選加工料，就是很棒的減重餐喔。聰明吃比完全忌口更重要，一起加油！
--- 範例 END ---
"""

PROMPT_CHAT_TASK = """# 你的身份：你是一位在**台灣台中**執業的友善熱情、專業又有同理心的營養師。你的所有回覆都必須使用**台灣在地口語**與**正體中文**。

# 核心任務：
用戶正在表達**減重期間**情緒或分享日常，請給予**簡短且直接**的**減重期間**的支持、理解或鼓勵，就像你在 LINE 上對朋友說一句暖心的話。
**回覆務必極其簡潔（目標在20-40字內完成），直接回答核心情緒或內容，絕不說教或延伸話題。**
**非常重要：絕不使用任何開場白或結尾語。**
"""

# --- (以下為不變的程式碼) ---

# --- 初始化 ---
print(f"DEBUG: LINE_CHANNEL_ACCESS_TOKEN loaded: {'Yes' if line_channel_access_token else 'No'}")
print(f"DEBUG: LINE_CHANNEL_SECRET loaded: {'Yes' if line_channel_secret else 'No'}")
print(f"DEBUG: OPENAI_API_KEY loaded: {'Yes' if openai_api_key else 'No'}")
print(f"DEBUG: REDIS_URL loaded: {'Yes' if redis_url else 'No'}")

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

# --- 核心邏輯函式 ---
def send_final_message(user_id, reply_token, message_objects):
    try:
        line_bot_api.reply_message(reply_token, message_objects)
        print(f"DEBUG: Successfully replied to user {user_id} using reply_token.")
    except LineBotApiError as e:
        if "Invalid reply token" in str(e):
            print(f"WARN: Reply token for user {user_id} expired. Falling back to push_message.")
            line_bot_api.push_message(user_id, message_objects)
            print(f"DEBUG: Successfully pushed message to user {user_id}.")
        else:
            print(f"ERROR: Failed to send message to user {user_id} due to a LineBotApiError: {e}")
    except Exception as e:
        print(f"ERROR: An unexpected error occurred in send_final_message for user {user_id}: {e}")

def process_message_bundle(user_id, reply_token):
    print(f"DEBUG: ⏰ Timer expired for user {user_id}. Starting bundle processing.")
    
    if not r or not client or not line_bot_api:
        print("ERROR: Redis, OpenAI client, or Line Bot API is not available.")
        return

    bundle_key = KEY_MESSAGE_BUNDLE.format(user_id=user_id)
    history_key = KEY_CONVERSATION_HISTORY.format(user_id=user_id)
    
    try:
        messages_str = r.get(bundle_key)
        message_bundle = json.loads(messages_str) if messages_str else []
        if not message_bundle:
            return
        r.delete(bundle_key)
        
        history_list_json = r.lrange(history_key, 0, -1)
        conversation_history = [json.loads(item) for item in history_list_json]
        
    except Exception as e:
        print(f"ERROR: Failed to retrieve data from Redis for user {user_id}: {e}")
        return

    # --- [核心修改] 程式碼路由邏輯 ---
    images = [msg for msg in message_bundle if msg['type'] == 'image']
    texts = [msg['content'] for msg in message_bundle if msg['type'] == 'text']
    image_count = len(images)
    combined_text = "\n".join(texts)

    current_user_content = []
    if combined_text:
        current_user_content.append({"type": "text", "text": combined_text})
    for img in images:
        current_user_content.append({
            "type": "image_url",
            "image_url": { "url": f"data:image/jpeg;base64,{img['content']}", "detail": "high" }
        })

    if image_count > 0:
        current_input_for_history = "[使用者傳送了圖片]\n" + combined_text
    else:
        current_input_for_history = combined_text

    try:
        system_prompt = None
        
        if image_count == 1:
            print(f"DEBUG: [Router] Single image detected. Assigning Single Image Task.")
            system_prompt = PROMPT_IDENTITY + PROMPT_TASK_SINGLE_IMAGE + PROMPT_TASK_NON_FOOD
        elif image_count > 1:
            print(f"DEBUG: [Router] Multiple images detected. Assigning Multi Image Task.")
            system_prompt = PROMPT_IDENTITY + PROMPT_TASK_MULTI_IMAGE + PROMPT_TASK_NON_FOOD
        else: # image_count == 0
            print(f"DEBUG: [Router] Text only. Using classifier.")
            classifier_response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "判斷使用者訊息屬於『營養/健康相關』還是『情緒/閒聊』。只回覆分類名稱。"},
                    {"role": "user", "content": combined_text or "（使用者沒有輸入文字）"}
                ], temperature=0 )
            intent = classifier_response.choices[0].message.content.strip()
            print(f"DEBUG: [Classifier] Intent is '{intent}'")
            if '營養/健康相關' in intent:
                system_prompt = PROMPT_NUTRITION_TASK
            else: 
                system_prompt = PROMPT_CHAT_TASK
        
        messages_to_openai = [{"role": "system", "content": system_prompt}] + conversation_history
        messages_to_openai.append({"role": "user", "content": current_user_content})
        
        response = client.chat.completions.create( model="gpt-4o", messages=messages_to_openai, temperature=0.7, max_tokens=1024 )
        reply_text = response.choices[0].message.content.strip()

        r.rpush(history_key, json.dumps({"role": "user", "content": current_input_for_history}))
        r.rpush(history_key, json.dumps({"role": "assistant", "content": reply_text}))
        r.expire(history_key, CONVERSATION_MEMORY_SECONDS)

        send_final_message(user_id, reply_token, TextSendMessage(text=reply_text))
        
    except Exception as e:
        print(f"ERROR: An error occurred during OpenAI call for user {user_id}: {e}")
        traceback.print_exc()
        error_message = "抱歉，我好像有點累了，請稍後再試一次喔！"
        send_final_message(user_id, reply_token, TextSendMessage(text=error_message))

# --- Webhook 訊息處理 ---
@app.route("/", methods=['GET'])
def home():
    return "OK", 200

@app.route("/callback", methods=['POST'])
def callback():
    if not handler: abort(500)
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        print(f"CRITICAL ERROR: An unexpected error occurred during handler.handle: {e}")
        traceback.print_exc()
        abort(500)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    user_input = event.message.text
    print(f"DEBUG: 🧾 Received text message from user {user_id}: '{user_input}'")

    if user_input == "清除記憶體":
        print(f"DEBUG: [Command] Received 'clear memory' command from user {user_id}.")
        if r:
            try:
                history_key = KEY_CONVERSATION_HISTORY.format(user_id=user_id)
                r.delete(history_key)
                reply_text = "✅ 記憶已清除！"
            except Exception as e:
                reply_text = f"❌ 清除記憶時發生錯誤: {e}"
        else:
            reply_text = "❌ Redis 未連線，無法清除記憶。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return 

    if not r: return

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
    except Exception as e:
        print(f"ERROR: An error occurred in handle_text_message for user {user_id}: {e}")

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    print(f"DEBUG: 🖼️ Received image message from user {user_id}")
    
    if not r or not line_bot_api: return
        
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
    except Exception as e:
        print(f"ERROR: An error occurred in handle_image_message for user {user_id}: {e}")
        try:
            error_message = "哎呀，我的眼睛好像有點花了，沒看清楚您的照片，可以再傳一次嗎？"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_message))
        except Exception as reply_e:
            print(f"ERROR: Failed to even send the error reply: {reply_e}")


# --- LIFF 頁面與 API 路由 (您可將 api_server.py 的路由貼於此處) ---
@app.route("/liff")
def liff_page():
    return render_template('liff.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')


# --- 伺服器啟動 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"DEBUG: Starting Flask app on host 0.0.0.0, port {port}")
    app.run(host="0.0.0.0", port=port)

# --- End of final app.py code ---
