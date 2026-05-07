import time
import logging
import requests
import pandas as pd
from datetime import datetime, timezone

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
TELEGRAM_TOKEN = "8542688230:AAGnkw00lubZyzLiBHAPwLhJsTk41la61n8"
CHAT_ID = "5096041910"

CHECK_EVERY = 60 * 5

# ─────────────────────────────
# LOGGING
# ─────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger()

# ─────────────────────────────
# TELEGRAM SAFE SEND
# ─────────────────────────────
def send_message(text):
    if not text or str(text).strip() == "":
        log.warning("Message vide ignoré")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        log.info(f"Telegram: {r.status_code} | {r.text}")

    except Exception as e:
        log.error(f"Telegram error: {e}")

# ─────────────────────────────
# TEST TELEGRAM AU DÉMARRAGE
# ─────────────────────────────
def test_telegram():
    log.info("Testing Telegram connection...")

    send_message("🚀 BOT ONLINE - TEST OK")

# ─────────────────────────────
# DATA XAU (Yahoo)
# ─────────────────────────────
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
        "close": ohlcv["close"]
    }).dropna()

    return df

# ─────────────────────────────
# RSI
# ─────────────────────────────
def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))

# ─────────────────────────────
# INDICATORS
# ─────────────────────────────
def indicators(df):
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["rsi"] = rsi(df["close"])

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.rolling(14).mean()

    return df

# ─────────────────────────────
# SUPPORT / RESISTANCE
# ─────────────────────────────
def sr(df):
    return round(df["low"].rolling(20).min().iloc[-1], 2), round(df["high"].rolling(20).max().iloc[-1], 2)

# ─────────────────────────────
# SIGNAL
# ─────────────────────────────
def signal(df):
    last = df.iloc[-1]

    price = last["close"]
    ema9 = last["ema9"]
    ema21 = last["ema21"]
    rsi_val = last["rsi"]
    atr = last["atr"]

    support, resistance = sr(df)

    # sécurité
    if pd.isna(price) or pd.isna(ema9) or pd.isna(ema21) or pd.isna(rsi_val) or pd.isna(atr):
        return "NEUTRAL", price, None, None, 50, [], support, resistance

    buy = 0
    sell = 0
    reasons = []

    if ema9 > ema21:
        buy += 1
        reasons.append("EMA bullish")
    else:
        sell += 1
        reasons.append("EMA bearish")

    if rsi_val > 55:
        buy += 1
        reasons.append(f"RSI bullish {rsi_val:.1f}")
    elif rsi_val < 45:
        sell += 1
        reasons.append(f"RSI bearish {rsi_val:.1f}")

    if price <= support * 1.002:
        buy += 1
        reasons.append("Support zone")

    if price >= resistance * 0.998:
        sell += 1
        reasons.append("Resistance zone")

    if buy >= 2:
        return "BUY", price, round(price - atr*1.2,2), round(price + atr*1.8,2), 75, reasons, support, resistance

    if sell >= 2:
        return "SELL", price, round(price + atr*1.2,2), round(price - atr*1.8,2), 75, reasons, support, resistance

    return "NEUTRAL", price, None, None, 50, [], support, resistance

# ─────────────────────────────
# FORMAT MESSAGE
# ─────────────────────────────
def format_msg(sig, price, sl, tp, conf, reasons, sup, res):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if sig == "NEUTRAL":
        return f"""🔍 XAU/USD
{now}
Price: {price:.2f}
Support: {sup} | Resistance: {res}
No setup"""

    emoji = "🟢" if sig == "BUY" else "🔴"

    return f"""{emoji} {sig} XAU/USD
{now}

Entry: {price:.2f}
SL: {sl}
TP: {tp}
Confidence: {conf}%

Reasons:
- """ + "\n- ".join(reasons[:4])

# ─────────────────────────────
# MAIN LOOP
# ─────────────────────────────
def main():
    log.info("BOT STARTED")

    test_telegram()

    while True:
        try:
            df = get_ohlcv()
            df = indicators(df)

            sig, price, sl, tp, conf, reasons, sup, res = signal(df)

            log.info(f"{sig} | {price} | {conf}%")

            if sig != "NEUTRAL":
                msg = format_msg(sig, price, sl, tp, conf, reasons, sup, res)
                send_message(msg)

        except Exception as e:
            log.error(e)
            send_message(f"ERROR: {e}")

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    main()
