import os
import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────
TELEGRAM_TOKEN = "8542688230:AAGnkw00lubZyzLiBHAPwLhJsTk41la61n8"
CHAT_ID        = "5096041910"
CHECK_EVERY    = 60 * 15          # toutes les 15 min
HTF_REFRESH    = 60 * 60          # refresh tendance HTF toutes les 1h
STATUS_EVERY   = 60 * 60 * 4     # rapport de statut toutes les 4h

# ─── LOGGING ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot_xauusd.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── CACHE HTF ────────────────────────────────────────────
htf_cache = {"trend": ("NEUTRE", "NEUTRE"), "last_update": 0}

# ─── TELEGRAM ─────────────────────────────────────────────
def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log.error(f"Erreur Telegram: {e}")

# ─── DONNÉES PRIX ─────────────────────────────────────────
def get_ohlcv(interval="15m", range_="5d"):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    params = {"interval": interval, "range": range_}
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data   = r.json()
    result = data["chart"]["result"][0]
    ohlcv  = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "time":   pd.to_datetime(result["timestamp"], unit="s"),
        "open":   ohlcv["open"],
        "high":   ohlcv["high"],
        "low":    ohlcv["low"],
        "close":  ohlcv["close"],
        "volume": ohlcv["volume"]
    }).dropna()
    return df

# ─── INDICATEURS ──────────────────────────────────────────
def compute_indicators(df):
    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    # EMAs
    df["ema9"]   = close.ewm(span=9,   adjust=False).mean()
    df["ema20"]  = close.ewm(span=20,  adjust=False).mean()
    df["ema50"]  = close.ewm(span=50,  adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    # RSI (14)
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs       = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # Stoch RSI
    rsi         = df["rsi"]
    rsi_min     = rsi.rolling(14).min()
    rsi_max     = rsi.rolling(14).max()
    df["srsi"]  = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-10) * 100

    # MACD
    ema12             = close.ewm(span=12, adjust=False).mean()
    ema26             = close.ewm(span=26, adjust=False).mean()
    df["macd"]        = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    # Bollinger Bands
    sma20          = close.rolling(20).mean()
    std20          = close.rolling(20).std()
    df["bb_upper"] = sma20 + 2 * std20
    df["bb_lower"] = sma20 - 2 * std20
    df["bb_mid"]   = sma20

    # ATR (14)
    hl             = high - low
    hc             = (high - close.shift()).abs()
    lc             = (low  - close.shift()).abs()
    tr             = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"]      = tr.rolling(14).mean()

    # ADX + DI
    plus_dm          = high.diff().clip(lower=0)
    minus_dm         = (-low.diff()).clip(lower=0)
    atr14            = tr.ewm(com=13, adjust=False).mean()
    plus_di          = 100 * plus_dm.ewm(com=13, adjust=False).mean() / atr14
    minus_di         = 100 * minus_dm.ewm(com=13, adjust=False).mean() / atr14
    dx               = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
    df["adx"]        = dx.ewm(com=13, adjust=False).mean()
    df["plus_di"]    = plus_di
    df["minus_di"]   = minus_di

    # Williams %R
    highest_high     = high.rolling(14).max()
    lowest_low       = low.rolling(14).min()
    df["williams_r"] = -100 * (highest_high - close) / (highest_high - lowest_low + 1e-10)

    # Volume moyen
    df["vol_avg20"] = df["volume"].rolling(20).mean()

    return df

# ─── SUPPORT / RÉSISTANCE ─────────────────────────────────
def get_support_resistance(df, window=20):
    highs      = df["high"].rolling(window, center=True).max()
    lows       = df["low"].rolling(window, center=True).min()
    resistance = highs.dropna().iloc[-5:].max()
    support    = lows.dropna().iloc[-5:].min()
    return round(support, 2), round(resistance, 2)

