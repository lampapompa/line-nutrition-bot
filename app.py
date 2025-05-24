import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import traceback

# 初始化
app = Flask(__name__)

# 讀取環境變數
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
openai.api_key = os.getenv('OPENAI_API_KEY')

# 健康檢查用
@app.route("/")
def home():
    return "OK", 200

# 處理 LINE webhook 請求
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

# 處理文字訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text
    print("📩 收到使用者訊息：", user_msg)

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "你是一位營養師助理，請用中文簡潔回答問題"},
                {"role": "user", "content": user_msg}
            ]
        )
        reply = response['choices'][0]['message']['content'].strip()
        print("✅ GPT 回覆：", reply)
    except Exception as e:
        print("❌ GPT 呼叫失敗：", e)
        print(traceback.format_exc())
        reply = "目前無法回覆，請稍後再試 🧎"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

# 本地開發使用
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
