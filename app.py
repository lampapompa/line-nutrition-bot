import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI, APIStatusError, APIConnectionError, AuthenticationError # 導入新版 OpenAI 錯誤類別
import traceback # 引入 traceback 模組用於打印完整錯誤堆疊

app = Flask(__name__)

# 環境變數：從 Render 或 .env 自動抓取
line_channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_channel_secret = os.getenv("LINE_CHANNEL_SECRET")
openai_api_key = os.getenv("OPENAI_API_KEY") # 獲取金鑰，用於打印 DEBUG 信息

# **DEBUG: 檢查環境變數是否正確讀取**
print(f"DEBUG: LINE_CHANNEL_ACCESS_TOKEN loaded: {line_channel_access_token}")
print(f"DEBUG: LINE_CHANNEL_SECRET loaded: {line_channel_secret}")
# 注意：為了安全，這裡不打印完整的 OpenAI API 金鑰，只打印是否存在
print(f"DEBUG: OPENAI_API_KEY loaded: {'Yes' if openai_api_key else 'No'}")
print(f"DEBUG: Type of OPENAI_API_KEY: {type(openai_api_key)}")

# 初始化 LineBotApi 和 WebhookHandler
if line_channel_access_token and line_channel_secret:
    line_bot_api = LineBotApi(line_channel_access_token)
    handler = WebhookHandler(line_channel_secret)
else:
    print("ERROR: LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_SECRET is missing. Please set environment variables.")
    exit(1) # 如果缺少必要變數，無法正常運行

# 初始化 OpenAI 客戶端
# OpenAI 客戶端會自動從 OPENAI_API_KEY 環境變數中讀取金鑰
client = None # 先初始化為 None
if openai_api_key:
    try:
        client = OpenAI(api_key=openai_api_key) # 顯式傳遞金鑰，確保初始化成功
        print("DEBUG: OpenAI client initialized successfully.")
    except Exception as e:
        print(f"ERROR: Failed to initialize OpenAI client: {e}")
        traceback.print_exc()
        client = None # 初始化失敗，將 client 設為 None
else:
    print("ERROR: OPENAI_API_KEY is missing. OpenAI related features will be disabled.")
    
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

    try:
        print("DEBUG: Attempting to handle webhook event with handler...")
        handler.handle(body, signature)
        print("DEBUG: Webhook event handled successfully by handler.")
    except InvalidSignatureError:
        print("ERROR: InvalidSignatureError - Signature verification failed. Check LINE Channel Secret in Render and LINE Developers.")
        traceback.print_exc()
        abort(400) # Bad Request
    except Exception as e:
        print(f"CRITICAL ERROR: An unexpected error occurred during handler.handle: {e}")
        traceback.print_exc()
        abort(500) # Internal Server Error

    return "OK"

# 當收到「文字訊息」時觸發這裡
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    print(f"DEBUG: >>> Entering handle_message function. Event source type: {event.source.type}, User ID: {event.source.user_id}")
    print(f"DEBUG: Event type: {event.type}, Message ID: {event.message.id}")
    user_input = event.message.text
    print(f"DEBUG: 🧾 Received user message in handle_message: '{user_input}'")

    reply_text = "目前無法回覆，請稍後再試 🧘" # 預設回覆，以防任何錯誤

    if not client: # 檢查 OpenAI 客戶端是否成功初始化
        print("ERROR: OpenAI client is not initialized. Cannot call GPT.")
    else:
        try:
            print(f"DEBUG: Calling OpenAI ChatCompletion API with model gpt-3.5-turbo for input: '{user_input}'")
            response = client.chat.completions.create( # 使用新版語法
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "你是一位口語親切的營養師助理，請以真實人類語氣回覆訊息。"},
                    {"role": "user", "content": user_input}
                ],
                temperature=0.7
            )
            print(f"DEBUG: 🎉 OpenAI API call successful. Full response: {response}")
            reply_text = response.choices[0].message.content.strip() # 使用新版語法獲取內容
            print(f"DEBUG: Generated reply text: '{reply_text}'")

        except AuthenticationError as e: # 新版錯誤類型
            print(f"ERROR: OpenAI Authentication Error: {e}. Check your API key and billing status.")
            reply_text = "GPT 驗證失敗，請檢查 API 金鑰和帳戶。🔐"
            traceback.print_exc()
        except (APIStatusError, APIConnectionError) as e: # 新版錯誤類型，捕獲 API 狀態和連接錯誤
            print(f"ERROR: OpenAI API Status/Connection Error: {e}. An issue occurred with OpenAI's servers or network.")
            reply_text = "GPT 服務暫時不穩定，請稍後再試。🌐"
            traceback.print_exc()
        except Exception as e:
            print(f"ERROR: ❌ An unexpected error occurred during GPT call: {e}.")
            traceback.print_exc()
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
        traceback.print_exc()


# 正確的 Render 啟動方式：讀取 port 並綁定 0.0.0.0
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"DEBUG: Starting Flask app on host 0.0.0.0, port {port}")
    app.run(host="0.0.0.0", port=port)
