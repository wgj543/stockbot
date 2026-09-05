import os
import csv
import requests
from datetime import datetime, timedelta

TOKEN = os.getenv("FINMIND_TOKEN")

def load_stock_list():

    stock_dict = {}

    with open(
        "stock_list.csv",
        "r",
        encoding="utf-8"
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            stock_dict[row["code"]] = {
                "name": row["name"],
                "type": row["type"]
            }

    return stock_dict


stock_map = load_stock_list()


def search_stock(keyword):

    if len(keyword) < 2 and not keyword.isdigit():

        return []

    exact_match = []
    startswith_match = []
    contains_match = []
    code_match = []

    for code, info in stock_map.items():

        name = info["name"]
        stock_type = info["type"]

        if keyword == name:

            exact_match.append(
                (code, name, stock_type)
            )

        elif name.startswith(keyword):

            startswith_match.append(
                (code, name, stock_type)
            )

        elif keyword in name:

            contains_match.append(
                (
                    name.find(keyword),
                    code,
                    name,
                    stock_type
                )
            )

        elif keyword in code:

            code_match.append(
                (code, name, stock_type)
            )

    contains_match.sort()

    contains_match = [
        (code, name, stock_type)
        for _, code, name, stock_type in contains_match
    ]

    return (
        exact_match +
        startswith_match +
        contains_match +
        code_match
    )


## FinMind 股價查詢

def get_stock_data(stock_code):

    url = "https://api.finmindtrade.com/api/v4/data"

    end_date = datetime.today()

    start_date = end_date - timedelta(days=7)

    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_code,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "token": TOKEN
    }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=10
        )

        data = response.json()

    except Exception:

        return None

    if not data["data"]:

        return None
    
    latest = data["data"][-1]

    previous_close = (
        latest["close"]
        - latest["spread"]
    )

    if previous_close == 0:

        change_percent = 0

    else:

        change_percent = (
            latest["spread"]
            / previous_close
        ) * 100


    if latest["spread"] > 0:

        trend_icon = "🔴"

    elif latest["spread"] < 0:

        trend_icon = "🟢"

    else:

        trend_icon = "⚪"


    return {
        "code": stock_code,
        "price": latest["close"],
        "high": latest["max"],
        "low": latest["min"],
        "change": latest["spread"],
        "change_percent": change_percent,
        "volume": latest["Trading_Volume"],
        "trend_icon": trend_icon
    }


## 更新股票清單

def update_stock_list():

    url = "https://api.finmindtrade.com/api/v4/data"

    params = {
        "dataset": "TaiwanStockInfo",
        "token": TOKEN
    }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=10
        )

        data = response.json()

    except Exception:

        print("更新股票清單失敗")
        return

    with open(
        "stock_list.csv",
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(file)

        writer.writerow(
            ["code", "name", "type"]
        )

        for row in data["data"]:

            writer.writerow(
                [
                    row["stock_id"],
                    row["stock_name"],
                    row["type"]
                ]
            )

    print(
        f"stock_list.csv 更新完成，共 {len(data['data'])} 筆資料"
    )



