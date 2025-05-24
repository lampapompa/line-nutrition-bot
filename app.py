import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import traceback # 引入 traceback 模組用於打印完整錯誤堆疊

app = Flask(__name__)

# 環境變數：從 Render 或 .env 自動抓取
line_channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_channel_secret = os.getenv("LINE_CHANNEL_SECRET")
openai_api_key = os.getenv("OPENAI_API_KEY")

# **DEBUG: 檢查環境變數是否正確讀取**
print(f"DEBUG: LINE_CHANNEL_ACCESS_TOKEN loaded: {line_channel_access_token}")
print(f"DEBUG: LINE_CHANNEL_SECRET loaded: {line_channel_secret}")
print(f"DEBUG: OPENAI_API_KEY loaded: {openai_api_key}") # 這裡會打印出金鑰的部分內容或None
print(f"DEBUG: Type of OPENAI_API_KEY: {type(openai_api_key)}")

# 初始化 LineBotApi 和 WebhookHandler
if line_channel_access_token and line_channel_secret:
    line_bot_api = LineBotApi(line_channel_access_token)
    handler = WebhookHandler(line_channel_secret)
else:
    print("ERROR: LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_SECRET is missing. Please set environment variables.")
    # 在生產環境中，如果關鍵變數缺失，通常會導致服務啟動失敗或功能受限。
    # 這裡選擇退出，確保問題被發現。
    exit(1)

# 初始化 OpenAI API
if openai_api_key:
    openai.api_key = openai_api_key
else:
    print("ERROR: OPENAI_API_KEY is missing. OpenAI related features will be disabled.")
    # 如果 OpenAI 金鑰缺失，GPT 功能將無法使用，但 LINE Bot 應能繼續響應預設消息。
    
# 健康檢查用（Render 會先 ping "/"，不寫會判定你沒開服務）
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
    print(f"DEBUG: X-Line-Signature: {signature}") # 打印簽名以便除錯

    # **新增的 DEBUG 區塊：在 handler.handle 前後和錯誤捕獲**
    try:
        print("DEBUG: Attempting to handle webhook event with handler...")
        handler.handle(body, signature)
        print("DEBUG: Webhook event handled successfully by handler.") # 如果這行出現，表示 handler.handle 成功了
    except InvalidSignatureError:
        print("ERROR: InvalidSignatureError - Signature verification failed. Check LINE Channel Secret in Render and LINE Developers.")
        traceback.print_exc() # 打印完整的錯誤堆疊追蹤
        abort(400) # Bad Request
    except Exception as e: # 捕獲其他所有可能的錯誤，這是關鍵！
        print(f"CRITICAL ERROR: An unexpected error occurred during handler.handle: {e}")
        traceback.print_exc() # 打印完整的錯誤堆疊追蹤
        abort(500) # Internal Server Error

    return "OK"

# 當收到「文字訊息」時觸發這裡
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    print(f"DEBUG: >>> Entering handle_message function. Event source type: {event.source.type}, User ID: {event.source.user_id}") # 確保這行出現
    print(f"DEBUG: Event type: {event.type}, Message ID: {event.message.id}")
    user_input = event.message.text
    print(f"DEBUG: 🧾 Received user message in handle_message: '{user_input}'")

    reply_text = "目前無法回覆，請稍後再試 🧘" # 預設回覆，以防任何錯誤

    if not openai.api_key:
        print("ERROR: OpenAI API Key is not set. Cannot call GPT.")
        # 如果金鑰沒有設定，直接回覆預設訊息
    else:
        try:
            print(f"DEBUG: Calling OpenAI ChatCompletion API with model gpt-3.5-turbo for input: '{user_input}'")
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "你是一位口語親切的營養師助理，請以真實人類語氣回覆訊息。"},
                    {"role": "user", "content": user_input}
                ],
                temperature=0.7
            )
            # **DEBUG: 打印 OpenAI 回應**
            print(f"DEBUG: 🎉 OpenAI API call successful. Full response: {response}")
            reply_text = response['choices'][0]['message']['content'].strip()
            print(f"DEBUG: Generated reply text: '{reply_text}'")

        except openai.error.AuthenticationError as e:
            print(f"ERROR: OpenAI Authentication Error: {e}. Check your API key and billing status.")
            reply_text = "GPT 驗證失敗，請檢查 API 金鑰和帳戶。🔐"
            traceback.print_exc()
        except openai.error.APIError as e:
            print(f"ERROR: OpenAI API Error: {e}. An issue occurred with OpenAI's servers.")
            reply_text = "GPT 服務暫時不穩定，請稍後再試。🌐"
            traceback.print_exc()
        except Exception as e: # 捕獲所有其他可能的錯誤
            print(f"ERROR: ❌ An unexpected error occurred during GPT call: {e}.")
            traceback.print_exc() # 打印完整的錯誤堆疊追蹤
            reply_text = "目前無法回覆，請稍後再試 🧘"

    try:
        print(f"DEBUG: Replying to LINE user with text: '{reply_text}'")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        print("DEBUG: Reply sent successfully to LINE.")
    except Exception as e:
        print(f"ERROR: Failed to reply to LINE user: {e}.")
        traceback.print_exc() # 打印完整的錯誤堆疊追蹤


# 正確的 Render 啟動方式：讀取 port 並綁定 0.0.0.0
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"DEBUG: Starting Flask app on host 0.0.0.0, port {port}")
    app.run(host="0.0.0.0", port=port)
