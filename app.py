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
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text
    print("📩 收到使用者訊息：", text)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"你說的是：「{text}」")
    )

# 正確的 Render 啟動方式：讀取 port 並綁定 0.0.0.0
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
