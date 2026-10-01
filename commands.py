"""LINE 文字指令解析與回覆組字。handle_command 回傳 None 代表「不是指令」。"""
import unicodedata
from decimal import Decimal, InvalidOperation
import re

import portfolio
from portfolio import MAX_PRICE, MAX_SHARES, UserError
from quotes import get_quotes
from stock import search_stock, stock_map

HELP_TEXT = (
    "📖 指令說明\n\n"
    "【庫存】\n"
    "庫存\n"
    "買 2330 1000 600.5　(代號 股數 成交價)\n"
    "買 2330 2張 600.5　(1張=1000股)\n"
    "賣 2330 500\n"
    "設定庫存 2330 1000 600　(直接修正)\n\n"
    "【待觀察】\n"
    "觀察\n"
    "加觀察 2330\n"
    "刪觀察 2330\n\n"
    "其他輸入：股票名稱或代號查詢"
)

_QTY_RE = re.compile(r"^(\d{1,9})(張|股)?$")


# ---------- 解析 ----------

def _parse_qty(token: str) -> int:
    m = _QTY_RE.match(token)
    if not m:
        raise UserError(f"數量格式錯誤：{token}（例：500 或 2張）")
    n = int(m.group(1)) * (1000 if m.group(2) == "張" else 1)
    if not 0 < n <= MAX_SHARES:
        raise UserError("數量需大於 0 且不超過 1 億股")
    return n


def _parse_price(token: str) -> Decimal:
    try:
        p = Decimal(token)
    except InvalidOperation:
        raise UserError(f"價格格式錯誤：{token}") from None
    if not p.is_finite() or not 0 < p <= MAX_PRICE:
        raise UserError("價格需大於 0 且不超過 100,000")
    return p


def _resolve(keyword: str) -> tuple[str, str]:
    """代號或名稱 → (code, name)。不唯一時請使用者改用代號。"""
    if keyword in stock_map:
        return keyword, stock_map[keyword]["name"]
    matches = search_stock(keyword)
    if not matches:
        raise UserError(f"找不到股票：{keyword}")
    exact = [m for m in matches if m[1] == keyword]
    pick = exact if len(exact) == 1 else matches
    if len(pick) == 1:
        return pick[0][0], pick[0][1]
    lines = "\n".join(f"{c} {n}" for c, n, _ in matches[:10])
    raise UserError(f"符合多筆，請改用股票代號：\n{lines}")


# ---------- 格式化 ----------

def _icon(x) -> str:
    return "🔴" if x > 0 else "🟢" if x < 0 else "⚪"  # 台股慣例：紅漲綠跌


def _fmt_shares(n: int) -> str:
    if n % 1000 == 0:
        return f"{n // 1000:,} 張"
    if n > 1000:
        return f"{n // 1000:,} 張 {n % 1000} 股"
    return f"{n:,} 股"


def _dec(x) -> Decimal:
    return Decimal(str(x))


# ---------- 指令 ----------

def _show_holdings(user_id: str, args: list[str]) -> str:
    holdings = portfolio.list_holdings(user_id)
    if not holdings:
        return "📦 目前沒有庫存\n例：買 2330 1000 600"

    quotes = get_quotes(h.code for h in holdings)
    total_cost = total_value = Decimal(0)
    missing = 0
    blocks = []

    for h in holdings:
        cost = h.avg_cost * h.shares
        q = quotes.get(h.code)
        head = f"{h.code} {h.name}\n股數：{_fmt_shares(h.shares)}\n均價：{h.avg_cost:,.2f}"
        if not q or q.get("price") is None:
            missing += 1
            blocks.append(f"{head}\n現價：暫無資料")
            continue
        price = _dec(q["price"])
        value = price * h.shares
        pnl = value - cost
        pct = pnl / cost * 100
        total_cost += cost
        total_value += value
        blocks.append(
            f"{head}\n現價：{price:,.2f}\n"
            f"{_icon(pnl)} 損益：{pnl:+,.0f}（{pct:+.2f}%）"
        )

    text = "📦 我的庫存\n\n" + "\n\n".join(blocks)
    if total_cost > 0:
        total_pnl = total_value - total_cost
        text += (
            "\n\n━━━━━━━━\n"
            f"總成本：{total_cost:,.0f}\n"
            f"總市值：{total_value:,.0f}\n"
            f"{_icon(total_pnl)} 總損益：{total_pnl:+,.0f}（{total_pnl / total_cost * 100:+.2f}%）"
        )
    if missing:
        text += f"\n\n⚠️ {missing} 檔查價失敗，未計入總計"
    text += "\n\n※ 未含手續費與證交稅"
    return text


