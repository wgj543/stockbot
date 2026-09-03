from flask import Flask, request

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError

from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)

from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
)

import os
import yfinance as yf

app = Flask(__name__)

CHANNEL_SECRET = "68da1e85e278a1e13953321c072fa01f"
CHANNEL_ACCESS_TOKEN = "H9L7qLjzOJLfFZ8G6XktZe5VAtyg5hpTI3ruNewMe2wydcGFS/JNYf3isfw7M+Ws8W22GAeKrmP2BGC0cIR/x5Yod0xa1/Amq4AO4EDGClFZhl3xfxjxk9GnE6bVbI32UIdqBJdYv6tdwEbF16WrRAdB04t89/1O/w1cDnyilFU="

configuration = Configuration(
    access_token=CHANNEL_ACCESS_TOKEN
)

handler = WebhookHandler(CHANNEL_SECRET)


@app.route("/")
def home():
    return "Stock Bot Running!"


@app.route("/callback", methods=["POST"])
def callback():

    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):

    user_message = event.message.text

    try:
        stock = yf.Ticker(f"{user_message}.TW")

        data = stock.history(period="5d")

        if data.empty:
            raise Exception("查無股票資料")

        price = data["Close"].dropna().iloc[-1]

        reply_text = (
            f"股票代號：{user_message}\n"
            f"收盤價：{price:.2f}"
        )

    except Exception as e:
        reply_text = f"錯誤：{str(e)}"

    with ApiClient(configuration) as api_client:

        line_bot_api = MessagingApi(api_client)

        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(text=reply_text)
                ]
            )
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)