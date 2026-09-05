from stock import (
    get_stock_data,
    search_stock,
)

while True:

    keyword = input("請輸入股票名稱：")

    matches = search_stock(keyword)

    if len(matches) == 0:

        if len(keyword) < 2 and not keyword.isdigit():

            print("請至少輸入 2 個中文字，或完整股票代號")

        else:

            print("找不到符合的股票")

    elif len(matches) == 1:

        code, name = matches[0]

        print(f"找到：{code} {name}")

        result = get_stock_data(code)

        print()

        print(
            f"""
        📈 {name} ({code})

        💰 最新價格：{result['price']:.2f}

        {result['trend_icon']} 漲跌：{result['change']:+.2f}
        {result['trend_icon']} 漲跌幅：{result['change_percent']:+.2f}%

        🔺 今日最高：{result['high']:.2f}
        🔻 今日最低：{result['low']:.2f}

        📦 成交量：{result['volume']/1000:,.0f} 張
        """
        )

        print()

    else:

        print(f"\n找到 {len(matches)} 筆資料")
        print("以下顯示前20筆：\n")

        for code, name in matches[:20]:

            print(f"{code} {name}")

        print("\n請輸入股票代號繼續查詢\n")