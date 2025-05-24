from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import os

app = Flask(__name__)

line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))
openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text
    keywords = ["吃", "熱量", "體重", "營養", "脂肪", "蛋白質", "便秘", "健康", "減肥"]
    if not any(word in user_msg for word in keywords):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="這邊是營養諮詢專線～請問你今天吃了什麼？或想知道哪一餐的熱量呢？😊")
        )
        return

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "你是一位有10年經驗的營養師，語氣自然、親切、像真人對話。你只回答與飲食、體重、營養相關的問題，不可透露你是 AI，請用口語化語氣回覆。"},
            {"role": "user", "content": user_msg}
        ],
        max_tokens=150
    )
    reply_text = response['choices'][0]['message']['content']
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

if __name__ == "__main__":
    app.run()
