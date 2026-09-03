import yfinance as yf

stock_no = input("請輸入股票代號：")

for suffix in [".TW", ".TWO"]:

    stock = yf.Ticker(stock_no + suffix)

    info = stock.info

    if info.get("shortName"):

        print("股票代號:", stock_no)
        print("股票名稱:", info.get("shortName"))
        print("目前股價:", info.get("currentPrice"))

        break