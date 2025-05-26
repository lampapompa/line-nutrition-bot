import os
from flask import Flask, request, abort
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

app = Flask(__name__)

# 環境變數：從 Render 或 .env 自動抓取
line_channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_channel_secret = os.getenv("LINE_CHANNEL_SECRET")
openai_api_key = os.getenv("OPENAI_API_KEY")
redis_url = os.getenv("REDIS_URL") # 新增 Redis URL 環境變數

# DEBUG: 檢查環境變數是否正確讀取
print(f"DEBUG: LINE_CHANNEL_ACCESS_TOKEN loaded: {'Yes' if line_channel_access_token else 'No'}")
print(f"DEBUG: LINE_CHANNEL_SECRET loaded: {'Yes' if line_channel_secret else 'No'}")
print(f"DEBUG: OPENAI_API_KEY loaded: {'Yes' if openai_api_key else 'No'}")
print(f"DEBUG: REDIS_URL loaded: {'Yes' if redis_url else 'No'}")

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

# --- 共用的回覆邏輯 (延遲和分段) ---
def send_delayed_response(event, reply_text):
    messages_to_send = []
    
    # 計算延遲時間 (已根據您的新要求調整)
    reply_length = len(reply_text)
    delay_seconds = 0
    if reply_length <= 30:
        delay_seconds = random.uniform(3, 5) # 30字內：3-5秒
    elif 30 < reply_length <= 60:
        delay_seconds = random.uniform(5, 7) # 31-60字：5-7秒
    elif 60 < reply_length <= 100:
        delay_seconds = random.uniform(7, 9) # 61-100字：7-9秒
    else: # 超過100字，使用基於100字的延遲再加上額外時間，但不超過30秒
        delay_seconds = random.uniform(7, 9) + ((reply_length - 100) / 50) * random.uniform(1, 2)
        delay_seconds = min(delay_seconds, 30) # 確保延遲不超過30秒

    print(f"DEBUG: Calculated initial reply delay: {delay_seconds:.2f} seconds for {reply_length} characters.")
    time.sleep(delay_seconds) # 執行延遲

    # --- START MODIFICATION: 刪除訊息分段邏輯 ---
    # messages_to_send 將只包含一條完整的訊息
    messages_to_send.append(TextSendMessage(text=reply_text.strip()))
    # --- END MODIFICATION ---

    # 新增 debug log 來驗證 messages_to_send 的內容和長度
    print(f"DEBUG: Preparing to send {len(messages_to_send)} messages.")
    if messages_to_send:
        print(f"DEBUG: First message text content: {messages_to_send[0].text[:50]}...") # 只印前50字
    else:
        print("DEBUG: messages_to_send is empty!")

    # 發送回覆
    try:
        if line_bot_api is None:
            print("ERROR: line_bot_api is not initialized. Cannot reply.")
            return

        print(f"DEBUG: Final reply messages to send: {len(messages_to_send)} messages for reply token: {event.reply_token}.")
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
    user_id = event.source.user_id # 獲取用戶 ID
    user_input = event.message.text
    print(f"DEBUG: 🧾 Received text message from user {user_id}: '{user_input}'")

    # 預設回覆，以防任何錯誤
    reply_text = "目前無法回覆，請稍後再試 🧘" 

    if not client:
        print("ERROR: OpenAI client is not initialized. Cannot call GPT.")
        send_delayed_response(event, reply_text)
        return

    # 檢查是否有待處理的圖片 (來自 Redis)
    pending_image_data_str = None
    if r:
        try:
            pending_image_data_str = r.get(f"pending_image:{user_id}")
            print(f"DEBUG: Checking Redis for pending image for user {user_id}: {pending_image_data_str is not None}")
        except Exception as redis_e:
            print(f"ERROR: Failed to get pending image from Redis for user {user_id}: {redis_e}")
            traceback.print_exc()
            # 如果 Redis 錯誤，當作沒有待處理圖片
            pending_image_data_str = None
            
    if pending_image_data_str:
        # 用戶發送了文字，且有待處理圖片
        # 判斷用戶是否在詢問圖片相關內容，例如熱量
        print(f"DEBUG: User {user_id} has a pending image. Checking text intent for image.")
        
        # 定義觸發圖片分析的關鍵字
        image_analysis_keywords = ["熱量", "卡路里", "算", "估", "分析", "看", "這是什麼", "照片", "圖"]
        
        # 判斷用戶文字是否包含圖片分析意圖
        is_image_analysis_intent = False
        if any(keyword in user_input for keyword in image_analysis_keywords):
            is_image_analysis_intent = True
        
        if is_image_analysis_intent:
            print(f"DEBUG: User {user_id} intends to analyze pending image.")
            # 清除待處理圖片標記，避免重複處理
            if r:
                try:
                    r.delete(f"pending_image:{user_id}")
                    print(f"DEBUG: Pending image deleted from Redis for user {user_id}.")
                except Exception as redis_e:
                    print(f"ERROR: Failed to delete pending image from Redis for user {user_id}: {redis_e}")
                    traceback.print_exc()

            try:
                pending_image_data = json.loads(pending_image_data_str)
                base64_image = pending_image_data['base64_image']
                
                # --- START MODIFICATION FOR TEXT HANDLER'S VISION PROMPT ---
                print("DEBUG: Calling GPT-4o for image analysis from text handler...")
                vision_system_prompt_for_text_handler = """
                你是一位友善且專業的營養師助理，專精於分析食物圖片的營養成分。
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
                vision_response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": vision_system_prompt_for_text_handler},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                            ]
                        }
                    ],
                    max_tokens=500, # 增加 max_tokens 以允許更詳細的回覆
                    temperature=0.7 
                )
                # --- END MODIFICATION FOR TEXT HANDLER'S VISION PROMPT ---
                reply_text = vision_response.choices[0].message.content.strip()
                send_delayed_response(event, reply_text)

            except AuthenticationError as e:
                print(f"ERROR: OpenAI Authentication Error: {e}. Check your API key and billing status.")
                reply_text = "GPT 驗證失敗，請檢查 API 金鑰和帳戶。🔐"
                traceback.print_exc()
                send_delayed_response(event, reply_text)
            except (APIStatusError, APIConnectionError) as e:
                print(f"ERROR: OpenAI API Status/Connection Error for Vision: {e}. An issue occurred with OpenAI's servers or network.")
                reply_text = "圖片分析服務暫時不穩定，請稍後再試。🌐"
                traceback.print_exc()
                send_delayed_response(event, reply_text)
            except Exception as e:
                print(f"ERROR: ❌ An unexpected error occurred during GPT Vision call: {e}.")
                traceback.print_exc()
                reply_text = "抱歉，分析圖片時遇到問題，請稍後再試。😢"
                send_delayed_response(event, reply_text)
            
            return # 處理完圖片相關請求後就返回

        else: # 有待處理圖片，但用戶文字與圖片分析無關
            print(f"DEBUG: User {user_id} has pending image, but text is not about image analysis. Replying with reminder.")
            # 調整語氣，更自然、不那麼「巴結」
            reply_text = "嗯？這條訊息好像不是在問照片的問題耶！如果你想問照片，記得告訴我喔。不然我可以回答其他關於營養或健康的問題啦！"
            send_delayed_response(event, reply_text)
            return # 處理完提醒後就返回

    # ----------------------------------------------------------------
    # 如果沒有待處理圖片，或者文字與圖片無關，則執行文字處理邏輯
    # ----------------------------------------------------------------
    try:
        print(f"DEBUG: Stage 1 (Text): Classifying '{user_input}' intent.")
        # 改變判斷器的提示詞，讓它回覆多種類型
        judgment_response = client.chat.completions.create(
            model="gpt-3.5-turbo", 
            messages=[
                {"role": "system", "content": """你是一個訊息分類器。請判斷用戶的訊息屬於以下哪一種類型：
                - 『營養/健康相關』：直接提問營養、飲食、熱量、減重等事實性或建議性內容。
                - 『情緒/閒聊/非營養提問』：表達情緒（如沮喪、開心）、分享生活日常，或是與營養健康主題無關但仍想與人聊天的內容。
                - 『無關』：與營養健康主題完全無關，也不是表達情緒或想聊天的內容（例如隨意打字、廣告）。

                只回覆分類名稱，不要有其他文字。
                """},
                {"role": "user", "content": user_input}
            ],
            temperature=0 # 判斷時，溫度設為 0，確保最確定性的回覆
        )
        judgment_category = judgment_response.choices[0].message.content.strip()
        print(f"DEBUG: Judgment result: '{judgment_category}'")

        if judgment_category == '營養/健康相關':
            print(f"DEBUG: Stage 2 (Text): Question is nutrition related. Generating detailed response for: '{user_input}'")
            # 營養師主體回覆邏輯
            system_prompt_content = """
            你是一位友善、專業的營養師助理。
            請以口語化、簡潔自然的語氣進行回覆，就像在 LINE 上與朋友簡短聊天一樣。
            **非常重要：回覆務必簡潔，直接回答問題核心，請勿使用任何開場白、問候語或結尾語，例如『嘿』、『哈囉』、『您好』、『有問題再問我喔』、『希望有幫助』、『感謝』、『需要其他幫助嗎？』等，直接提供資訊即可。除非必要，否則不需要過度使用表情符號。**
            在回答時，提供專業的營養知識，避免生硬的專業術語。
            **在描述食物份量時，請盡量使用容易理解的日常比喻（例如：拳頭大小、掌心大小、一碗、一個馬克杯等），而不是模糊的「中等」或「適量」。**
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
            print(f"DEBUG: 🎉 OpenAI GPT-4o API call successful. Full response: {response}")
            reply_text = response.choices[0].message.content.strip()
            print(f"DEBUG: Generated reply text: '{reply_text}'")
            send_delayed_response(event, reply_text)

        elif judgment_category == '情緒/閒聊/非營養提問':
            print(f"DEBUG: Stage 2 (Text): Question is emotional/chat. Generating sympathetic response for: '{user_input}'")
            # 新增的情緒/閒聊回覆邏輯，強調簡潔
            sympathy_prompt_content = """
            你是一位友善、貼心且支持性的營養師助理，以**極為簡潔**的方式回應。
            用戶正在表達情緒或分享日常，請給予**簡短且直接**的支持、理解或鼓勵，就像你在 LINE 上對朋友說一句暖心的話。
            保持同理心和鼓勵的語氣。如果語句內容隱含對減重或健康的沮喪，可以給予正向鼓勵。
            **非常重要：回覆務必極其簡潔（目標在20-40字內完成），直接回答核心情緒或內容，請勿使用任何開場白、問候語或結尾語，例如『嘿』、『哈囉』、『您好』、『有問題再問我喔』、『希望有幫助』、『感謝』、『需要其他幫助嗎？』等。避免過度使用表情符號。**
            """
            sympathy_response = client.chat.completions.create(
                model="gpt-4o", # 情感回覆也用4o，語氣會更自然
                messages=[
                    {"role": "system", "content": sympathy_prompt_content},
                    {"role": "user", "content": user_input}
                ],
                temperature=0.7, # 稍微降低溫度，讓語氣更自然、內容更聚焦
                max_tokens=100 # 這類回覆不需要太長
            )
            reply_text = sympathy_response.choices[0].message.content.strip()
            print(f"DEBUG: Generated sympathetic reply text: '{reply_text}'")
            send_delayed_response(event, reply_text)

        elif judgment_category == '無關':
            print(f"DEBUG: Stage 2 (Text): Question is NOT nutrition related. Replying with random positive emoji.")
            # 真正無關的回覆邏輯，維持表情符號
            positive_emojis = ["😊", "👍", "✨", "🌸", "💡", "💖", "🌟", "🙌", "🙂"]
            reply_text_emoji = random.choice(positive_emojis)
            try:
                if line_bot_api is None:
                    print("ERROR: line_bot_api is not initialized. Cannot reply with emoji.")
                    return
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text_emoji))
            except Exception as e:
                print(f"ERROR: Failed to reply with emoji: {e}.")
                traceback.print_exc()
        else: # 處理未知的分類結果
            print(f"WARNING: Unexpected judgment category: '{judgment_category}'. Falling back to generic reply.")
            reply_text = "抱歉，我還不太明白您的意思，您可以再說清楚一點嗎？"
            send_delayed_response(event, reply_text)

    except AuthenticationError as e:
        print(f"ERROR: OpenAI Authentication Error: {e}. Check your API key and billing status.")
        reply_text = "GPT 驗證失敗，請檢查 API 金鑰和帳戶。🔐"
        traceback.print_exc()
        send_delayed_response(event, reply_text)
    except (APIStatusError, APIConnectionError) as e:
        print(f"ERROR: OpenAI API Status/Connection Error: {e}. An issue occurred with OpenAI's servers or network.")
        reply_text = "GPT 服務暫時不穩定，請稍後再試。🌐"
        traceback.print_exc()
        send_delayed_response(event, reply_text)
    except Exception as e:
        print(f"ERROR: ❌ An unexpected error occurred during GPT call: {e}.")
        traceback.print_exc()
        reply_text = "目前無法回覆，請稍後再試 🧘"
        send_delayed_response(event, reply_text)

