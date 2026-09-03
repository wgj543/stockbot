from flask import Flask, request

app = Flask(__name__)

@app.route("/")
def home():
    return "Stock Bot Running!"

@app.route("/callback", methods=["POST"])
def callback():
    return "OK"

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)