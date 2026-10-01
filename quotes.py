"""批次查價：並行 + 短 TTL 快取，避免逐檔串行造成 LINE 回覆逾時與 API 限流。"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from stock import get_stock_data, stock_map

logger = logging.getLogger(__name__)

_TTL_SECONDS = 15
_MAX_WORKERS = 5
_FUTURE_TIMEOUT = 15

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def get_quotes(codes) -> dict[str, dict | None]:
    """回傳 {code: quote_dict 或 None（查詢失敗）}。"""
    unique = list(dict.fromkeys(codes))
    result: dict[str, dict | None] = {}
    to_fetch: list[str] = []

    now = time.monotonic()
    with _lock:
        for code in unique:
            hit = _cache.get(code)
            if hit and now - hit[0] < _TTL_SECONDS:
                result[code] = hit[1]
            else:
                to_fetch.append(code)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(to_fetch))) as pool:
            futures = {
                code: pool.submit(get_stock_data, code, stock_map.get(code, {}).get("type"))
                for code in to_fetch
            }
            for code, fut in futures.items():
                try:
                    data = fut.result(timeout=_FUTURE_TIMEOUT)
                except Exception:
                    logger.exception("查價失敗 code=%s", code)
                    data = None
                result[code] = data
                if data:
                    with _lock:
                        _cache[code] = (time.monotonic(), data)
    return result