def _buy(user_id: str, args: list[str]) -> str:
    if len(args) != 3:
        raise UserError("格式：買 代號 數量 成交價\n例：買 2330 1000 600.5")
    code, name = _resolve(args[0])
    h = portfolio.buy(user_id, code, name, _parse_qty(args[1]), _parse_price(args[2]))
    return (
        f"✅ 已買入 {name}({code})\n"
        f"目前持有：{_fmt_shares(h.shares)}\n"
        f"平均成本：{h.avg_cost:,.2f}"
    )


def _sell(user_id: str, args: list[str]) -> str:
    if len(args) != 2:
        raise UserError("格式：賣 代號 數量\n例：賣 2330 500")
    code, name = _resolve(args[0])
    remaining, avg = portfolio.sell(user_id, code, _parse_qty(args[1]))
    if remaining == 0:
        return f"✅ 已賣出 {name}({code})，庫存已清空"
    return f"✅ 已賣出 {name}({code})\n剩餘：{_fmt_shares(remaining)}\n平均成本：{avg:,.2f}"


def _set_holding(user_id: str, args: list[str]) -> str:
    if len(args) != 3:
        raise UserError("格式：設定庫存 代號 數量 均價\n例：設定庫存 2330 1000 600")
    code, name = _resolve(args[0])
    h = portfolio.set_holding(user_id, code, name, _parse_qty(args[1]), _parse_price(args[2]))
    return f"✅ 已設定 {name}({code})\n持有：{_fmt_shares(h.shares)}\n平均成本：{h.avg_cost:,.2f}"


def _show_watch(user_id: str, args: list[str]) -> str:
    items = portfolio.list_watch(user_id)
    if not items:
        return "👀 待觀察名單是空的\n例：加觀察 2330"

    quotes = get_quotes(w.code for w in items)
    blocks = []
    for w in items:
        q = quotes.get(w.code)
        if not q or q.get("price") is None:
            blocks.append(f"⚪ {w.code} {w.name}\n   暫無資料")
            continue
        vol_fmt = ",.2f" if q.get("market") == "ESB" else ",.0f"
        blocks.append(
            f"{q['trend_icon']} {w.code} {w.name}\n"
            f"   現價 {q['price']:.2f}　{q['change']:+.2f}（{q['change_percent']:+.2f}%）\n"
            f"   高 {q['high']:.2f}　低 {q['low']:.2f}　量 {format(q['volume'], vol_fmt)} 張"
        )
    return "👀 待觀察名單\n\n" + "\n\n".join(blocks)


def _add_watch(user_id: str, args: list[str]) -> str:
    if len(args) != 1:
        raise UserError("格式：加觀察 代號\n例：加觀察 2330")
    code, name = _resolve(args[0])
    if portfolio.add_watch(user_id, code, name):
        return f"✅ 已加入觀察：{name}({code})"
    return f"ℹ️ {name}({code}) 已在名單中"


def _remove_watch(user_id: str, args: list[str]) -> str:
    if len(args) != 1:
        raise UserError("格式：刪觀察 代號\n例：刪觀察 2330")
    code, name = _resolve(args[0])
    if portfolio.remove_watch(user_id, code):
        return f"✅ 已移除觀察：{name}({code})"
    return f"ℹ️ {name}({code}) 不在名單中"


_ROUTES = {
    "庫存": _show_holdings,
    "買": _buy,
    "賣": _sell,
    "設定庫存": _set_holding,
    "觀察": _show_watch,
    "加觀察": _add_watch,
    "刪觀察": _remove_watch,
}


def handle_command(user_id: str, text: str) -> str | None:
    # NFKC：全形數字/空白 → 半形，避免手機輸入法造成解析失敗
    tokens = unicodedata.normalize("NFKC", text).split()
    if not tokens:
        return None
    if tokens[0].lower() in ("說明", "help", "指令"):
        return HELP_TEXT
    route = _ROUTES.get(tokens[0])
    if route is None:
        return None
    try:
        return route(user_id, tokens[1:])
    except UserError as e:
        return f"❌ {e}"
