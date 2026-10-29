from datetime import date, timedelta
from io import BytesIO
import unittest
from unittest.mock import patch
from PIL import Image

# Keep this test module runnable from the standalone update package as well as
# from the full project root, where the real stock.py is present.
try:
    import stock  # noqa: F401
except ModuleNotFoundError:
    import os
    import sys
    import types
    stock_stub = types.ModuleType("stock")
    stock_stub.FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY")
    sys.modules["stock"] = stock_stub

import charting


def sample_rows(count=45):
    rows = []
    day = date(2026, 7, 1)
    price = 100.0
    while len(rows) < count:
        if day.weekday() < 5:
            opening = price + 0.2
            closing = opening + (0.4 if len(rows) % 2 else -0.3)
            rows.append({
                "date": day.isoformat(), "open": opening,
                "high": max(opening, closing) + 0.8,
                "low": min(opening, closing) - 0.8,
                "close": closing, "volume": 10000 + len(rows) * 100,
            })
            price = closing
        day += timedelta(days=1)
    return rows


class ChartingUpdateTests(unittest.TestCase):
    def test_ma_periods_and_warmup(self):
        rows = sample_rows(25)
        ma5 = charting._moving_average(rows, 5)
        ma10 = charting._moving_average(rows, 10)
        ma20 = charting._moving_average(rows, 20)
        self.assertIsNone(ma5[3])
        self.assertIsNotNone(ma5[4])
        self.assertIsNone(ma10[8])
        self.assertIsNotNone(ma10[9])
        self.assertIsNone(ma20[18])
        self.assertIsNotNone(ma20[19])

    def test_normalize_quote_for_existing_text_reply(self):
        raw = {
            "lastPrice": 612.5,
            "highPrice": 615.0,
            "lowPrice": 607.0,
            "change": 2.5,
            "changePercent": 0.41,
            "market": "TSE",
            "total": {"tradeVolume": 12345},
        }
        result = charting.normalize_quote_for_reply("2330", raw)
        self.assertEqual(result["code"], "2330")
        self.assertEqual(result["price"], 612.5)
        self.assertEqual(result["high"], 615.0)
        self.assertEqual(result["low"], 607.0)
        self.assertEqual(result["volume"], 12345.0)
        self.assertEqual(result["trend_icon"], "🔴")
        self.assertIsNone(charting.normalize_quote_for_reply("2330", None))

    def test_fetch_current_quote_uses_uncached_fugle_endpoint(self):
        response = unittest.mock.Mock()
        response.json.return_value = {"symbol": "2330", "lastPrice": 600.0}
        response.raise_for_status.return_value = None
        with patch.object(charting.requests, "get", return_value=response) as request_get:
            with patch.object(charting, "FUGLE_API_KEY", "unit-test-key"):
                result = charting.fetch_current_quote("2330")
        self.assertEqual(result["lastPrice"], 600.0)
        self.assertEqual(request_get.call_args.args[0], "https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/2330")
        self.assertEqual(request_get.call_args.kwargs["headers"]["X-API-KEY"], "unit-test-key")

    def test_current_quote_replaces_current_day_bar(self):
        rows = sample_rows(30)
        current = dict(rows[-1])
        quote = {
            "date": current["date"], "openPrice": current["open"],
            "highPrice": current["high"] + 1, "lowPrice": current["low"] - 1,
            "lastPrice": current["close"] + 0.5,
            "total": {"tradeVolume": 50000},
        }
        with patch.object(charting, "_fetch_history", return_value=rows):
            daily = charting._daily_rows("2330", quote)
        self.assertEqual(sum(1 for row in daily if row["date"] == current["date"]), 1)
        bar = next(row for row in daily if row["date"] == current["date"])
        self.assertEqual(bar["close"], quote["lastPrice"])
        self.assertGreaterEqual(bar["high"], quote["lastPrice"])
        self.assertLessEqual(bar["low"], quote["lastPrice"])
        self.assertEqual(bar["volume"], 50000)

    def test_hides_moving_average_when_divergence_is_excessive(self):
        rows = sample_rows(30)
        # Build a sharp final-session move so the longer moving average is materially distant.
        rows[-1]["close"] = rows[-1]["close"] * 1.5
        rows[-1]["high"] = rows[-1]["close"] + 0.2
        rows[-1]["open"] = rows[-1]["close"] - 0.1
        axis_low = min(row["low"] for row in rows) - 1
        axis_high = max(row["high"] for row in rows) + 1
        visible, hidden = charting._select_visible_moving_averages(rows, axis_low, axis_high)
        self.assertIn(20, hidden)
        self.assertNotIn(20, [period for period, _, _ in visible])

    def test_renders_original_and_line_preview_images(self):
        rows = sample_rows(40)
        current = rows[-1]
        quote = {
            "date": current["date"], "openPrice": current["open"],
            "highPrice": current["high"], "lowPrice": current["low"],
            "lastPrice": current["close"], "total": {"tradeVolume": 25000},
        }
        with patch.object(charting, "_fetch_history", return_value=rows):
            chart_id = charting.create_stock_chart("2330", quote)
        self.assertIsNotNone(chart_id)
        original = charting.get_chart_image(chart_id, "original")
        preview = charting.get_chart_image(chart_id, "preview")
        self.assertIsNotNone(original)
        self.assertIsNotNone(preview)
        original_image = Image.open(BytesIO(original))
        preview_image = Image.open(BytesIO(preview))
        self.assertEqual(original_image.size, (1200, 900))
        self.assertLessEqual(preview_image.width, 1024)
        self.assertLessEqual(preview_image.height, 1024)
        self.assertIsNone(charting.get_chart_image("missing", "original"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
