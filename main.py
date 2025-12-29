
import os
from groq import Groq   # (Or google.genai depending on which code you used)
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np
import feedparser
import json
import requests
from datetime import datetime, time as dtime
import pytz

# ==========================================
# 🛑 PASTE YOUR KEYS HERE
# ==========================================
GROQ_API_KEY = "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"
TELEGRAM_TOKEN = "8288724595:AAESol9-uMlsoRrYizltjFO7bzXDKhnPa0c"
TELEGRAM_CHAT_ID = "335147823"

# --- CONFIGURATION ---
MODEL_ID = "llama-3.3-70b-versatile"
SYMBOL = "GC=F"
BACKUP = "XAUUSD=X"

# --- HELPER: TIME ZONES ---
def is_kill_zone():
    # Gold moves best during London (3AM-7AM EST) and NY (8AM-12PM EST)
    tz = pytz.timezone('US/Eastern')
    now = datetime.now(tz).time()
    
    london_open = dtime(3, 0)
    london_close = dtime(7, 0)
    ny_open = dtime(8, 0)
    ny_close = dtime(12, 0)
    
    if (london_open <= now <= london_close) or (ny_open <= now <= ny_close):
        return True, "✅ YES (High Volatility)"
    return False, "❌ NO (Low Volatility)"

# --- HELPER: SEND TELEGRAM ---
def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})
        print("✅ Telegram Sent!")
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

# --- ADVANCED SMC LOGIC ---
def get_smc_data():
    print(f"\n📊 SCANNING FOR LIQUIDITY & GAPS...")
    
    # 1. GET DATA
    df = yf.download(SYMBOL, period="1mo", interval="1h", progress=False)
    if df.empty: 
        print("⚠️ Primary Symbol failed, using Backup...")
        df = yf.download(BACKUP, period="1mo", interval="1h", progress=False)
    
    # Fix MultiIndex if present
    if isinstance(df.columns, pd.MultiIndex): 
        df.columns = df.columns.get_level_values(0)
    
    # 2. IDENTIFY SWING POINTS (Liquidity)
    # Swing High: High > Previous High AND High > Next High
    # Note: shift(-1) means we need the NEXT candle to close to confirm a swing
    df['Swing_High'] = df['High'][(df['High'] > df['High'].shift(1)) & (df['High'] > df['High'].shift(-1))]
    df['Swing_Low'] = df['Low'][(df['Low'] < df['Low'].shift(1)) & (df['Low'] < df['Low'].shift(-1))]
    
    last_swing_high = df['Swing_High'].last_valid_index()
    last_swing_low = df['Swing_Low'].last_valid_index()
    
    high_liq_price = df.loc[last_swing_high]['High'] if last_swing_high else 0
    low_liq_price = df.loc[last_swing_low]['Low'] if last_swing_low else 0

    # 3. IDENTIFY UNMITIGATED FVGS (Corrected Logic)
    # Bullish FVG: Candle 3 Low > Candle 1 High (Gap Up)
    df['Bull_FVG'] = (df['Low'] > df['High'].shift(2)) 
    # Bearish FVG: Candle 3 High < Candle 1 Low (Gap Down)
    df['Bear_FVG'] = (df['High'] < df['Low'].shift(2))
    
    # 4. CURRENT STATUS
    curr = df.iloc[-1]
    price = curr['Close']
    
    # CHECK FOR LIQUIDITY SWEEPS (The "Fake Out")
    # Did price wick above old high but close below it?
    sweep_high = (curr['High'] > high_liq_price) and (curr['Close'] < high_liq_price)
    sweep_low = (curr['Low'] < low_liq_price) and (curr['Close'] > low_liq_price)
    
    # CHECK IF IN FVG ZONE
    # Look at last 5 candles to see if we created a gap we are now inside
    recent_candles = df.tail(5)
    in_bull_zone = any(recent_candles['Bull_FVG'])
    in_bear_zone = any(recent_candles['Bear_FVG'])
    
    # 5. RISK (ATR) - Using pandas_ta for cleaner calc
    df['ATR'] = df.ta.atr(length=14)
    atr = df.iloc[-1]['ATR']
    
    # 6. HTF TREND (200 EMA)
    df['EMA_200'] = df.ta.ema(length=200)
    trend = "BULLISH" if price > df.iloc[-1]['EMA_200'] else "BEARISH"
    
    summary = f"""
    1. MARKET STRUCTURE:
    - Trend: {trend}
    - Liquidity High: {high_liq_price:.2f}
    - Liquidity Low: {low_liq_price:.2f}
    
    2. SMC EVENTS (Last Hour):
    - Liquidity Sweep High (Bearish Signal)? {sweep_high}
    - Liquidity Sweep Low (Bullish Signal)? {sweep_low}
    - Inside Bullish FVG? {in_bull_zone}
    - Inside Bearish FVG? {in_bear_zone}
    
    3. SESSION:
    - Kill Zone: {is_kill_zone()[1]}
    """
    
    return summary, price, atr, trend

# --- GROQ BRAIN ---
def ask_groq(technicals, news, price):
    print(f"🧠 ANALYZING WITH SMC LOGIC...")
    
    # Initialize Groq Client
    try:
        client = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        print(f"❌ Groq Error: {e}")
        return None
    
    prompt = f"""
    Act as a Professional SMC Trader.
    
    DATA: 
    {technicals}
    NEWS: 
    {news}
    
    STRICT ENTRY RULES:
    1. BUY IF: Trend is BULLISH + Price Swept Liquidity Low OR Price is Inside Bullish FVG.
    2. SELL IF: Trend is BEARISH + Price Swept Liquidity High OR Price is Inside Bearish FVG.
    3. WAIT IF: No Sweep and No FVG Test.
    
    Output JSON ONLY:
    {{
      "signal": "BUY", "SELL", or "WAIT",
      "reasoning": "Technical reason",
      "entry_price": {price:.2f}
    }}
    """
    try:
        completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=MODEL_ID, temperature=0.1, response_format={"type": "json_object"}
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        print(f"❌ AI Error: {e}")
        return None

# --- EXECUTION ---
try:
    tech, price, atr, trend = get_smc_data()
    decision = ask_groq(tech, "No major news", price)

    if decision:
        print("\n" + "═"*40)
        print(f"🤖 SMC SNIPER REPORT")
        print("═"*40)
        print(f"SIGNAL: {decision.get('signal')}")
        print(f"REASON: {decision.get('reasoning')}")
        
        if decision.get('signal') == "BUY":
            sl = price - (atr * 1.5)
            tp = price + (atr * 3.0)
            print(f"🛑 STOP: {sl:.2f}")
            print(f"🎯 TARGET: {tp:.2f}")
            send_telegram(f"🚀 BUY GOLD\nEntry: {price:.2f}\nSL: {sl:.2f}\nTP: {tp:.2f}")
            
        elif decision.get('signal') == "SELL":
            sl = price + (atr * 1.5)
            tp = price - (atr * 3.0)
            print(f"🛑 STOP: {sl:.2f}")
            print(f"🎯 TARGET: {tp:.2f}")
            send_telegram(f"🔻 SELL GOLD\nEntry: {price:.2f}\nSL: {sl:.2f}\nTP: {tp:.2f}")
        
        print("═"*40)
except Exception as e:
    print(f"❌ Runtime Error: {e}")
