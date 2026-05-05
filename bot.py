import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID        = os.environ.get("CHAT_ID")
SYMBOL         = "XAUUSD"
INTERVAL       = "15m"   # 15 minutes
CHECK_EVERY    = 60 * 15  # vérifier toutes les 15 min

# ─── TELEGRAM ─────────────────────────────────────────────
def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Erreur Telegram: {e}")

# ─── DONNÉES PRIX (via Yahoo Finance) ─────────────────────
def get_ohlcv():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    params = {
        "interval": "15m",
        "range": "5d"
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    data = r.json()

    timestamps = data["chart"]["result"][0]["timestamp"]
    ohlcv = data["chart"]["result"][0]["indicators"]["quote"][0]

    df = pd.DataFrame({
        "time":   pd.to_datetime(timestamps, unit="s"),
        "open":   ohlcv["open"],
        "high":   ohlcv["high"],
        "low":    ohlcv["low"],
        "close":  ohlcv["close"],
        "volume": ohlcv["volume"]
    }).dropna()

    return df

# ─── INDICATEURS TECHNIQUES ───────────────────────────────
def compute_indicators(df):
    close = df["close"]

    # EMA
    df["ema20"]  = close.ewm(span=20, adjust=False).mean()
    df["ema50"]  = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    # RSI (14)
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"]        = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    # ATR (14) pour SL/TP
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - close.shift()).abs()
    low_close  = (df["low"]  - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    return df

# ─── LOGIQUE DE SIGNAL ────────────────────────────────────
def get_signal(df):
    last  = df.iloc[-1]
    prev  = df.iloc[-2]

    price    = last["close"]
    ema20    = last["ema20"]
    ema50    = last["ema50"]
    ema200   = last["ema200"]
    rsi      = last["rsi"]
    macd     = last["macd"]
    macd_sig = last["macd_signal"]
    atr      = last["atr"]

    score_buy  = 0
    score_sell = 0

    # EMA trend
    if price > ema20 > ema50:   score_buy  += 1
    if price < ema20 < ema50:   score_sell += 1
    if ema50 > ema200:          score_buy  += 1
    if ema50 < ema200:          score_sell += 1

    # RSI
    if 40 < rsi < 60:           pass  # neutre
    if rsi < 35:                score_buy  += 2
    if rsi > 65:                score_sell += 2

    # MACD crossover
    if macd > macd_sig and prev["macd"] <= prev["macd_signal"]:
        score_buy  += 2
    if macd < macd_sig and prev["macd"] >= prev["macd_signal"]:
        score_sell += 2

    # Décision
    sl_distance = atr * 1.5
    tp_distance = atr * 3.0

    if score_buy >= 4:
        sl = round(price - sl_distance, 2)
        tp = round(price + tp_distance, 2)
        return "BUY", price, sl, tp, score_buy

    if score_sell >= 4:
        sl = round(price + sl_distance, 2)
        tp = round(price - tp_distance, 2)
        return "SELL", price, sl, tp, score_sell

    return "NEUTRE", price, None, None, max(score_buy, score_sell)

# ─── FORMATAGE DU MESSAGE ─────────────────────────────────
def format_signal(signal, price, sl, tp, score):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if signal == "NEUTRE":
        return (
            f"🔍 *XAU/USD — Analyse {now}*\n"
            f"Prix actuel : `{price:.2f}`\n"
            f"⏳ Pas de signal clair (score={score}/5)"
        )

    emoji = "🟢" if signal == "BUY" else "🔴"
    return (
        f"{emoji} *SIGNAL {signal} — XAU/USD*\n"
        f"🕐 {now}\n\n"
        f"💰 Entrée  : `{price:.2f}`\n"
        f"🛑 Stop Loss : `{sl:.2f}`\n"
        f"🎯 Take Profit : `{tp:.2f}`\n\n"
        f"📊 Confirmation : {score}/5 indicateurs\n"
        f"⚠️ _Gérez toujours votre risque._"
    )

# ─── BOUCLE PRINCIPALE ────────────────────────────────────
def main():
    print("✅ Bot XAU/USD démarré...")
    send_message("✅ *Bot XAU/USD démarré* — Surveillance active sur GC=F (15m)")

    last_signal = None

    while True:
        try:
            df = get_ohlcv()
            df = compute_indicators(df)
            signal, price, sl, tp, score = get_signal(df)

            print(f"[{datetime.utcnow().strftime('%H:%M')}] Signal: {signal} | Prix: {price:.2f} | Score: {score}")

            # Envoyer seulement si le signal change (éviter le spam)
            if signal != "NEUTRE" and signal != last_signal:
                msg = format_signal(signal, price, sl, tp, score)
                send_message(msg)
                last_signal = signal
            elif signal == "NEUTRE":
                last_signal = None

        except Exception as e:
            print(f"Erreur: {e}")
            send_message(f"⚠️ Erreur bot: {e}")

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    main()
