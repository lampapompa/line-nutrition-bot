from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import os
import time
import random

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
    keywords = [
        "吃什麼", "熱量", "減肥", "體重", "肥胖", "蛋白質", "便秘", "健康", "瘦身", "脂肪",
        "三餐", "飲食", "點心", "營養", "糖分", "澱粉", "甜點", "運動", "宵夜", "水腫",
        "代謝", "腸胃", "腸道", "飢餓", "熱量缺口", "菜單", "高蛋白", "飲料", "代餐", "補品",
        "維生素", "鈣", "鐵", "益生菌", "消化", "腸道菌", "暴食", "食慾", "斷食", "低醣",
        "低脂", "高纖維", "正餐", "補充", "挑食", "空腹", "胃痛", "胃食道逆流", "腸躁症", "敏感"
    ]

    if any(word in user_msg for word in keywords):
        time.sleep(3)
        reply = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "你是營養專屬小幫手，只回答營養相關的問題，語氣親切、實用、精簡，盡量控制在50字內。"},
                {"role": "user", "content": user_msg}
            ]
        )
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply.choices[0].message.content)
        )
    else:
        emoji_list = ["💪", "😊", "✨", "👍", "🥦", "🍎", "🌟"]
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=random.choice(emoji_list))
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
