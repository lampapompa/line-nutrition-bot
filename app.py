from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import os
import random
import time

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
    except Exception as e:
        print("Webhook error:", e)
        abort(400)
    return "OK"

# GPT 判斷是否屬於營養問題
def is_nutrition_related(msg):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "你是一個盡量擬真的營養師小幫手，你自己知道你是小幫手就好，想辦法用自然語言讓使用者感覺你是真人。使用者傳來的訊息是：「" + msg + "」。這是不是一個和「營養、飲食、熱量、食物、體重、代謝、維生素」相關的問題？如果是，請你回答「是」。如果不是，請你回答「否」。"
                }
            ],
            temperature=0,
            max_tokens=5,
        )
        result = response.choices[0].message["content"].strip()
        return result == "是"
    except Exception as e:
        print("判斷錯誤：", e)
        return False

# GPT 實際擬真回答
def generate_nutrition_reply(msg):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "你是一位真誠、自然、專業的台灣營養師，回應時語氣自然口語化，像人在聊天，可以加入『嗯…』『我想想喔』等語助詞，有時候可加入 😊 😉 💡 等小 emoji。回答字數控制在 80 字內，務求自然不機器。"
                },
                {
                    "role": "user",
                    "content": msg
                }
            ],
            temperature=0.7,
            max_tokens=200,
        )
        return response.choices[0].message["content"].strip()
    except Exception as e:
        print("GPT 回應錯誤：", e)
        return "我剛剛有點斷線，等等再幫你回答好嗎？"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    print("收到訊息：", user_msg)

    if is_nutrition_related(user_msg):
        time.sleep(random.uniform(2, 4))  # 模擬思考延遲
        reply = generate_nutrition_reply(user_msg)
    else:
        emoji_pool = ["😊", "👍", "✨", "😉", "👌", "🙆‍♀️", "😄", "🍀"]
        reply = random.choice(emoji_pool)

    print("回覆訊息：", reply)
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

# 正確綁定 PORT，避免 Render 無法掃到
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
