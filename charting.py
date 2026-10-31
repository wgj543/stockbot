"""Build intraday line charts for LINE using Fugle minute candles."""
from __future__ import annotations

import io
import logging
import os
import threading
import time
import uuid
from datetime import date, datetime, time as datetime_time, timedelta
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
_SESSION_OPEN = datetime_time(9, 0)
_SESSION_CLOSE = datetime_time(13, 30)
_SESSION_MINUTES = 270
_MAX_INTRADAY_MA_DEVIATION = 0.05

_history_cache: dict[str, tuple[float, list[dict]]] = {}
_chart_cache: dict[str, tuple[float, bytes, bytes]] = {}
_cache_lock = threading.Lock()


def _font(size: int = 18) -> ImageFont.ImageFont:
    """Load a common font available on Render or Windows."""
    candidates = (
        "NotoSansCJKtc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _parse_fugle_datetime(value) -> datetime | None:
    """Parse Fugle ISO strings or Unix timestamps (microseconds/milliseconds)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric <= 0:
            return None
        if numeric >= 1e14:
            numeric /= 1_000_000.0
        elif numeric >= 1e11:
            numeric /= 1_000.0
        try:
            return datetime.fromtimestamp(numeric, tz=_TAIPEI)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_TAIPEI)
        return parsed.astimezone(_TAIPEI)
    return None


def fetch_current_quote(stock_code: str) -> dict | None:
    """Fetch an uncached Fugle quote; this remains the source for the live price."""
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
    """Normalize Fugle's raw quote into fields used by the existing text reply."""
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
    """Fetch/cache daily closes used only to calculate MA5/MA10/MA20 references."""
    now = time.monotonic()
    with _cache_lock:
        cached = _history_cache.get(stock_code)
        if cached and now - cached[0] < _HISTORY_TTL_SECONDS:
            return [dict(row) for row in cached[1]]

    if not FUGLE_API_KEY:
        logger.warning("Daily MA data unavailable: FUGLE_API_KEY is not configured")
        return None
    today = datetime.now(_TAIPEI).date()
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
        if not isinstance(payload, dict) or not isinstance(payload.get("data", []), list):
            logger.warning("Fugle historical candles returned unexpected data for %s", stock_code)
            return None
        rows = []
        for item in payload.get("data", []):
            if not isinstance(item, dict):
                continue
            row_date = str(item.get("date", ""))[:10]
            open_price = _number(item.get("open"))
            high = _number(item.get("high"))
            low = _number(item.get("low"))
            close = _number(item.get("close"))
            volume = _number(item.get("volume")) or 0.0
            if not row_date or any(v is None or v <= 0 for v in (open_price, high, low, close)):
                continue
            rows.append({
                "date": row_date, "open": open_price, "high": high,
                "low": low, "close": close, "volume": max(0.0, volume),
            })
        rows.sort(key=lambda row: row["date"])
        if not rows:
            logger.warning("No valid historical daily candles for %s", stock_code)
            return None
        with _cache_lock:
            _history_cache[stock_code] = (time.monotonic(), [dict(row) for row in rows])
        return rows
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("Historical MA data fetch failed for %s: %s", stock_code, exc)
        return None


def _daily_rows(stock_code: str, quote: dict) -> list[dict] | None:
    """Compatibility helper retained for the existing daily-volume/MA tests."""
    rows = _fetch_history(stock_code)
    if not rows:
        return None
    rows = [
        {**row, "volume": max(0.0, _number(row.get("volume")) or 0.0) / 1000.0}
        for row in rows
    ]
    quote_date = str(quote.get("date", ""))[:10]
    last = _number(quote.get("lastPrice")) or _number(quote.get("closePrice"))
    open_price = _number(quote.get("openPrice"))
    high = _number(quote.get("highPrice"))
    low = _number(quote.get("lowPrice"))
    total = quote.get("total") if isinstance(quote.get("total"), dict) else {}
    volume = _number(total.get("tradeVolume"))
    if volume is not None and quote.get("market") == "ESB":
        volume /= 1000.0
    if not (quote_date and last and open_price and high and low and high >= low and volume is not None):
        logger.warning("Current quote lacks a usable OHLC range for %s", stock_code)
        return None
    today_bar = {
        "date": quote_date,
        "open": open_price,
        "high": max(high, open_price, last),
        "low": min(low, open_price, last),
        "close": last,
        "volume": max(0.0, volume),
    }
    rows = [row for row in rows if row["date"] != quote_date]
    rows.append(today_bar)
    rows.sort(key=lambda row: row["date"])
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
    """Compatibility helper retained for existing tests and daily MA calculations."""
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


def _quote_time_label(quote: dict) -> str:
    timestamp = quote.get("lastUpdated")
    if timestamp is None and isinstance(quote.get("lastTrade"), dict):
        timestamp = quote["lastTrade"].get("time")
    if timestamp is None and isinstance(quote.get("total"), dict):
        timestamp = quote["total"].get("time")
    updated_at = _parse_fugle_datetime(timestamp)
    if updated_at is None:
        retrieved_at = datetime.now(_TAIPEI)
        return f"Quote timestamp unavailable | Retrieved {retrieved_at:%Y-%m-%d %H:%M:%S} Asia/Taipei"
    return f"Quote updated {updated_at:%Y-%m-%d %H:%M:%S} Asia/Taipei"


def _fetch_intraday_candles(stock_code: str, quote: dict) -> list[dict] | None:
    """Fetch fresh one-minute candles for the current trading session (never cached)."""
    if not FUGLE_API_KEY:
        logger.warning("Intraday chart unavailable: FUGLE_API_KEY is not configured")
        return None
    url = f"{_API_URL}/intraday/candles/{stock_code}"
    params = {"timeframe": "1", "sort": "asc"}
    try:
        response = requests.get(
            url, headers={"X-API-KEY": FUGLE_API_KEY}, params=params,
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Fugle one-minute candles failed for %s: %s", stock_code, exc)
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        logger.warning("Fugle one-minute candles returned unexpected data for %s", stock_code)
        return None

    target_date = str(quote.get("date") or payload.get("date") or "")[:10]
    market = quote.get("market") or payload.get("market")
    results_by_minute: dict[str, dict] = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        candle_time = _parse_fugle_datetime(item.get("date"))
        close = _number(item.get("close"))
        if candle_time is None or close is None or close <= 0:
            continue
        if target_date and candle_time.date().isoformat() != target_date:
            continue
        if not (_SESSION_OPEN <= candle_time.time().replace(tzinfo=None) <= _SESSION_CLOSE):
            continue
        volume = max(0.0, _number(item.get("volume")) or 0.0)
        # Fugle minute-candle volume is lots for regular board lots, but shares for ESB.
        if market == "ESB":
            volume /= 1000.0
        average = _number(item.get("average"))
        high = _number(item.get("high")) or close
        low = _number(item.get("low")) or close
        key = candle_time.replace(second=0, microsecond=0).isoformat()
        results_by_minute[key] = {
            "time": candle_time,
            "price": close,
            "average": average,
            "volume": volume,
            "high": max(high, close),
            "low": min(low, close),
        }
    rows = sorted(results_by_minute.values(), key=lambda row: row["time"])
    if not rows:
        logger.warning("No current-session one-minute candles for %s", stock_code)
        return []
    # Carry the most recent available average forward if a candle omits the field.
    last_average = _number(quote.get("avgPrice"))
    for row in rows:
        if row["average"] is not None:
            last_average = row["average"]
        elif last_average is not None:
            row["average"] = last_average
    return rows


def _merge_latest_quote(points: list[dict], quote: dict) -> list[dict]:
    """Append/update the final line point with the latest quote without faking volume."""
    if not points:
        return []
    merged = [dict(point) for point in points]
    last_price = _number(quote.get("lastPrice")) or _number(quote.get("closePrice"))
    if last_price is None or last_price <= 0:
        return merged
    last_trade = quote.get("lastTrade") if isinstance(quote.get("lastTrade"), dict) else {}
    quote_time = _parse_fugle_datetime(last_trade.get("time")) or _parse_fugle_datetime(quote.get("lastUpdated"))
    if quote_time is None:
        merged[-1]["price"] = last_price
        return merged
    quote_date = str(quote.get("date", ""))[:10]
    if quote_date and quote_time.date().isoformat() != quote_date:
        return merged
    if quote_time.time().replace(tzinfo=None) < _SESSION_OPEN:
        return merged
    if quote_time.time().replace(tzinfo=None) > _SESSION_CLOSE:
        quote_time = quote_time.replace(
            hour=_SESSION_CLOSE.hour, minute=_SESSION_CLOSE.minute,
            second=0, microsecond=0,
        )
    latest_candle_minute = merged[-1]["time"].replace(second=0, microsecond=0)
    quote_minute = quote_time.replace(second=0, microsecond=0)
    if quote_minute == latest_candle_minute:
        merged[-1]["price"] = last_price
        merged[-1]["high"] = max(merged[-1].get("high", last_price), last_price)
        merged[-1]["low"] = min(merged[-1].get("low", last_price), last_price)
        avg_price = _number(quote.get("avgPrice"))
        if avg_price is not None:
            merged[-1]["average"] = avg_price
    elif quote_time >= merged[-1]["time"]:
        merged.append({
            "time": quote_time,
            "price": last_price,
            "average": _number(quote.get("avgPrice")) or merged[-1].get("average"),
            "volume": 0.0,
            "high": last_price,
            "low": last_price,
        })
    return merged


def _daily_ma_levels(stock_code: str, quote: dict) -> dict[int, float]:
    """Calculate provisional MA5/10/20 using prior daily closes plus today's last price."""
    history = _fetch_history(stock_code)
    if not history:
        return {}
    quote_date = str(quote.get("date") or datetime.now(_TAIPEI).date().isoformat())[:10]
    last_price = _number(quote.get("lastPrice")) or _number(quote.get("closePrice"))
    if last_price is None or last_price <= 0:
        return {}
    completed_closes = [
        row["close"] for row in history
        if str(row.get("date", ""))[:10] < quote_date and _number(row.get("close")) is not None
    ]
    levels: dict[int, float] = {}
    for period in (5, 10, 20):
        if len(completed_closes) >= period - 1:
            sample = completed_closes[-(period - 1):] + [last_price]
            levels[period] = sum(sample) / period
    return levels


def _visible_intraday_ma_levels(
    ma_levels: dict[int, float], latest_price: float,
    max_deviation: float = _MAX_INTRADAY_MA_DEVIATION,
) -> tuple[dict[int, float], list[int]]:
    visible: dict[int, float] = {}
    hidden: list[int] = []
    for period in (5, 10, 20):
        value = ma_levels.get(period)
        if value is None or latest_price <= 0:
            hidden.append(period)
            continue
        if abs(value - latest_price) / latest_price > max_deviation:
            hidden.append(period)
            continue
        visible[period] = value
    return visible, hidden


def _draw_dashed_line(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], fill: str, width: int = 2) -> None:
    x1, y, x2, _ = xy
    segment, gap = 12, 8
    x = x1
    while x < x2:
        end = min(x + segment, x2)
        draw.line((x, y, end, y), fill=fill, width=width)
        x = end + gap


def _render_chart(
    stock_code: str, points: list[dict], quote: dict, ma_levels: dict[int, float]
) -> tuple[bytes, bytes]:
    """Render the intraday price path, VWAP/average line, daily MA levels and minute volume."""
    width, height = 1000, 1200
    image = Image.new("RGB", (width, height), "#05090d")
    draw = ImageDraw.Draw(image)
    title_font = _font(28)
    price_font = _font(66)
    label_font = _font(18)
    small_font = _font(14)

    latest_price = _number(quote.get("lastPrice")) or points[-1]["price"]
    change = _number(quote.get("change"))
    if change is None:
        previous_close = _number(quote.get("previousClose")) or latest_price
        change = latest_price - previous_close
    change_percent = _number(quote.get("changePercent"))
    if change_percent is None:
        previous_close = _number(quote.get("previousClose")) or latest_price
        change_percent = (change / previous_close * 100.0) if previous_close else 0.0
    positive = change >= 0
    price_color = "#ff4d57" if positive else "#20e66b"
    fill_color = (210, 45, 58, 62) if positive else (10, 150, 62, 65)

    stock_name = str(quote.get("name") or "").strip()
    heading = f"{stock_code}  {stock_name}" if stock_name else stock_code
    draw.text((42, 24), heading, font=title_font, fill="#f0f4f8")
    draw.text((42, 72), "INTRADAY PRICE", font=small_font, fill="#8d9aa6")
    draw.text((42, 104), f"{latest_price:.2f}", font=price_font, fill=price_color)
    draw.text((390, 133), f"{change:+.2f} ({change_percent:+.2f}%)", font=title_font, fill=price_color)

    open_price = _number(quote.get("openPrice"))
    high_price = _number(quote.get("highPrice"))
    low_price = _number(quote.get("lowPrice"))
    avg_price = _number(quote.get("avgPrice"))
    previous_close = _number(quote.get("previousClose"))
    summary_parts = []
    for label, value in (("OPEN", open_price), ("HIGH", high_price), ("LOW", low_price), ("AVG", avg_price), ("PREV", previous_close)):
        if value is not None:
            summary_parts.append(f"{label} {value:.2f}")
    draw.text((44, 202), "    ".join(summary_parts), font=label_font, fill="#d0d7de")
    draw.text((44, 238), _quote_time_label(quote), font=small_font, fill="#91a0ad")

    left, right = 105, 960
    plot_top, plot_bottom = 365, 855
    volume_top, volume_bottom = 925, 1080
    plot_width = right - left

    visible_mas, hidden_mas = _visible_intraday_ma_levels(ma_levels, latest_price)
    price_values = [point["price"] for point in points]
    price_values += [v for v in (point.get("average") for point in points) if v is not None]
    if previous_close is not None:
        price_values.append(previous_close)
    price_values.extend(visible_mas.values())
    price_low, price_high = min(price_values), max(price_values)
    raw_spread = price_high - price_low
    pad = max(raw_spread * 0.09, latest_price * 0.003, 0.05)
    axis_low, axis_high = price_low - pad, price_high + pad
    if axis_high <= axis_low:
        axis_high = axis_low + 1.0

    def y_price(value: float) -> float:
        return plot_bottom - (value - axis_low) / (axis_high - axis_low) * (plot_bottom - plot_top)

    def x_time(value: datetime) -> float:
        local = value.astimezone(_TAIPEI)
        minute = (local.hour - 9) * 60 + local.minute + local.second / 60.0
        minute = min(max(minute, 0.0), float(_SESSION_MINUTES))
        return left + minute / _SESSION_MINUTES * plot_width

    # Price grid and y-axis labels.
    for idx in range(6):
        y = plot_top + idx * (plot_bottom - plot_top) / 5
        value = axis_high - idx * (axis_high - axis_low) / 5
        draw.line((left, y, right, y), fill="#252e36", width=1)
        draw.text((8, int(y - 10)), f"{value:.2f}", font=small_font, fill="#a5b0ba")

    # Time axis is fixed to the Taiwan regular session; the plotted line grows as candles arrive.
    for minute, label in ((0, "09:00"), (60, "10:00"), (120, "11:00"), (180, "12:00"), (240, "13:00"), (270, "13:30")):
        x = left + minute / _SESSION_MINUTES * plot_width
        draw.line((x, plot_top, x, volume_bottom), fill="#202a33", width=1)
        draw.text((int(x - 25), 1100), label, font=small_font, fill="#9aa6b2")

    # Previous close and daily moving averages are reference levels, not intraday price curves.
    if previous_close is not None and axis_low <= previous_close <= axis_high:
        _draw_dashed_line(draw, (left, int(y_price(previous_close)), right, int(y_price(previous_close))), "#89939c", 2)
        draw.text((right - 110, int(y_price(previous_close)) - 22), f"PREV {previous_close:.2f}", font=small_font, fill="#aab4bd")

    ma_colors = {5: "#f4c542", 10: "#38cbd0", 20: "#bd83ff"}
    legend_x = left
    legend_y = 302
    draw.text((42, legend_y), "PRICE", font=small_font, fill=price_color)
    draw.line((105, legend_y + 9, 135, legend_y + 9), fill=price_color, width=4)
    draw.text((150, legend_y), "AVG", font=small_font, fill="#d4d9de")
    draw.line((190, legend_y + 9, 220, legend_y + 9), fill="#d4d9de", width=3)
    legend_x = 250
    for period in (5, 10, 20):
        if period in visible_mas:
            color = ma_colors[period]
            y = int(y_price(visible_mas[period]))
            _draw_dashed_line(draw, (left, y, right, y), color, 2)
            draw.text((right - 112, y - 22), f"MA{period} {visible_mas[period]:.2f}", font=small_font, fill=color)
            draw.line((legend_x, legend_y + 9, legend_x + 24, legend_y + 9), fill=color, width=3)
            draw.text((legend_x + 31, legend_y), f"MA{period}", font=small_font, fill=color)
            legend_x += 110
        elif period in hidden_mas:
            draw.text((legend_x, legend_y), f"MA{period} hidden", font=small_font, fill="#6d7882")
            legend_x += 105

    # Subtle filled area below the intraday line, like the supplied brokerage reference.
    price_points = [(x_time(point["time"]), y_price(point["price"])) for point in points]
    if len(price_points) >= 2:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        polygon = [(price_points[0][0], plot_bottom)] + price_points + [(price_points[-1][0], plot_bottom)]
        overlay_draw.polygon(polygon, fill=fill_color)
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)
    if len(price_points) >= 2:
        draw.line(price_points, fill=price_color, width=4, joint="curve")
    elif price_points:
        x, y = price_points[0]
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=price_color)

    average_points = [
        (x_time(point["time"]), y_price(point["average"]))
        for point in points if point.get("average") is not None
    ]
    if len(average_points) >= 2:
        draw.line(average_points, fill="#d4d9de", width=3, joint="curve")

    # Current quote marker and label.
    if price_points:
        last_x, last_y = price_points[-1]
        draw.ellipse((last_x - 6, last_y - 6, last_x + 6, last_y + 6), fill=price_color, outline="#ffffff", width=2)
        label_x = max(left + 4, min(int(last_x - 82), right - 160))
        label_y = max(plot_top + 4, min(int(last_y - 34), plot_bottom - 28))
        draw.rounded_rectangle((label_x, label_y, label_x + 154, label_y + 26), radius=5, fill=price_color)
        draw.text((label_x + 8, label_y + 3), f"LAST {latest_price:.2f}", font=small_font, fill="#081016")

    # Per-minute volume bars; Fugle's regular-board candles already use lots.
    draw.line((left, volume_top, right, volume_top), fill="#46515b", width=1)
    draw.text((42, 890), "VOLUME (LOTS / MIN)", font=small_font, fill="#93a1ad")
    max_volume = max((point["volume"] for point in points), default=0.0) or 1.0
    bar_width = max(2, min(6, plot_width / max(_SESSION_MINUTES, 1) * 0.72))
    for point in points:
        volume = max(0.0, point.get("volume", 0.0))
        if volume <= 0:
            continue
        x = x_time(point["time"])
        bar_height = max(1, volume / max_volume * (volume_bottom - volume_top))
        draw.rectangle((x - bar_width / 2, volume_bottom - bar_height, x + bar_width / 2, volume_bottom), fill="#2386bc")

    draw.text((42, 1150), f"{len(points)} intraday points  |  1-minute candles  |  {points[-1]['time']:%H:%M:%S}", font=small_font, fill="#a7b2bc")
    draw.text((42, 1176), "Each lookup fetches the latest available session data. MA lines use daily closes.", font=small_font, fill="#73808b")

    original = io.BytesIO()
    image.save(original, format="PNG", optimize=True)
    preview_image = image.copy()
    preview_image.thumbnail((900, 1024), Image.Resampling.LANCZOS)
    preview = io.BytesIO()
    preview_image.save(preview, format="PNG", optimize=True)
    return original.getvalue(), preview.getvalue()


def create_stock_chart(stock_code: str, quote: dict) -> str | None:
    """Create an intraday line chart; never fall back to a misleading daily-candle chart."""
    try:
        candles = _fetch_intraday_candles(stock_code, quote)
        if not candles:
            logger.warning("Intraday chart omitted because no minute candles were returned: %s", stock_code)
            return None
        points = _merge_latest_quote(candles, quote)
        if not points:
            return None
        ma_levels = _daily_ma_levels(stock_code, quote)
        original, preview = _render_chart(stock_code, points, quote, ma_levels)
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
        logger.exception("Failed to create intraday stock chart for %s", stock_code)
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
