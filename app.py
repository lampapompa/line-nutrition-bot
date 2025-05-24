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
    # 在生產環境中，這裡通常會拋出錯誤或執行更嚴格的措施
    # 但為了讓程式碼能繼續被檢查，我們暫不直接退出
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
    
    # 計算延遲時間
    reply_length = len(reply_text)
    delay_seconds = 0
    if reply_length <= 10:
        delay_seconds = random.uniform(3, 5) # 3-5秒
    elif 10 < reply_length <= 30:
        delay_seconds = random.uniform(8, 12) # 8-12秒
    else: # 超過30字
        delay_seconds = random.uniform(10, 15) + (reply_length / 30) * random.uniform(1, 2) 
        delay_seconds = min(delay_seconds, 30) 

    print(f"DEBUG: Calculated initial reply delay: {delay_seconds:.2f} seconds for {reply_length} characters.")
    time.sleep(delay_seconds) # 執行延遲

    # 分段處理：將長回覆拆分成多條訊息，然後一次性發送
    # 限制每段訊息長度約在 50 個字，且總訊息不超過 5 條
    max_segment_length = 50 
    max_messages = 5

    sentences = []
    if reply_length > max_segment_length:
        current_segment = ""
        for char in reply_text:
            current_segment += char
            if len(current_segment) >= max_segment_length and char in ['。', '！', '？', '\n', '，', ' ']: # 嘗試在標點符號或空格處分段
                sentences.append(current_segment.strip())
                current_segment = ""
                if len(sentences) >= max_messages: # 達到最大訊息數，停止分段
                    break
        if current_segment and len(sentences) < max_messages: # 添加最後一段
            sentences.append(current_segment.strip())
    else:
        sentences.append(reply_text.strip()) # 短回覆直接加入

    # 確保最終至少有一條訊息，且不超過最大限制
    if not sentences:
        messages_to_send.append(TextSendMessage(text="抱歉，沒有內容可以回覆。"))
    else:
        for i, sentence in enumerate(sentences):
            if i < max_messages: # 確保不超過 5 條訊息
                messages_to_send.append(TextSendMessage(text=sentence))
            else:
                # 如果超出了 5 條，可以在最後一條加上提示
                if i == max_messages: # 只對第六條訊息做一次這個操作
                    messages_to_send[-1].text += " (訊息過長，請見完整回覆...)"
                break # 超過 5 條後停止添加

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

    reply_text = "目前無法回覆，請稍後再試 🧘" # 預設回覆，以防任何錯誤

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
                
                # ----------------------------------------------------------------
                # GPT-4o 圖片分析邏輯 (從 handle_image_message 函數中複製過來)
                # ----------------------------------------------------------------
                print("DEBUG: Calling GPT-4o for image analysis from text handler...")
                vision_response = client.chat.completions.create(
                    model="gpt-4o", # *** 這裡指定使用 gpt-4o 模型 ***
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": f"請詳細分析這張食物圖片，盡可能準確地估算其總熱量（卡路里），並列出可能的食物種類和估計份量。用戶的問題是：'{user_input}'。請用親切口語化的方式回覆。**回覆請務必簡潔，像在 LINE 上聊天一樣，不要過於冗長，將核心資訊傳達清楚即可。**"}, # <<< 在這裡的文字提示中加入簡潔要求
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                            ]
                        }
                    ],
                    max_tokens=400, # <<< 將這裡從 1000 調整到 400 (或 300-500 之間嘗試)
                    temperature=0.7 
                )
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
            reply_text = "嗯？這條訊息好像不是在問照片的問題耶！如果你想問照片，記得告訴我喔！不然我可以回答其他關於營養或健康的問題啦！😊"
            send_delayed_response(event, reply_text)
            return # 處理完提醒後就返回

    # ----------------------------------------------------------------
    # 如果沒有待處理圖片，或者文字與圖片無關，則執行原有的文字處理邏輯
    # ----------------------------------------------------------------
    is_nutrition_related = False
    try:
        print(f"DEBUG: Stage 1 (Text): Judging if '{user_input}' is nutrition related.")
        judgment_response = client.chat.completions.create(
            model="gpt-3.5-turbo", # 判斷通常用較快的模型
            messages=[
                {"role": "system", "content": "你是一個判斷器，請判斷用戶提出的問題是否與『營養』、『健康飲食』、『熱量計算』、『食物成分』或『減重』等主題相關。只回覆 '是' 或 '否'。"},
                {"role": "user", "content": user_input}
            ],
            temperature=0 # 判斷時，溫度設為 0，確保最確定性的回覆
        )
        judgment = judgment_response.choices[0].message.content.strip().lower()
        print(f"DEBUG: Judgment result: '{judgment}'")

        if judgment == '是':
            is_nutrition_related = True
        
    except Exception as e:
        print(f"ERROR: ❌ GPT Judgment call failed: {e}")
        traceback.print_exc()
        send_delayed_response(event, "抱歉，我的判斷系統出了點問題，請稍後再試。")
        return

    # ----------------------------------------------------------------
    # 第二階段：根據判斷結果進行回覆
    # ----------------------------------------------------------------
    if is_nutrition_related:
        print(f"DEBUG: Stage 2 (Text): Question is nutrition related. Generating detailed response for: '{user_input}'")
        try:
            # 強化的 System Prompt for "真人感"
            system_prompt_content = """
            你是一位溫暖、友善、專業且富有同理心的營養師助理。
            請以口語化、親切自然的語氣進行回覆，就像一位真正的朋友在與人交流。
            **非常重要：請務必保持簡潔，像在 LINE 上聊天一樣，不要過於冗長。盡量使用短句子，將核心資訊傳達清楚即可。**
            在回答時，除了提供專業的營養知識外，也可以適時加入一些鼓勵、關心或幽默的語氣。
            請簡潔明瞭地回答問題，避免過度冗長或生硬的專業術語。
            盡量在回答中加入表情符號，讓回覆更生動。
            """
            response = client.chat.completions.create(
                model="gpt-4o", # *** 這裡指定使用 gpt-4o 模型 ***
                messages=[
                    {"role": "system", "content": system_prompt_content},
                    {"role": "user", "content": user_input}
                ],
                temperature=0.8, # 提高一些溫度來增加真人感和多樣性
                max_tokens=400 # <<< 將這裡從 800 調整到 400 (或 300-500 之間嘗試)
            )
            print(f"DEBUG: 🎉 OpenAI GPT-4o API call successful. Full response: {response}")
            reply_text = response.choices[0].message.content.strip()
            print(f"DEBUG: Generated reply text: '{reply_text}'")

        except AuthenticationError as e:
            print(f"ERROR: OpenAI Authentication Error: {e}. Check your API key and billing status.")
            reply_text = "GPT 驗證失敗，請檢查 API 金鑰和帳戶。🔐"
            traceback.print_exc()
        except (APIStatusError, APIConnectionError) as e:
            print(f"ERROR: OpenAI API Status/Connection Error: {e}. An issue occurred with OpenAI's servers or network.")
            reply_text = "GPT 服務暫時不穩定，請稍後再試。🌐"
            traceback.print_exc()
        except Exception as e:
            print(f"ERROR: ❌ An unexpected error occurred during GPT call: {e}.")
            traceback.print_exc()
            reply_text = "目前無法回覆，請稍後再試 🧘"
        
        send_delayed_response(event, reply_text)

    else: # 非營養相關問題，回覆隨機正向 emoji
        print(f"DEBUG: Stage 2 (Text): Question is NOT nutrition related. Replying with random positive emoji.")
        positive_emojis = ["😊", "👍", "✨", "🌸", "💡", "💖", "🌟", "🙌", "🙂"]
        reply_text_emoji = random.choice(positive_emojis)
        send_delayed_response(event, reply_text_emoji)


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
            
            # 解決「丟圖沒回應」問題：簡化 initial_reply_text
            initial_reply_text = "照片收到，請問想問什麼？" # <<< 這裡修改了回覆文字
            send_delayed_response(event, initial_reply_text)
            return # <<< 確保這裡有 return，避免後續代碼繼續執行

        else:
            print(f"WARNING: Redis not initialized. Cannot save pending image for user {user_id}. Image will be processed immediately without pending logic.")
            # 如果 Redis 沒有初始化，或者連線失敗，則退回到立即處理模式 (無狀態模式)
            # 這會導致用戶上傳圖片後立即觸發分析，而不是等待
            # 這段是為了解決如果 Redis 失敗，服務不至於完全失效的備用方案
            
            # ----------------------------------------------------------------
            # GPT-4o 圖片分析邏輯 (無 Redis 狀態時的直接處理)
            # ----------------------------------------------------------------
            print("DEBUG: Calling GPT-4o for direct image analysis (Redis not available).")
            vision_response = client.chat.completions.create(
                model="gpt-4o", 
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "請詳細分析這張食物圖片，盡可能準確地估算其總熱量（卡路里），並列出可能的食物種類和估計份量。如果可以，請提供一些營養師的建議，例如是否有營養缺口，或者可以如何搭配。請用親切口語化的方式回覆。**回覆請務必簡潔，像在 LINE 上聊天一樣，不要過於冗長，將核心資訊傳達清楚即可。**"}, # <<< 在這裡的文字提示中加入簡潔要求
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }
                ],
                max_tokens=400, # <<< 將這裡從 1000 調整到 400 (或 300-500 之間嘗試)
                temperature=0.7 
            )
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
