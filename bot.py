import time
import logging
import requests
import pandas as pd
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────
TELEGRAM_TOKEN = "8542688230:AAGnkw00lubZyzLiBHAPWlJsTk41la61n8"
CHAT_ID        = "5096041910"

CHECK_EVERY    = 60 * 5   # 5 min (scalping propre XAU 15m)
STATUS_EVERY   = 60 * 60 * 4

# ─── LOGGING ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger()

# ─── TELEGRAM ────────────────────────────────────────────
def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log.error(f"Telegram error: {e}")

# ─── DATA ────────────────────────────────────────────────
def get_ohlcv(interval="15m", range_="5d"):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    params = {"interval": interval, "range": range_}
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

# ─── INDICATORS (SCALP LIGHT) ───────────────────────────
def compute_indicators(df):
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # EMA
    df["ema9"]  = close.ewm(span=9).mean()
    df["ema21"] = close.ewm(span=21).mean()
    df["ema50"] = close.ewm(span=50).mean()

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/14).mean()
    avg_loss = loss.ewm(alpha=1/14).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR
    tr = pd.concat([
        high - low,
        abs(high - close.shift()),
        abs(low - close.shift())
    ], axis=1).max(axis=1)

    df["atr"] = tr.rolling(14).mean()

    return df

# ─── SUPPORT / RESISTANCE SIMPLE ─────────────────────────
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
    ema50 = last["ema50"]
    rsi = last["rsi"]
    atr = last["atr"]

    support, resistance = get_sr(df)

    score_buy = 0
    score_sell = 0
    reasons = []

    # ── TREND ──
    if ema9 > ema21:
        score_buy += 1
        reasons.append("Trend court haussier")
    else:
        score_sell += 1
        reasons.append("Trend court baissier")

    # ── RSI MOMENTUM ──
    if rsi > 55:
        score_buy += 1
        reasons.append(f"RSI bullish ({rsi:.1f})")
    elif rsi < 45:
        score_sell += 1
        reasons.append(f"RSI bearish ({rsi:.1f})")

    # ── TREND CONFIRMATION ──
    if ema21 > ema50:
        score_buy += 1
    else:
        score_sell += 1

    # ── SUPPORT / RESISTANCE ──
    if price <= support * 1.002:
        score_buy += 1
        reasons.append("Support zone")
    if price >= resistance * 0.998:
        score_sell += 1
        reasons.append("Resistance zone")

    # ── DECISION ──
    if score_buy >= 2:
        sl = round(price - atr * 1.2, 2)
        tp = round(price + atr * 1.8, 2)
        return "BUY", price, sl, tp, 75, reasons, support, resistance

    if score_sell >= 2:
        sl = round(price + atr * 1.2, 2)
        tp = round(price - atr * 1.8, 2)
        return "SELL", price, sl, tp, 75, reasons, support, resistance

    return "NEUTRE", price, None, None, 50, [], support, resistance

# ─── FORMAT MESSAGE ──────────────────────────────────────
def format_msg(signal, price, sl, tp, confidence, reasons, support, resistance):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if signal == "NEUTRE":
        return (
            f"🔍 *XAU/USD SCALP*\n"
            f"🕐 {now}\n"
            f"💰 {price:.2f}\n"
            f"📍 Support {support} | Resistance {resistance}\n"
            f"⏳ Pas de setup clair"
        )

    emoji = "🟢" if signal == "BUY" else "🔴"

    return (
        f"{emoji} *{signal} XAU/USD SCALP*\n"
        f"🕐 {now}\n\n"
        f"💰 Entry: `{price:.2f}`\n"
        f"🛑 SL: `{sl}`\n"
        f"🎯 TP: `{tp}`\n"
        f"📊 Confidence: {confidence}%\n\n"
        f"📍 Support: {support} | Resistance: {resistance}\n\n"
        f"✔️ Reasons:\n" + "\n".join(reasons[:4])
    )

# ─── MAIN LOOP ───────────────────────────────────────────
def main():
    log.info("SCALP BOT XAU/USD STARTED")
    send_message("🚀 *Scalp Bot XAU/USD activé*")

    last_signal = None
    last_status = time.time()

    while True:
        try:
            df = get_ohlcv()
            df = compute_indicators(df)

            signal, price, sl, tp, conf, reasons, support, resistance = get_signal(df)

            log.info(f"{signal} | {price:.2f} | {conf}%")

            if signal != "NEUTRE" and signal != last_signal:
                msg = format_msg(signal, price, sl, tp, conf, reasons, support, resistance)
                send_message(msg)
                last_signal = signal

            if signal == "NEUTRE":
                last_signal = None

            if time.time() - last_status > STATUS_EVERY:
                send_message(f"🤖 Bot actif | Prix {price:.2f}")
                last_status = time.time()

        except Exception as e:
            log.error(e)
            send_message(f"⚠️ Error: {e}")
            last_signal = None

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    main()