# --- 處理圖片訊息 ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id # 獲取用戶 ID
    print(f"DEBUG: >>> Entering handle_image_message function. User ID: {user_id}")
    
    reply_text = "抱歉，圖片處理服務目前無法使用，請稍後再試。😅"

    if not client:
        print("ERROR: OpenAI client is not initialized. Cannot process image.")
        send_delayed_response(event, reply_text)
        return

    try:
        # 1. 獲取圖片內容並 Base64 編碼
        message_content = line_bot_api.get_message_content(event.message.id)
        image_data = b''
        for chunk in message_content.iter_content():
            image_data += chunk
        base64_image = base64.b64encode(image_data).decode('utf-8')
        print(f"DEBUG: Image received and Base64 encoded for user {user_id}. Size: {len(base64_image)} bytes.")

        # 2. 將圖片數據儲存到 Redis，並標記為待處理
        if r:
            # 儲存 Base64 編碼的圖片和相關信息
            image_info = {"base64_image": base64_image}
            # 設定 5 分鐘過期時間 (300 秒)，如果用戶超過 5 分鐘沒有提問就忘記這張圖
            r.set(f"pending_image:{user_id}", json.dumps(image_info), ex=300) 
            print(f"DEBUG: Pending image saved to Redis for user {user_id}. Expires in 300s.")
            
            # 簡化圖片上傳的初步回覆，更自然
            initial_reply_text = "照片收到囉。請問有什麼想問的嗎？" # 移除表情符號，更簡潔
            send_delayed_response(event, initial_reply_text)
            return # 確保這裡有 return，避免後續代碼繼續執行

        else:
            print(f"WARNING: Redis not initialized. Cannot save pending image for user {user_id}. Image will be processed immediately without pending logic.")
            
            # --- START MODIFICATION FOR IMAGE HANDLER'S VISION PROMPT ---
            print("DEBUG: Calling GPT-4o for direct image analysis (Redis not available).")
            vision_system_prompt_for_image_handler = """
            你是一位友善且專業的營養師助理，專精於分析食物圖片的營養成分。
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
            vision_response = client.chat.completions.create(
                model="gpt-4o", 
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_system_prompt_for_image_handler},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }
                ],
                max_tokens=500, # 增加 max_tokens 以允許更詳細的回覆
                temperature=0.7 
            )
            # --- END MODIFICATION FOR IMAGE HANDLER'S VISION PROMPT ---
            reply_text = vision_response.choices[0].message.content.strip()
            send_delayed_response(event, reply_text)
            return # 處理完畢直接返回，因為沒有等待邏輯

    except AuthenticationError as e:
        print(f"ERROR: OpenAI Authentication Error: {e}. Check your API key and billing status.")
        reply_text = "GPT 驗證失敗，請檢查 API 金鑰和帳戶。🔐"
        traceback.print_exc()
        send_delayed_response(event, reply_text)
    except (APIStatusError, APIConnectionError) as e:
        print(f"ERROR: OpenAI API Status/Connection Error for Vision: {e}. An issue occurred with OpenAI's servers or network.")
        reply_text = "圖片分析服務暫時不穩定，請稍後再試。🌐"
        traceback.print_exc()
        send_delayed_response(event, reply_text)
    except Exception as e:
        print(f"ERROR: ❌ An unexpected error occurred during image processing: {e}.")
        traceback.print_exc()
        reply_text = "處理圖片時遇到問題，請稍後再試 🧘"
        send_delayed_response(event, reply_text)
    
# 正確的 Render 啟動方式：讀取 port 並綁定 0.0.0.0
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"DEBUG: Starting Flask app on host 0.0.0.0, port {port}")
    app.run(host="0.0.0.0", port=port)
