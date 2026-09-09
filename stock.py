import os
import csv
import requests
from datetime import datetime, timedelta

FINMIND_TOKEN = os.getenv(
    "FINMIND_TOKEN"
)

FUGLE_API_KEY = os.getenv(
    "FUGLE_API_KEY"
)

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

def get_finmind_data(stock_code):

    url = "https://api.finmindtrade.com/api/v4/data"
    
    end_date = datetime.today()

    start_date = end_date - timedelta(days=7)

    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_code,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "token": FINMIND_TOKEN
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


def get_fugle_data(
    stock_code
):

    url = (
        "https://api.fugle.tw/"
        f"marketdata/v1.0/"
        f"stock/intraday/quote/{stock_code}"
    )

    headers = {
        "X-API-KEY": FUGLE_API_KEY
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        data = response.json()

    except Exception as e:

        print(
            "FUGLE ERROR:",
            e,
            flush=True
        )

        return None

    if (
        "lastPrice" not in data
        or
        data["lastPrice"] is None
    ):

        print(
            "FUGLE INVALID DATA:",
            data,
            flush=True
        )

        return None
    
    if data.get("change", 0) > 0:

        trend_icon = "🔴"

    elif data.get("change", 0) < 0:

        trend_icon = "🟢"

    else:

        trend_icon = "⚪"

    volume = data["total"]["tradeVolume"]

    if data.get("market") == "ESB":

        volume = round(
            volume / 1000,
            2
        )

    try:

        return {
            "code": stock_code,
            "price": data["lastPrice"],
            "high": data["highPrice"],
            "low": data["lowPrice"],
            "change": data["change"],
            "change_percent": data["changePercent"],
            "volume": volume,
            "market": data.get("market"),
            "trend_icon": trend_icon
        }
    
    except Exception as e:

        print(
            "FUGLE PARSE ERROR:",
            e,
            flush=True
        )

        print(
            data,
            flush=True
        )

        return None
 

def get_stock_data(
    stock_code,
    stock_type
):

    return get_fugle_data(stock_code)
   

## 更新股票清單

def update_stock_list():

    url = "https://api.finmindtrade.com/api/v4/data"

    params = {
        "dataset": "TaiwanStockInfo",
        "token": FINMIND_TOKEN
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



