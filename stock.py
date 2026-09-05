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

            stock_dict[
                row["code"]
            ] = row["name"]

    return stock_dict


stock_map = load_stock_list()


def search_stock(keyword):

    if len(keyword) < 2 and not keyword.isdigit():

        return []

    exact_match = []
    startswith_match = []
    contains_match = []
    code_match = []

    for code, name in stock_map.items():

        if keyword == name:

            exact_match.append(
                (code, name)
            )

        elif name.startswith(keyword):

            startswith_match.append(
                (code, name)
            )

        elif keyword in name:

            contains_match.append(
                (
                    name.find(keyword),
                    code,
                    name
                )
            )

        elif keyword in code:

            code_match.append(
                (code, name)
            )

    contains_match.sort()

    contains_match = [
        (code, name)
        for _, code, name in contains_match
    ]

    return (
        exact_match +
        startswith_match +
        contains_match +
        code_match
    )


#新的函式

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

    response = requests.get(
        url,
        params=params
    )

    data = response.json()

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


#抓台股資料

def update_stock_list():

    url = "https://api.finmindtrade.com/api/v4/data"

    params = {
        "dataset": "TaiwanStockInfo",
        "token": TOKEN
    }

    response = requests.get(
        url,
        params=params
    )

    data = response.json()

    with open(
        "stock_list.csv",
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(file)

        writer.writerow(
            ["code", "name"]
        )

        for row in data["data"]:

            writer.writerow(
                [
                    row["stock_id"],
                    row["stock_name"]
                ]
            )

    print("stock_list.csv 更新完成")