# ─── PATTERNS DE BOUGIES ──────────────────────────────────
def detect_candle_pattern(df):
    last  = df.iloc[-1]
    prev  = df.iloc[-2]
    prev2 = df.iloc[-3]

    o, h, l, c = last["open"], last["high"], last["low"], last["close"]
    po, pc     = prev["open"], prev["close"]
    body       = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    patterns = []

    if pc < po and c > o and c > po and o < pc:
        patterns.append(("BULL_ENGULFING", 2))
    if pc > po and c < o and c < po and o > pc:
        patterns.append(("BEAR_ENGULFING", 2))
    if lower_wick > 2 * body and upper_wick < body and c > o:
        patterns.append(("HAMMER", 1))
    if upper_wick > 2 * body and lower_wick < body and c < o:
        patterns.append(("SHOOTING_STAR", 1))
    if (prev2["close"] < prev2["open"]
            and abs(pc - po) < abs(prev2["close"] - prev2["open"]) * 0.3
            and c > o
            and c > (prev2["open"] + prev2["close"]) / 2):
        patterns.append(("MORNING_STAR", 3))
    if (prev2["close"] > prev2["open"]
            and abs(pc - po) < abs(prev2["close"] - prev2["open"]) * 0.3
            and c < o
            and c < (prev2["open"] + prev2["close"]) / 2):
        patterns.append(("EVENING_STAR", 3))

    return patterns

# ─── TENDANCE MULTI-TIMEFRAME (avec cache) ────────────────
def get_htf_trend():
    now = time.time()
    if now - htf_cache["last_update"] < HTF_REFRESH:
        return htf_cache["trend"]
    try:
        df_1h    = get_ohlcv("1h", "1mo")
        df_1h    = compute_indicators(df_1h)
        trend_1h = "BUY" if df_1h.iloc[-1]["ema20"] > df_1h.iloc[-1]["ema50"] else "SELL"

        df_raw = get_ohlcv("1h", "3mo")
        df_raw = df_raw.set_index("time")
        df_4h  = df_raw.resample("4h").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum"
        }).dropna().reset_index()
        df_4h    = compute_indicators(df_4h)
        trend_4h = "BUY" if df_4h.iloc[-1]["ema50"] > df_4h.iloc[-1]["ema200"] else "SELL"

        htf_cache["trend"]       = (trend_1h, trend_4h)
        htf_cache["last_update"] = now
        log.info(f"HTF mis à jour — 1H: {trend_1h} | 4H: {trend_4h}")
        return trend_1h, trend_4h

    except Exception as e:
        log.warning(f"HTF erreur, cache utilisé: {e}")
        return htf_cache["trend"]  # retourne le dernier connu

