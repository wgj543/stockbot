"""庫存 / 待觀察名單的業務邏輯（不依賴 LINE、不依賴行情）。"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from db import Holding, Watch, session_scope

MAX_HOLDINGS = 30
MAX_WATCH = 20
MAX_SHARES = 100_000_000
MAX_PRICE = Decimal("100000")
_COST_Q = Decimal("0.0001")


class UserError(Exception):
    """可直接顯示給使用者的錯誤。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _count(s, model, user_id: str) -> int:
    return s.scalar(
        select(func.count()).select_from(model).where(model.user_id == user_id)
    )


def _get_holding(s, user_id: str, code: str):
    # with_for_update：PostgreSQL 下鎖定該列，避免同時買賣造成成本算錯
    return s.scalar(
        select(Holding)
        .where(Holding.user_id == user_id, Holding.code == code)
        .with_for_update()
    )


# ---------- 庫存 ----------

def buy(user_id: str, code: str, name: str, shares: int, price: Decimal) -> Holding:
    """買入：以加權平均重算成本。"""
    with session_scope() as s:
        h = _get_holding(s, user_id, code)
        if h is None:
            if _count(s, Holding, user_id) >= MAX_HOLDINGS:
                raise UserError(f"庫存檔數已達上限 {MAX_HOLDINGS} 檔")
            h = Holding(
                user_id=user_id, code=code, name=name,
                shares=shares, avg_cost=price.quantize(_COST_Q),
            )
            s.add(h)
        else:
            total = h.shares + shares
            if total > MAX_SHARES:
                raise UserError("持股數超過上限")
            h.avg_cost = (
                (h.avg_cost * h.shares + price * shares) / total
            ).quantize(_COST_Q)
            h.shares = total
            h.name = name
            h.updated_at = _now()
        s.flush()
        return h


def sell(user_id: str, code: str, shares: int) -> tuple[int, Decimal]:
    """賣出（減少庫存）：平均成本不變。回傳 (剩餘股數, 平均成本)。"""
    with session_scope() as s:
        h = _get_holding(s, user_id, code)
        if h is None:
            raise UserError(f"庫存中沒有 {code}")
        if shares > h.shares:
            raise UserError(f"庫存僅 {h.shares:,} 股，無法賣出 {shares:,} 股")
        avg = h.avg_cost
        remaining = h.shares - shares
        if remaining == 0:
            s.delete(h)
        else:
            h.shares = remaining
            h.updated_at = _now()
        return remaining, avg


def set_holding(user_id: str, code: str, name: str, shares: int, price: Decimal) -> Holding:
    """直接覆寫股數與平均成本（用來修正輸入錯誤）。"""
    with session_scope() as s:
        h = _get_holding(s, user_id, code)
        if h is None:
            if _count(s, Holding, user_id) >= MAX_HOLDINGS:
                raise UserError(f"庫存檔數已達上限 {MAX_HOLDINGS} 檔")
            h = Holding(user_id=user_id, code=code, name=name,
                        shares=shares, avg_cost=price.quantize(_COST_Q))
            s.add(h)
        else:
            h.name = name
            h.shares = shares
            h.avg_cost = price.quantize(_COST_Q)
            h.updated_at = _now()
        s.flush()
        return h


def list_holdings(user_id: str) -> list[Holding]:
    with session_scope() as s:
        return list(s.scalars(
            select(Holding).where(Holding.user_id == user_id).order_by(Holding.code)
        ))


# ---------- 待觀察名單 ----------

def add_watch(user_id: str, code: str, name: str) -> bool:
    """回傳 True=新增成功，False=已存在。"""
    with session_scope() as s:
        exists = s.scalar(select(Watch.id).where(Watch.user_id == user_id, Watch.code == code))
        if exists:
            return False
        if _count(s, Watch, user_id) >= MAX_WATCH:
            raise UserError(f"待觀察名單已達上限 {MAX_WATCH} 檔")
        s.add(Watch(user_id=user_id, code=code, name=name))
        return True


def remove_watch(user_id: str, code: str) -> bool:
    with session_scope() as s:
        w = s.scalar(select(Watch).where(Watch.user_id == user_id, Watch.code == code))
        if w is None:
            return False
        s.delete(w)
        return True


def list_watch(user_id: str) -> list[Watch]:
    with session_scope() as s:
        return list(s.scalars(
            select(Watch).where(Watch.user_id == user_id).order_by(Watch.created_at)
        ))
