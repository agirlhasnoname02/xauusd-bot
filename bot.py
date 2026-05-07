import time
import logging
import requests
import pandas as pd
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────
TELEGRAM_TOKEN = "TON_TOKEN_ICI"
CHAT_ID        = "TON_CHAT_ID_ICI"

CHECK_EVERY = 60 * 5  # 5 min scalping

# ─── LOGGING ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger()

# ─── TELEGRAM (DEBUG OK) ─────────────────────────────────
def send_message("🚀 TEST BOT OK - Telegram fonctionne"):
    url = f"https://api.telegram.org/bot8542688230:AAGnkw00lubZyzLiBHAPwLhJsTk41la61n8/sendMessage"
    payload = {
        "chat_id": 8531096212,
        "text": text
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        log.info(f"Telegram response: {r.status_code} | {r.text}")
    except Exception as e:
        log.error(f"Telegram error: {e}")

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

# ─── INDICATORS LIGHT SCALPING ───────────────────────────
def compute_indicators(df):
    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ema9"] = close.ewm(span=9).mean()
    df["ema21"] = close.ewm(span=21).mean()
    df["rsi"] = compute_rsi(close)

    tr = pd.concat([
        high - low,
        abs(high - close.shift()),
        abs(low - close.shift())
    ], axis=1).max(axis=1)

    df["atr"] = tr.rolling(14).mean()

    return df

# ─── RSI ────────────────────────────────────────────────
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/period).mean()
    avg_loss = loss.ewm(alpha=1/period).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))

# ─── SUPPORT / RESISTANCE ───────────────────────────────
def get_sr(df):
    support = df["low"].rolling(20).min().iloc[-1]
    resistance = df["high"].rolling(20).max().iloc[-1]
    return round(support, 2), round(resistance, 2)

# ─── SIGNAL SCALPING ─────────────────────────────────────
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
        reasons.append("Trend haussier EMA")
    else:
        sell += 1
        reasons.append("Trend baissier EMA")

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

    return "NEUTRE", price, None, None, 50, [], support, resistance

# ─── FORMAT MESSAGE ──────────────────────────────────────
def format_msg(signal, price, sl, tp, conf, reasons, support, resistance):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if signal == "NEUTRE":
        return (
            f"🔍 XAU/USD SCALP\n"
            f"{now}\n"
            f"Price: {price:.2f}\n"
            f"Support: {support} | Resistance: {resistance}\n"
            f"No clear setup"
        )

    emoji = "🟢" if signal == "BUY" else "🔴"

    return (
        f"{emoji} {signal} XAU/USD SCALP\n"
        f"{now}\n\n"
        f"Entry: {price:.2f}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        f"Confidence: {conf}%\n\n"
        f"Reasons:\n" + "\n".join(reasons[:4])
    )

# ─── MAIN LOOP ───────────────────────────────────────────
def main():
    log.info("SCALP BOT STARTED")
    send_message("🚀 Scalping bot XAU/USD ONLINE")

    while True:
        try:
            df = get_ohlcv()
            df = compute_indicators(df)

            signal, price, sl, tp, conf, reasons, support, resistance = get_signal(df)

            log.info(f"{signal} | {price:.2f} | {conf}%")

            # 🔥 IMPORTANT: ENVOI SANS BLOQUAGE
            if signal != "NEUTRE":
                msg = format_msg(signal, price, sl, tp, conf, reasons, support, resistance)
                send_message(msg)

        except Exception as e:
            log.error(e)
            send_message(f"Error: {e}")

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    main()