# ─── SIGNAL PRINCIPAL ─────────────────────────────────────
def get_signal(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    price      = last["close"]
    ema9       = last["ema9"]
    ema20      = last["ema20"]
    ema50      = last["ema50"]
    ema200     = last["ema200"]
    rsi        = last["rsi"]
    macd       = last["macd"]
    macd_sig   = last["macd_signal"]
    macd_h     = last["macd_hist"]
    srsi       = last["srsi"]
    adx        = last["adx"]
    plus_di    = last["plus_di"]
    minus_di   = last["minus_di"]
    bb_upper   = last["bb_upper"]
    bb_lower   = last["bb_lower"]
    atr        = last["atr"]
    williams_r = last["williams_r"]
    volume     = last["volume"]
    vol_avg20  = last["vol_avg20"]

    support, resistance = get_support_resistance(df)

    score_buy    = 0
    score_sell   = 0
    reasons_buy  = []
    reasons_sell = []

    # ── Filtre volume (seuil abaissé à 0.5 pour éviter trop de NEUTRE) ──
    if pd.notna(vol_avg20) and volume < vol_avg20 * 0.5:
        log.info("Volume insuffisant, signal ignoré.")
        return "NEUTRE", price, None, None, 0, [], support, resistance

    # ── EMAs ──
    if price > ema9 > ema20 > ema50:
        score_buy += 2
        reasons_buy.append("EMA alignées haussières")
    if price < ema9 < ema20 < ema50:
        score_sell += 2
        reasons_sell.append("EMA alignées baissières")
    if ema50 > ema200:
        score_buy += 1
        reasons_buy.append("Tendance long terme haussière")
    if ema50 < ema200:
        score_sell += 1
        reasons_sell.append("Tendance long terme baissière")

    # ── RSI ──
    if 45 < rsi < 65:
        score_buy += 1
        reasons_buy.append(f"RSI favorable ({rsi:.1f})")
    if 35 < rsi < 55:
        score_sell += 1
        reasons_sell.append(f"RSI favorable ({rsi:.1f})")
    if rsi < 30:
        score_buy += 2
        reasons_buy.append(f"RSI en survente ({rsi:.1f})")
    if rsi > 70:
        score_sell += 2
        reasons_sell.append(f"RSI en surachat ({rsi:.1f})")

    # ── Stoch RSI ──
    if srsi < 25:
        score_buy += 1
        reasons_buy.append("Stoch RSI survente")
    if srsi > 75:
        score_sell += 1
        reasons_sell.append("Stoch RSI surachat")

    # ── Williams %R ──
    if williams_r < -80:
        score_buy += 1
        reasons_buy.append(f"Williams %R survente ({williams_r:.1f})")
    if williams_r > -20:
        score_sell += 1
        reasons_sell.append(f"Williams %R surachat ({williams_r:.1f})")

    # ── MACD ──
    if macd > macd_sig and prev["macd"] <= prev["macd_signal"]:
        score_buy += 2
        reasons_buy.append("Croisement MACD haussier ✨")
    if macd < macd_sig and prev["macd"] >= prev["macd_signal"]:
        score_sell += 2
        reasons_sell.append("Croisement MACD baissier ✨")
    if macd_h > 0:
        score_buy += 1
        reasons_buy.append("Momentum MACD positif")
    if macd_h < 0:
        score_sell += 1
        reasons_sell.append("Momentum MACD négatif")

    # ── ADX ──
    if adx > 25:
        if plus_di > minus_di:
            score_buy += 1
            reasons_buy.append(f"Tendance forte ADX ({adx:.1f})")
        else:
            score_sell += 1
            reasons_sell.append(f"Tendance forte ADX ({adx:.1f})")

    # ── Bollinger ──
    if price < bb_lower:
        score_buy += 1
        reasons_buy.append("Prix sous Bollinger inférieur")
    if price > bb_upper:
        score_sell += 1
        reasons_sell.append("Prix sur Bollinger supérieur")

    # ── Patterns de bougies ──
    for pattern, weight in detect_candle_pattern(df):
        if pattern in ["BULL_ENGULFING", "HAMMER", "MORNING_STAR"]:
            score_buy += weight
            reasons_buy.append(f"Pattern : {pattern}")
        elif pattern in ["BEAR_ENGULFING", "SHOOTING_STAR", "EVENING_STAR"]:
            score_sell += weight
            reasons_sell.append(f"Pattern : {pattern}")

    # ── Tendance HTF (avec cache) ──
    trend_1h, trend_4h = get_htf_trend()
    if trend_1h == "BUY":
        score_buy += 2
        reasons_buy.append("Tendance 1H haussière")
    elif trend_1h == "SELL":
        score_sell += 2
        reasons_sell.append("Tendance 1H baissière")
    if trend_4h == "BUY":
        score_buy += 2
        reasons_buy.append("Tendance 4H haussière")
    elif trend_4h == "SELL":
        score_sell += 2
        reasons_sell.append("Tendance 4H baissière")

    # ── Support / Résistance ──
    if price <= support * 1.003:
        score_buy += 1
        reasons_buy.append(f"Proche du support ({support})")
    if price >= resistance * 0.997:
        score_sell += 1
        reasons_sell.append(f"Proche de la résistance ({resistance})")

    max_score       = 22
    confidence_buy  = min(round((score_buy  / max_score) * 100), 100)
    confidence_sell = min(round((score_sell / max_score) * 100), 100)

    # ── Décision ──
    if score_buy >= 6 and score_buy > score_sell:
        sl = round(support - atr * 0.3, 2)
        tp = round(price + atr * 3.0, 2)
        # Sécurité : SL doit être sous le prix pour BUY
        if sl >= price:
            sl = round(price - atr * 1.5, 2)
        return "BUY", price, sl, tp, confidence_buy, reasons_buy, support, resistance

    if score_sell >= 6 and score_sell > score_buy:
        sl = round(resistance + atr * 0.3, 2)
        tp = round(price - atr * 3.0, 2)
        # Sécurité : SL doit être au-dessus du prix pour SELL
        if sl <= price:
            sl = round(price + atr * 1.5, 2)
        # Sécurité : TP doit être sous le prix pour SELL
        if tp >= price:
            tp = round(price - atr * 3.0, 2)
        return "SELL", price, sl, tp, confidence_sell, reasons_sell, support, resistance

    best = max(confidence_buy, confidence_sell)
    return "NEUTRE", price, None, None, best, [], support, resistance

# ─── FORMAT MESSAGE ───────────────────────────────────────
def format_signal(signal, price, sl, tp, confidence, reasons, support, resistance):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if signal == "NEUTRE":
        return (
            f"🔍 *XAU/USD — Analyse {now}*\n"
            f"Prix : `{price:.2f}`\n"
            f"Support : `{support}` | Résistance : `{resistance}`\n"
            f"⏳ Pas de signal clair — patience..."
        )

    rr     = round(abs(tp - price) / abs(price - sl), 2) if sl and tp and abs(price - sl) > 0 else 0
    emoji  = "🟢" if signal == "BUY" else "🔴"
    direct = "📈 ACHAT" if signal == "BUY" else "📉 VENTE"
    stars  = "⭐" if confidence < 60 else "⭐⭐" if confidence < 75 else "⭐⭐⭐"

    reasons_text = "\n".join([f"  ✔️ {r}" for r in reasons[:6]])

    return (
        f"{emoji} *{direct} XAU/USD* {stars}\n"
        f"🕐 {now}\n\n"
        f"💰 *Entrée*      : `{price:.2f}`\n"
        f"🛑 *Stop Loss*   : `{sl:.2f}`\n"
        f"🎯 *Take Profit* : `{tp:.2f}`\n"
        f"📐 *Risk/Reward* : `1:{rr}`\n\n"
        f"📊 *Confiance* : `{confidence}%`\n"
        f"🔎 *Raisons* :\n{reasons_text}\n\n"
        f"📍 Support : `{support}` | Résistance : `{resistance}`\n\n"
        f"⚠️ _Gérez toujours votre risque. Ce n'est pas un conseil financier._"
    )

# ─── BOUCLE PRINCIPALE ────────────────────────────────────
def main():
    log.info("Bot XAU/USD PRO démarré.")
    send_message("✅ *Bot XAU/USD PRO démarré* 🚀\nAnalyse multi-timeframe active (15m + 1H + 4H)")

    last_signal  = None
    last_status  = time.time()

    while True:
        try:
            df = get_ohlcv("15m", "5d")
            df = compute_indicators(df)
            signal, price, sl, tp, confidence, reasons, support, resistance = get_signal(df)

            now = datetime.now(timezone.utc).strftime("%H:%M")
            log.info(f"Signal: {signal} | Prix: {price:.2f} | Confiance: {confidence}%")

            # Envoyer signal si nouveau
            if signal != "NEUTRE" and signal != last_signal:
                msg = format_signal(signal, price, sl, tp, confidence, reasons, support, resistance)
                send_message(msg)
                last_signal = signal
            elif signal == "NEUTRE":
                last_signal = None

            # Rapport de statut toutes les 4h
            if time.time() - last_status >= STATUS_EVERY:
                send_message(
                    f"🤖 *Statut bot XAU/USD*\n"
                    f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
                    f"💰 Prix actuel : `{price:.2f}`\n"
                    f"📍 Support : `{support}` | Résistance : `{resistance}`\n"
                    f"✅ Bot actif et opérationnel."
                )
                last_status = time.time()

        except Exception as e:
            log.error(f"Erreur: {e}")
            send_message(f"⚠️ Erreur bot: `{e}`")
            last_signal = None

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    main()
