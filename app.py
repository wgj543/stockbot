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

from stock import (
    get_stock_data,
    search_stock
)

app = Flask(__name__)

# ===================================
# LINE 設定
# ===================================

CHANNEL_SECRET = os.environ.get(
    "CHANNEL_SECRET",
    "LOCAL_TEST"
)

CHANNEL_ACCESS_TOKEN = os.environ.get(
    "CHANNEL_ACCESS_TOKEN",
    "LOCAL_TEST"
)

configuration = Configuration(
    access_token=CHANNEL_ACCESS_TOKEN
)

handler = WebhookHandler(
    CHANNEL_SECRET
)

# ===================================
# 首頁
# ===================================

@app.route("/")
def home():

    return "Stock Bot Running!"

# ===================================
# Webhook
# ===================================

@app.route("/callback", methods=["POST"])
def callback():

    signature = request.headers.get(
        "X-Line-Signature",
        ""
    )

    body = request.get_data(
        as_text=True
    )

    try:

        handler.handle(
            body,
            signature
        )

    except InvalidSignatureError:

        return "Invalid signature", 400

    except Exception as e:

        print(
            "Webhook Error:",
            e,
            flush=True
        )

        return "Error", 500

    return "OK"

# ===================================
# LINE 收訊息
# ===================================

@handler.add(
    MessageEvent,
    message=TextMessageContent
)
def handle_message(event):

    user_message = (
        event.message.text.strip()
    )

    print(
        f"收到訊息：{user_message}",
        flush=True
    )

    matches = search_stock(
        user_message
    )

    # 找不到

    if len(matches) == 0:

        if (
            len(user_message) < 2
            and
            not user_message.isdigit()
        ):

            reply_text = (
                "❓ 請至少輸入 2 個中文字\n"
                "或完整股票代號"
            )

        else:

            reply_text = (
                "❌ 找不到符合的股票"
            )

    # 找到唯一結果

    elif len(matches) == 1:

        code, name, stock_type = matches[0]

        stock_data = get_stock_data(
            code
        )

        if stock_data is None:

            reply_text = (
                f"❌ 查不到股票：{code}"
            )

        else:

            stock_data["type"] = stock_type

            if stock_data["type"] == "emerging":

                change_info = (
                    "⚠️ 興櫃股票（不提供漲跌幅資訊）\n\n"
                )

            else:

                change_info = (
                    f"{stock_data['trend_icon']} 漲跌："
                    f"{stock_data['change']:+.2f}\n"
                    f"{stock_data['trend_icon']} 漲跌幅："
                    f"{stock_data['change_percent']:+.2f}%\n\n"
                )

            reply_text = (
                f"📈 {name} ({code})\n\n"
                f"💰 最新價格："
                f"{stock_data['price']:.2f}\n\n"
                f"{change_info}"                
                f"🔺 今日最高："
                f"{stock_data['high']:.2f}\n"
                f"🔻 今日最低："
                f"{stock_data['low']:.2f}\n\n"
                f"📦 成交量："
                f"{stock_data['volume']/1000:,.0f} 張"
            )

    # 找到多筆

    else:

        reply_text = (
            f"找到 {len(matches)} 筆資料\n"
            f"以下顯示前20筆：\n\n"
        )

        for code, name, stock_type in matches[:20]:

            reply_text += (
                f"{code} {name}\n"
            )

        reply_text += (
            "\n請輸入股票代號繼續查詢"
        )

    try:

        with ApiClient(
            configuration
        ) as api_client:

            line_bot_api = MessagingApi(
                api_client
            )

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=
                    event.reply_token,
                    messages=[
                        TextMessage(
                            text=reply_text
                        )
                    ]
                )
            )

    except Exception as e:

        print(
            "LINE回覆錯誤:",
            e,
            flush=True
        )

# ===================================
# Render 啟動
# ===================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )