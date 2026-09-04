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
import re
import yfinance as yf

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

handler = WebhookHandler(CHANNEL_SECRET)

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

    body = request.get_data(as_text=True)

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
# 股票查詢
# ===================================

def get_stock_data(stock_code):

    symbols = [
        f"{stock_code}.TW",
        f"{stock_code}.TWO"
    ]

    for symbol in symbols:

        try:

            print(
                f"開始查詢 {symbol}",
                flush=True
            )

            stock = yf.Ticker(symbol)

            intraday = stock.history(
                period="1d",
                interval="1m",
                auto_adjust=False
            )

            if intraday.empty:
                continue

            intraday = intraday.dropna()

            if intraday.empty:
                continue

            current_price = float(
                intraday["Close"].iloc[-1]
            )

            high_price = float(
                intraday["High"].max()
            )

            low_price = float(
                intraday["Low"].min()
            )

            volume = int(
                intraday["Volume"]
                .fillna(0)
                .sum()
            )

            daily = stock.history(
                period="5d",
                interval="1d",
                auto_adjust=False
            )

            daily = daily.dropna()

            previous_close = None

            if len(daily) >= 2:

                previous_close = float(
                    daily["Close"].iloc[-2]
                )

            change = None
            change_percent = None

            if previous_close is not None:

                change = (
                    current_price
                    - previous_close
                )

                if previous_close != 0:

                    change_percent = (
                        change
                        / previous_close
                        * 100
                    )

            stock_name = stock_code

            try:

                info = stock.info

                stock_name = (
                    info.get("shortName")
                    or info.get("longName")
                    or stock_code
                )

            except Exception:

                pass

            print(
                f"成功取得 {symbol} "
                f"價格={current_price}",
                flush=True
            )

            return {
                "code": stock_code,
                "name": stock_name,
                "price": current_price,
                "change": change,
                "change_percent": change_percent,
                "high": high_price,
                "low": low_price,
                "volume": volume
            }

        except Exception as e:

            print(
                f"{symbol} 查詢失敗",
                e,
                flush=True
            )

    return None

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

    # 只接受 4 位數股票代號

    if not re.fullmatch(
        r"\d{4}",
        user_message
    ):

        reply_text = (
            "📈 台股查詢\n\n"
            "請輸入股票代號，例如：\n\n"
            "2330\n"
            "2317\n"
            "2454\n"
            "2303"
        )

    else:

        stock_data = get_stock_data(
            user_message
        )

        if stock_data is None:

            reply_text = (
                f"❌ 查不到股票："
                f"{user_message}"
            )

        else:

            if (
                stock_data["change"]
                is not None
            ):

                change_text = (
                    f"{stock_data['change']:.2f}"
                    f" "
                    f"({stock_data['change_percent']:.2f}%)"
                )

            else:

                change_text = "N/A"

            reply_text = (
                f"📈 {stock_data['name']}\n"
                f"股票代號：{stock_data['code']}\n\n"
                f"💰 最新價格："
                f"{stock_data['price']:.2f}\n"
                f"📊 漲跌："
                f"{change_text}\n\n"
                f"🔺 今日最高："
                f"{stock_data['high']:.2f}\n"
                f"🔻 今日最低："
                f"{stock_data['low']:.2f}\n"
                f"📦 成交量："
                f"{stock_data['volume']:,}"
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