import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 環境變數：從 Render 或 .env 自動抓取
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# 健康檢查用（Render 會先 ping "/"，不寫會判定你沒開服務）
@app.route("/", methods=['GET'])
def home():
    return "OK", 200

# LINE Webhook 專用路徑
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

# 當收到「文字訊息」時觸發這裡（Echo 回傳同樣訊息）
import openai  # ← 確保這行放在最上面

openai.api_key = os.getenv("OPENAI_API_KEY")  # ← 放在第 7 行 app = Flask(__name__) 下面

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text
    print("🧾 收到使用者訊息：", user_input)

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "你是一位口語親切的營養師助理，請以真實人類語氣回覆訊息。"},
                {"role": "user", "content": user_input}
            ],
            temperature=0.7
        )
        reply_text = response['choices'][0]['message']['content'].strip()
    except Exception as e:
        print("❌ GPT 呼叫失敗：", e)
        reply_text = "目前無法回覆，請稍後再試 🧘"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


# 正確的 Render 啟動方式：讀取 port 並綁定 0.0.0.0
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
