from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import unittest
from unittest.mock import patch
from PIL import Image

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


def sample_intraday_points():
    base = datetime(2026, 10, 30, 9, 0, tzinfo=charting._TAIPEI)
    return [
        {"time": base + timedelta(minutes=i), "price": 100 + i * 0.2,
         "average": 100 + i * 0.1, "volume": 20 + i, "high": 101 + i * 0.2,
         "low": 99 + i * 0.2}
        for i in range(5)
    ]


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
            "lastPrice": 612.5, "highPrice": 615.0, "lowPrice": 607.0,
            "change": 2.5, "changePercent": 0.41, "market": "TSE",
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
            "lastPrice": current["close"] + 0.5, "total": {"tradeVolume": 50000},
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
        rows[-1]["close"] = rows[-1]["close"] * 1.5
        rows[-1]["high"] = rows[-1]["close"] + 0.2
        rows[-1]["open"] = rows[-1]["close"] - 0.1
        axis_low = min(row["low"] for row in rows) - 1
        axis_high = max(row["high"] for row in rows) + 1
        visible, hidden = charting._select_visible_moving_averages(rows, axis_low, axis_high)
        self.assertIn(20, hidden)
        self.assertNotIn(20, [period for period, _, _ in visible])

    def test_quote_time_label_uses_fugle_last_updated(self):
        updated_utc = datetime(2026, 10, 30, 10, 30, tzinfo=timezone.utc)
        timestamp_microseconds = int(updated_utc.timestamp() * 1_000_000)
        label = charting._quote_time_label({"lastUpdated": timestamp_microseconds})
        self.assertIn("2026-10-30 18:30:00 Asia/Taipei", label)

    def test_volume_units_are_normalized_to_lots(self):
        rows = sample_rows(30)
        current = rows[-1]
        quote = {
            "date": current["date"], "openPrice": current["open"],
            "highPrice": current["high"] + 1, "lowPrice": current["low"] - 1,
            "lastPrice": current["close"] + 0.5, "market": "TSE",
            "total": {"tradeVolume": 50000},
        }
        with patch.object(charting, "_fetch_history", return_value=rows):
            daily = charting._daily_rows("2330", quote)
        self.assertAlmostEqual(daily[-2]["volume"], rows[-2]["volume"] / 1000.0)
        self.assertEqual(daily[-1]["volume"], 50000)
        esb_quote = {**quote, "market": "ESB"}
        with patch.object(charting, "_fetch_history", return_value=rows):
            esb_daily = charting._daily_rows("1234", esb_quote)
        self.assertEqual(esb_daily[-1]["volume"], 50.0)

    def test_fetches_fresh_one_minute_candles(self):
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "date": "2026-10-30", "market": "TSE",
            "data": [{
                "date": "2026-10-30T09:00:00.000+08:00", "close": 2370,
                "average": 2371.5, "volume": 23, "high": 2372, "low": 2369,
            }],
        }
        quote = {"date": "2026-10-30", "market": "TSE"}
        with patch.object(charting.requests, "get", return_value=response) as request_get:
            with patch.object(charting, "FUGLE_API_KEY", "unit-test-key"):
                rows = charting._fetch_intraday_candles("2330", quote)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 2370)
        self.assertEqual(rows[0]["volume"], 23)
        self.assertEqual(request_get.call_args.args[0], "https://api.fugle.tw/marketdata/v1.0/stock/intraday/candles/2330")
        self.assertEqual(request_get.call_args.kwargs["params"], {"timeframe": "1", "sort": "asc"})

    def test_esb_minute_volume_is_converted_to_lots(self):
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "date": "2026-10-30", "market": "ESB",
            "data": [{
                "date": "2026-10-30T09:00:00.000+08:00", "close": 100,
                "average": 100, "volume": 1200,
            }],
        }
        with patch.object(charting.requests, "get", return_value=response):
            with patch.object(charting, "FUGLE_API_KEY", "unit-test-key"):
                rows = charting._fetch_intraday_candles("1234", {"date": "2026-10-30", "market": "ESB"})
        self.assertEqual(rows[0]["volume"], 1.2)

    def test_intraday_candles_filter_wrong_day_and_out_of_session(self):
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "date": "2026-10-30", "market": "TSE",
            "data": [
                {"date": "2026-10-30T08:59:00+08:00", "close": 100, "volume": 1},
                {"date": "2026-10-30T09:00:00+08:00", "close": 101, "volume": 2},
                {"date": "2026-10-29T09:01:00+08:00", "close": 99, "volume": 3},
                {"date": "2026-10-30T13:31:00+08:00", "close": 102, "volume": 4},
            ],
        }
        with patch.object(charting.requests, "get", return_value=response):
            with patch.object(charting, "FUGLE_API_KEY", "unit-test-key"):
                rows = charting._fetch_intraday_candles("2330", {"date": "2026-10-30", "market": "TSE"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 101)

    def test_latest_quote_is_merged_into_last_minute_point(self):
        points = sample_intraday_points()
        quote = {
            "date": "2026-10-30", "lastPrice": 102.25, "avgPrice": 101.2,
            "lastTrade": {"time": int(datetime(2026, 10, 30, 9, 4, 35, tzinfo=charting._TAIPEI).timestamp() * 1_000_000)},
        }
        merged = charting._merge_latest_quote(points, quote)
        self.assertEqual(len(merged), len(points))
        self.assertEqual(merged[-1]["price"], 102.25)
        self.assertEqual(merged[-1]["average"], 101.2)

    def test_latest_quote_appends_a_point_when_it_is_newer_than_last_candle(self):
        points = sample_intraday_points()[:3]
        quote = {
            "date": "2026-10-30", "lastPrice": 102.25,
            "lastUpdated": int(datetime(2026, 10, 30, 9, 4, 10, tzinfo=charting._TAIPEI).timestamp() * 1_000_000),
        }
        merged = charting._merge_latest_quote(points, quote)
        self.assertEqual(len(merged), 4)
        self.assertEqual(merged[-1]["price"], 102.25)
        self.assertEqual(merged[-1]["volume"], 0.0)

    def test_daily_ma_values_use_live_price_as_provisional_close(self):
        rows = sample_rows(25)
        quote_date = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)).isoformat()
        quote = {"date": quote_date, "lastPrice": 200.0}
        with patch.object(charting, "_fetch_history", return_value=rows):
            levels = charting._daily_ma_levels("2330", quote)
        expected_ma5 = (sum(row["close"] for row in rows[-4:]) + 200.0) / 5
        self.assertAlmostEqual(levels[5], expected_ma5)
        self.assertIn(10, levels)
        self.assertIn(20, levels)

    def test_outlying_daily_mas_are_hidden_from_intraday_axis(self):
        visible, hidden = charting._visible_intraday_ma_levels({5: 99, 10: 101, 20: 130}, 100)
        self.assertEqual(visible, {5: 99, 10: 101})
        self.assertIn(20, hidden)

    def test_chart_skips_instead_of_falling_back_to_daily_candles(self):
        quote = {"lastPrice": 100, "date": "2026-10-30"}
        with patch.object(charting, "_fetch_intraday_candles", return_value=[]):
            with patch.object(charting, "_fetch_history", return_value=sample_rows(30)):
                self.assertIsNone(charting.create_stock_chart("2330", quote))

    def test_renders_portrait_intraday_line_chart_and_line_preview(self):
        quote = {
            "date": "2026-10-30", "name": "台積電", "lastPrice": 100.8,
            "previousClose": 100.0, "openPrice": 100.2, "highPrice": 101.2,
            "lowPrice": 99.8, "avgPrice": 100.5, "change": 0.8,
            "changePercent": 0.8,
            "lastUpdated": int(datetime(2026, 10, 30, 9, 5, tzinfo=charting._TAIPEI).timestamp() * 1_000_000),
        }
        with patch.object(charting, "_fetch_intraday_candles", return_value=sample_intraday_points()):
            with patch.object(charting, "_fetch_history", return_value=sample_rows(30)):
                chart_id = charting.create_stock_chart("2330", quote)
        self.assertIsNotNone(chart_id)
        original = charting.get_chart_image(chart_id, "original")
        preview = charting.get_chart_image(chart_id, "preview")
        self.assertIsNotNone(original)
        self.assertIsNotNone(preview)
        original_image = Image.open(BytesIO(original))
        preview_image = Image.open(BytesIO(preview))
        self.assertEqual(original_image.size, (1000, 1200))
        self.assertLessEqual(preview_image.width, 1024)
        self.assertLessEqual(preview_image.height, 1024)
        self.assertIsNone(charting.get_chart_image("missing", "original"))

    def test_invalid_quote_timestamp_uses_retrieval_label(self):
        label = charting._quote_time_label({"lastUpdated": "not-a-time"})
        self.assertIn("Quote timestamp unavailable", label)


if __name__ == "__main__":
    unittest.main(verbosity=2)
