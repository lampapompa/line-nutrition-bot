from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import openai
import os
import random

app = Flask(__name__)

# 環境變數
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))
openai.api_key = os.getenv("OPENAI_API_KEY")

# 關鍵字判斷：命中才呼叫 GPT
nutrition_keywords = [
    "吃", "飲食", "營養", "熱量", "體重", "體脂", "脂肪", "蛋白質", "碳水", "糖", "膽固醇",
    "瘦", "變瘦", "瘦下來", "瘦身", "瘦不下來", "減肥", "減重", "代謝", "消耗", "燃燒",
    "發胖", "胖了", "肥胖", "卡路里", "低卡", "控制飲食", "節食", "斷食", "升糖", "GI值",
    "便秘", "排便", "順暢", "腸道", "腸胃", "腸內", "益生菌", "腸道菌", "腸菌", "消化",
    "健康", "三高", "高血壓", "高血糖", "高血脂", "血脂", "血糖", "膽固醇", "血壓",
    "膳食纖維", "纖維", "零食", "正餐", "飲料", "炸物", "油炸", "控制", "少吃", "多喝水"
]

# 沒命中關鍵字時的 emoji 回覆
fallback_emojis = ["😊", "😄", "👍", "🙌", "🌟", "✨", "😃", "😁", "😺", "😉", "🥰"]

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"Webhook Error: {e}")
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    # 檢查是否包含關鍵字
    if not any(keyword in user_msg for keyword in nutrition_keywords):
        emoji = random.choice(fallback_emojis)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=emoji)
        )
        return

    # 呼叫 OpenAI 回應
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "你是一位專業的台灣營養師，擅長用口語化的方式回答與飲食、減肥、代謝有關的問題。"},
            {"role": "user", "content": user_msg}
        ],
        temperature=0.6,
        max_tokens=400,
    )
    reply_text = response["choices"][0]["message"]["content"].strip()

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

