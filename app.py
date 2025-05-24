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
            TextSendMessage(text="我是營養專屬小幫手～只回答營養相關的問題唷！")
        )
        return

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "你是一位口語化、親切有同理心的營養師，請以台灣人熟悉的口吻回覆"},
            {"role": "user", "content": user_msg}
        ],
        max_tokens=300,
        temperature=0.7
    )

    reply = response.choices[0].message.content.strip()

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
