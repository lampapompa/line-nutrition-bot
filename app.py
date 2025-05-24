import os
import openai
import random
import time
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 設定密鑰
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
openai.api_key = os.getenv('OPENAI_API_KEY')
print("🔑 OpenAI API key:", openai.api_key)

# 健康檢查路由
@app.route('/')
def health_check():
    return 'OK', 200

# Webhook 路由
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

# GPT 回應邏輯
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    print("📦 收到的 event 是：", event)
    user_msg = event.message.text


    # 呼叫 GPT 模型直接回應
    print("⚡️ Calling OpenAI GPT with message:", user_msg)
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "你是一位口語親切的營養師助理，請以真實人類語氣回覆訊息。"},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.7,
            max_tokens=500
        )
        reply = response['choices'][0]['message']['content'].strip()
    except Exception as e:
        print("❌ GPT 呼叫失敗：", e)
        reply = "目前無法回覆，請稍後再試 🧎"

        
        print("✅ GPT reply:", reply)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )


# 啟動服務，確保綁定 port（如 render 預設會給 PORT 環境變數）
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
