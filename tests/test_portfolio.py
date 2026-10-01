import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from decimal import Decimal

import pytest

import db
import portfolio
from portfolio import UserError


@pytest.fixture(autouse=True)
def fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.init_db()


def test_buy_weighted_average():
    portfolio.buy("u1", "2330", "台積電", 1000, Decimal("600"))
    h = portfolio.buy("u1", "2330", "台積電", 1000, Decimal("800"))
    assert h.shares == 2000 and h.avg_cost == Decimal("700.0000")


def test_sell_keeps_avg_cost_and_clears():
    portfolio.buy("u1", "2330", "台積電", 1000, Decimal("600"))
    remaining, avg = portfolio.sell("u1", "2330", 400)
    assert remaining == 600 and avg == Decimal("600.0000")
    assert portfolio.sell("u1", "2330", 600)[0] == 0
    assert portfolio.list_holdings("u1") == []


def test_oversell_and_missing():
    portfolio.buy("u1", "2330", "台積電", 100, Decimal("600"))
    with pytest.raises(UserError):
        portfolio.sell("u1", "2330", 101)
    with pytest.raises(UserError):
        portfolio.sell("u1", "2317", 1)


def test_user_isolation_and_watch():
    portfolio.buy("u1", "2330", "台積電", 100, Decimal("600"))
    assert portfolio.list_holdings("u2") == []
    assert portfolio.add_watch("u1", "2330", "台積電") is True
    assert portfolio.add_watch("u1", "2330", "台積電") is False
    assert portfolio.remove_watch("u2", "2330") is False
    assert portfolio.remove_watch("u1", "2330") is True
