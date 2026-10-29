"""Generate a daily candlestick chart for LINE and keep its image URLs temporarily available."""
from __future__ import annotations

import io
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

from stock import FUGLE_API_KEY

logger = logging.getLogger(__name__)
_TAIPEI = ZoneInfo("Asia/Taipei")
_API_URL = "https://api.fugle.tw/marketdata/v1.0/stock"
_HTTP_TIMEOUT = (3.05, 8)
_HISTORY_TTL_SECONDS = 6 * 60 * 60
_CHART_TTL_SECONDS = 10 * 60
_MAX_CHARTS = 256

_history_cache: dict[str, tuple[float, list[dict]]] = {}
_chart_cache: dict[str, tuple[float, bytes, bytes]] = {}
_cache_lock = threading.Lock()


def _font(size: int = 18) -> ImageFont.ImageFont:
    """Use a standard font; chart labels intentionally use ASCII for portability."""
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def fetch_current_quote(stock_code: str) -> dict | None:
    """Fetch the uncached Fugle intraday quote used by both reply text and chart."""
    if not FUGLE_API_KEY:
        logger.warning("Current quote unavailable: FUGLE_API_KEY is not configured")
        return None

    url = f"{_API_URL}/intraday/quote/{stock_code}"
    try:
        response = requests.get(
            url, headers={"X-API-KEY": FUGLE_API_KEY}, timeout=(2.05, 5)
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Fugle intraday quote failed for %s: %s", stock_code, exc)
        return None

    if not isinstance(payload, dict) or _number(payload.get("lastPrice")) is None:
        logger.warning("Fugle intraday quote returned invalid data for %s", stock_code)
        return None
    return payload


def normalize_quote_for_reply(stock_code: str, payload: dict | None) -> dict | None:
    """Normalize Fugle's raw quote into the fields used by the existing text reply."""
    if not isinstance(payload, dict) or _number(payload.get("lastPrice")) is None:
        return None
    try:
        price = float(payload["lastPrice"])
        change = float(payload.get("change") or 0)
        change_percent = float(payload.get("changePercent") or 0)
        total = payload.get("total") if isinstance(payload.get("total"), dict) else {}
        volume = float(total.get("tradeVolume") or 0)
        market = payload.get("market")
        if market == "ESB":
            volume = round(volume / 1000, 2)
        return {
            "code": stock_code,
            "price": price,
            "high": float(payload.get("highPrice") or price),
            "low": float(payload.get("lowPrice") or price),
            "change": change,
            "change_percent": change_percent,
            "volume": volume,
            "market": market,
            "trend_icon": "🔴" if change > 0 else "🟢" if change < 0 else "⚪",
        }
    except (TypeError, ValueError, KeyError):
        logger.exception("Unable to normalize Fugle quote for %s", stock_code)
        return None


def _fetch_history(stock_code: str) -> list[dict] | None:
    now = time.monotonic()
    with _cache_lock:
        cached = _history_cache.get(stock_code)
        if cached and now - cached[0] < _HISTORY_TTL_SECONDS:
            return [dict(row) for row in cached[1]]

    if not FUGLE_API_KEY:
        logger.warning("Chart unavailable: FUGLE_API_KEY is not configured")
        return None

    today = datetime.now(_TAIPEI).date()
    # 120 calendar days generally provides far more than the 20 sessions needed for MA20.
    start_date = today - timedelta(days=120)
    url = f"{_API_URL}/historical/candles/{stock_code}"
    params = {
        "from": start_date.isoformat(),
        "to": today.isoformat(),
        "timeframe": "D",
        "fields": "open,high,low,close,volume",
    }
    try:
        response = requests.get(
            url, headers={"X-API-KEY": FUGLE_API_KEY}, params=params,
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            logger.warning("Fugle historical candles returned non-object data for %s", stock_code)
            return None
        raw_rows = payload.get("data", [])
        if not isinstance(raw_rows, list):
            logger.warning("Fugle historical candles returned unexpected data for %s", stock_code)
            return None
        rows = []
        for item in raw_rows:
            if not isinstance(item, dict):
                continue
            date = str(item.get("date", ""))[:10]
            open_price = _number(item.get("open"))
            high = _number(item.get("high"))
            low = _number(item.get("low"))
            close = _number(item.get("close"))
            volume = _number(item.get("volume")) or 0.0
            if not date or any(v is None or v <= 0 for v in (open_price, high, low, close)):
                continue
            rows.append({
                "date": date, "open": open_price, "high": high,
                "low": low, "close": close, "volume": max(0.0, volume),
            })
        rows.sort(key=lambda row: row["date"])
        if not rows:
            logger.warning("No valid historical candles for %s", stock_code)
            return None
        with _cache_lock:
            _history_cache[stock_code] = (time.monotonic(), [dict(row) for row in rows])
        return rows
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("Historical chart data fetch failed for %s: %s", stock_code, exc)
        return None


def _daily_rows(stock_code: str, quote: dict) -> list[dict] | None:
    rows = _fetch_history(stock_code)
    if not rows:
        return None

    # Replace an incomplete/current-day historical row with the latest quote fields.
    quote_date = str(quote.get("date", ""))[:10]
    last = _number(quote.get("lastPrice")) or _number(quote.get("closePrice"))
    open_price = _number(quote.get("openPrice"))
    high = _number(quote.get("highPrice"))
    low = _number(quote.get("lowPrice"))
    total = quote.get("total") if isinstance(quote.get("total"), dict) else {}
    volume = _number(total.get("tradeVolume"))

    # A valid current-session OHLC range prevents placeholders or stale candles being shown as current.
    if not (quote_date and last and open_price and high and low and high >= low and volume is not None):
        logger.warning("Current quote lacks a usable OHLC range for %s; chart omitted", stock_code)
        return None

    if quote_date and last and open_price and high and low and high >= low and volume is not None:
        todays_bar = {
            "date": quote_date,
            "open": open_price,
            "high": max(high, open_price, last),
            "low": min(low, open_price, last),
            "close": last,
            "volume": max(0.0, volume),
        }
        rows = [row for row in rows if row["date"] != quote_date]
        rows.append(todays_bar)
        rows.sort(key=lambda row: row["date"])

    # Keep the most recent 60 sessions while retaining enough history for all requested MAs.
    return rows[-60:]


def _moving_average(rows: list[dict], period: int) -> list[float | None]:
    values = [row["close"] for row in rows]
    averages: list[float | None] = []
    for idx in range(len(values)):
        if idx + 1 < period:
            averages.append(None)
        else:
            window = values[idx + 1 - period:idx + 1]
            averages.append(sum(window) / period)
    return averages



def _select_visible_moving_averages(
    rows: list[dict], axis_low: float, axis_high: float, max_deviation: float = 0.15
) -> tuple[list[tuple[int, str, list[float | None]]], list[int]]:
    """Hide an MA that would sit far away from the latest candle or outside the price axis."""
    visible: list[tuple[int, str, list[float | None]]] = []
    hidden: list[int] = []
    latest_close = rows[-1]["close"]
    for period, color in [(5, "#f5c451"), (10, "#44d0d0"), (20, "#c58aff")]:
        averages = _moving_average(rows, period)
        latest_ma = next((value for value in reversed(averages) if value is not None), None)
        if latest_ma is None:
            hidden.append(period)
            continue
        deviation = abs(latest_ma - latest_close) / latest_close if latest_close else 0.0
        if latest_ma < axis_low or latest_ma > axis_high or deviation > max_deviation:
            hidden.append(period)
            continue
        visible.append((period, color, averages))
    return visible, hidden

def _render_chart(stock_code: str, rows: list[dict], quote: dict) -> tuple[bytes, bytes]:
    # Draw the price area independently of moving averages so a distant MA cannot squash candles.
    width, height = 1200, 900
    image = Image.new("RGB", (width, height), "#0b0f14")
    draw = ImageDraw.Draw(image)
    title_font = _font(28)
    label_font = _font(17)
    small_font = _font(14)

    draw.text((46, 28), f"{stock_code}  |  DAILY K", font=title_font, fill="#f0f4f8")
    now = datetime.now(_TAIPEI).strftime("%Y-%m-%d %H:%M:%S Asia/Taipei")
    draw.text((48, 68), f"Latest available quote | Retrieved {now}", font=small_font, fill="#a8b4c0")

    left, right = 88, width - 38
    price_top, price_bottom = 120, 588
    vol_top, vol_bottom = 650, 805
    plot_width = right - left
    count = len(rows)
    slot = plot_width / max(count, 1)
    candle_width = max(3, min(12, int(slot * 0.55)))

    visible_low = min(row["low"] for row in rows)
    visible_high = max(row["high"] for row in rows)
    spread = max(visible_high - visible_low, visible_high * 0.015, 0.01)
    axis_low = visible_low - spread * 0.08
    axis_high = visible_high + spread * 0.08
    if axis_high <= axis_low:
        axis_high = axis_low + 1

    def y_price(value: float) -> float:
        return price_bottom - (value - axis_low) / (axis_high - axis_low) * (price_bottom - price_top)

    # Price grid and scale.
    for i in range(6):
        y = price_top + i * (price_bottom - price_top) / 5
        value = axis_high - i * (axis_high - axis_low) / 5
        draw.line((left, y, right, y), fill="#28323d", width=1)
        draw.text((8, int(y - 10)), f"{value:.2f}", font=label_font, fill="#9eabb7")
    for i in range(0, count, max(1, count // 6)):
        x = left + (i + 0.5) * slot
        draw.line((x, price_top, x, vol_bottom), fill="#1a232d", width=1)
        draw.text((int(x - 32), 610), rows[i]["date"][5:], font=small_font, fill="#8f9ba7")

    # Keep the price axis focused on candles; hide an MA if its latest value is >15% away.
    ma_drawn, ma_hidden = _select_visible_moving_averages(rows, axis_low, axis_high)

    # Taiwan market convention: red up candles, green down candles.
    max_volume = max((row["volume"] for row in rows), default=0.0) or 1.0
    for idx, row in enumerate(rows):
        x = left + (idx + 0.5) * slot
        y_open = y_price(row["open"])
        y_close = y_price(row["close"])
        y_high = y_price(row["high"])
        y_low = y_price(row["low"])
        candle_color = "#ff525d" if row["close"] >= row["open"] else "#26c281"
        draw.line((x, y_high, x, y_low), fill=candle_color, width=2)
        top = min(y_open, y_close)
        bottom = max(y_open, y_close)
        if bottom - top < 2:
            bottom = top + 2
        draw.rectangle((x - candle_width / 2, top, x + candle_width / 2, bottom), fill=candle_color)
        vol_height = row["volume"] / max_volume * (vol_bottom - vol_top)
        draw.rectangle((x - candle_width / 2, vol_bottom - vol_height, x + candle_width / 2, vol_bottom), fill=candle_color)

    # Draw averages on top of candles, as users expect from a technical chart.
    legend_x, legend_y = left, 102
    draw.rectangle((left, 92, right, 117), fill="#0b0f14")
    for period, color, averages in ma_drawn:
        points = [
            (left + (idx + 0.5) * slot, y_price(avg))
            for idx, avg in enumerate(averages) if avg is not None
        ]
        if len(points) >= 2:
            draw.line(points, fill=color, width=3, joint="curve")
        draw.line((legend_x, legend_y + 8, legend_x + 24, legend_y + 8), fill=color, width=3)
        draw.text((legend_x + 30, legend_y), f"MA{period}", font=small_font, fill=color)
        legend_x += 95
    for period in ma_hidden:
        draw.text((legend_x, legend_y), f"MA{period} hidden (divergent)", font=small_font, fill="#78838e")
        legend_x += 165

    draw.line((left, vol_top, right, vol_top), fill="#34414d", width=1)
    draw.text((left, 625), "VOLUME", font=small_font, fill="#a8b4c0")

    latest = rows[-1]
    latest_quote_price = _number(quote.get("lastPrice")) or latest["close"]
    draw.text((left, 830), f"Last {latest_quote_price:.2f}   Candle date {latest['date']}   Bars {count}", font=label_font, fill="#f0f4f8")
    draw.text((left, 860), "Intraday candle uses the latest available quote; moving averages use daily closes.", font=small_font, fill="#8f9ba7")

    original = io.BytesIO()
    image.save(original, format="PNG", optimize=True)
    preview_image = image.copy()
    preview_image.thumbnail((960, 720), Image.Resampling.LANCZOS)
    preview = io.BytesIO()
    preview_image.save(preview, format="PNG", optimize=True)
    return original.getvalue(), preview.getvalue()


def create_stock_chart(stock_code: str, quote: dict) -> str | None:
    """Build a chart and return an opaque, short-lived ID for public image endpoints."""
    try:
        rows = _daily_rows(stock_code, quote)
        if not rows:
            return None
        original, preview = _render_chart(stock_code, rows, quote)
        chart_id = uuid.uuid4().hex
        now = time.monotonic()
        with _cache_lock:
            expired = [key for key, value in _chart_cache.items() if value[0] <= now]
            for key in expired:
                _chart_cache.pop(key, None)
            while len(_chart_cache) >= _MAX_CHARTS:
                oldest = min(_chart_cache, key=lambda key: _chart_cache[key][0])
                _chart_cache.pop(oldest, None)
            _chart_cache[chart_id] = (now + _CHART_TTL_SECONDS, original, preview)
        return chart_id
    except Exception:
        logger.exception("Failed to create stock chart for %s", stock_code)
        return None


def get_chart_image(chart_id: str, variant: str) -> bytes | None:
    if variant not in {"original", "preview"}:
        return None
    now = time.monotonic()
    with _cache_lock:
        entry = _chart_cache.get(chart_id)
        if not entry:
            return None
        expires, original, preview = entry
        if expires <= now:
            _chart_cache.pop(chart_id, None)
            return None
        return original if variant == "original" else preview
