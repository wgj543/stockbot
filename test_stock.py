from app import get_stock_data

stocks = [
    "2330",
    "2303",
    "2454",
    "8299",
    "0050",
    "00919"
]

for stock in stocks:

    print("\n" + "=" * 60)

    result = get_stock_data(stock)

    print(f"股票代號: {stock}")

    print(result)