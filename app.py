import logging
import os

from flask import Flask, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from commands import handle_command
from db import init_db
from stock import get_stock_data, search_stock

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "LOCAL_TEST")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "LOCAL_TEST")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

init_db()

LINE_TEXT_LIMIT = 5000


@app.route("/")
def home():
    return "Stock Bot Running!"


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    except Exception:
        logger.exception("Webhook Error")
        return "Error", 500
    return "OK"


def build_stock_reply(user_message: str) -> str:
    """原本的單檔股價查詢邏輯（未改動行為）。"""
    matches = search_stock(user_message)

    if not matches:
        if len(user_message) < 2 and not user_message.isdigit():
            return "❓ 請至少輸入 2 個中文字\n或完整股票代號\n\n輸入「說明」查看所有指令"
        return "❌ 找不到符合的股票"

    if len(matches) == 1:
        code, name, stock_type = matches[0]
        data = get_stock_data(code, stock_type)
        if data is None:
            return f"❌ 查不到股票：{code}"

        volume_fmt = ",.2f" if data.get("market") == "ESB" else ",.0f"
        return (
            f"📈 {name} ({code})\n\n"
            f"💰 最新價格：{data['price']:.2f}\n\n"
            f"{data['trend_icon']} 漲跌：{data['change']:+.2f}\n"
            f"{data['trend_icon']} 漲跌幅：{data['change_percent']:+.2f}%\n\n"
            f"🔺 今日最高：{data['high']:.2f}\n"
            f"🔻 今日最低：{data['low']:.2f}\n\n"
            f"📦 成交量：{format(data['volume'], volume_fmt)} 張"
        )

    lines = "\n".join(f"{c} {n}" for c, n, _ in matches[:20])
    return (
        f"找到 {len(matches)} 筆資料\n以下顯示前20筆：\n\n{lines}\n\n請輸入股票代號繼續查詢"
    )


def reply(reply_token: str, text: str) -> None:
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=text[:LINE_TEXT_LIMIT])],
                )
            )
    except Exception:
        logger.exception("LINE 回覆錯誤")


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    # 群組/聊天室中 user_id 可能為 None
    user_id = getattr(event.source, "user_id", None)

    try:
        answer = handle_command(user_id, text) if user_id else None
        if answer is None:
            answer = build_stock_reply(text)
    except Exception:
        logger.exception("處理訊息失敗")
        answer = "⚠️ 系統忙碌中，請稍後再試"

    reply(event.reply_token, answer)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
