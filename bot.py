import time
import logging
import requests
import pandas as pd
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────
TELEGRAM_TOKEN = "8542688230:AAGnkw00lubZyzLiBHAPwLhJsTk41la61n8"
CHAT_ID = "8531096212"

CHECK_EVERY = 60 * 5  # 5 min

# ─── LOGGING ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger()

# ─── TELEGRAM ─────────────────────────────────────────────
def send_message(text):
    url = f"https://api.telegram.org/bot8542688230:AAGnkw00lubZyzLiBHAPwLhJsTk41la61n8/sendMessage"

    payload = {
        "chat_id": CHAT_ID,"8531096212"
        "text": text
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        log.info(f"Telegram: {r.status_code} | {r.text}")
        return r.json()

    except Exception as e:
        log.error(f"Telegram error: {e}")
        return None
import requests

TOKEN = "8542688230:AAGnkw00lubZyzLiBHAPwLhJsTk41la61n8"
url = f"https://api.telegram.org/bot8542688230:AAGnkw00lubZyzLiBHAPwLhJsTk41la61n8/getMe"

print(requests.get(url).text)

# ─── DATA ────────────────────────────────────────────────
def get_ohlcv():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    params = {"interval": "15m", "range": "5d"}
    headers = {"User-Agent": "Mozilla/5.0"}

    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()

    data = r.json()["chart"]["result"][0]
    ohlcv = data["indicators"]["quote"][0]

    df = pd.DataFrame({
        "time": pd.to_datetime(data["timestamp"], unit="s"),
        "open": ohlcv["open"],
        "high": ohlcv["high"],
        "low": ohlcv["low"],
        "close": ohlcv["close"],
        "volume": ohlcv["volume"]
    }).dropna()

    return df


# ─── INDICATORS ───────────────────────────────────────────
def compute_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def compute_indicators(df):
    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()
    df["rsi"] = compute_rsi(close)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.rolling(14).mean()

    return df


# ─── SUPPORT / RESISTANCE ────────────────────────────────
def get_sr(df):
    support = df["low"].rolling(20).min().iloc[-1]
    resistance = df["high"].rolling(20).max().iloc[-1]
    return round(support, 2), round(resistance, 2)


# ─── SIGNAL ENGINE ────────────────────────────────────────
def get_signal(df):
    last = df.iloc[-1]

    price = last["close"]
    ema9 = last["ema9"]
    ema21 = last["ema21"]
    rsi = last["rsi"]
    atr = last["atr"]

    support, resistance = get_sr(df)

    buy = 0
    sell = 0
    reasons = []

    # TREND
    if ema9 > ema21:
        buy += 1
        reasons.append("EMA trend bullish")
    else:
        sell += 1
        reasons.append("EMA trend bearish")

    # RSI
    if rsi > 55:
        buy += 1
        reasons.append(f"RSI bullish {rsi:.1f}")
    elif rsi < 45:
        sell += 1
        reasons.append(f"RSI bearish {rsi:.1f}")

    # ZONES
    if price <= support * 1.002:
        buy += 1
        reasons.append("Support zone")
    if price >= resistance * 0.998:
        sell += 1
        reasons.append("Resistance zone")

    # DECISION
    if buy >= 2:
        sl = round(price - atr * 1.2, 2)
        tp = round(price + atr * 1.8, 2)
        return "BUY", price, sl, tp, 75, reasons, support, resistance

    if sell >= 2:
        sl = round(price + atr * 1.2, 2)
        tp = round(price - atr * 1.8, 2)
        return "SELL", price, sl, tp, 75, reasons, support, resistance

    return "NEUTRAL", price, None, None, 50, [], support, resistance


# ─── MESSAGE FORMAT ───────────────────────────────────────
def format_msg(signal, price, sl, tp, conf, reasons, support, resistance):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if signal == "NEUTRAL":
        return (
            f"🔍 XAU/USD SCALP\n"
            f"{now}\n"
            f"Price: {price:.2f}\n"
            f"Support: {support} | Resistance: {resistance}\n"
            f"No clear setup"
        )

    emoji = "🟢" if signal == "BUY" else "🔴"

    return (
        f"{emoji} {signal} XAU/USD\n"
        f"{now}\n\n"
        f"Entry: {price:.2f}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        f"Confidence: {conf}%\n\n"
        f"Reasons:\n" + "\n".join(reasons[:4])
    )


# ─── MAIN LOOP ───────────────────────────────────────────
def main():
    log.info("BOT STARTED")
    send_message("🚀 XAU/USD bot ONLINE")

    while True:
        try:
            df = get_ohlcv()
            df = compute_indicators(df)

            signal, price, sl, tp, conf, reasons, support, resistance = get_signal(df)

            log.info(f"{signal} | {price:.2f} | {conf}%")

            if signal != "NEUTRAL":
                msg = format_msg(signal, price, sl, tp, conf, reasons, support, resistance)
                send_message(msg)

        except Exception as e:
            log.error(e)
            send_message(f"ERROR: {e}")

        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()
