"""
Kronos AI 趨勢預測模組
封裝 Kronos 時序預測模型，提供股價趨勢預測、支撐/壓力區間估算。
"""

import sys
import os
import numpy as np
import pandas as pd
import torch
import streamlit as st
import yfinance as yf
from datetime import timedelta

# Kronos 模型路徑
KRONOS_ROOT = "/mnt/d/code/Kronos_Test"
if KRONOS_ROOT not in sys.path:
    sys.path.insert(0, KRONOS_ROOT)


# ─── 模型載入 (全域快取，只載入一次) ───────────────────────────
@st.cache_resource(show_spinner="🧠 正在載入 Kronos AI 模型 (首次約需 30 秒)...")
def load_kronos_models():
    """載入 Kronos Tokenizer + Model，回傳 KronosPredictor 實例。"""
    from model import Kronos, KronosTokenizer, KronosPredictor

    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, max_context=512)
    return predictor


# ─── 資料準備 ─────────────────────────────────────────────────
def _prepare_kronos_input(price_df: pd.DataFrame, lookback: int = 100):
    """
    將 yfinance 格式的 price_df 轉為 Kronos 所需格式。
    回傳 (x_df, x_timestamp, last_close, last_date)
    """
    df = price_df.copy()

    # 處理 MultiIndex columns (yfinance 特性)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 統一欄名到 Kronos 小寫格式
    rename_map = {
        'Open': 'open', 'High': 'high', 'Low': 'low',
        'Close': 'close', 'Volume': 'volume'
    }
    df = df.rename(columns=rename_map)

    # 補上 amount (Kronos 需要)
    if 'amount' not in df.columns:
        df['amount'] = df['close'] * df['volume']

    # 確保 index 是 DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # 取最後 lookback 根 K 棒
    lookback = min(lookback, len(df))
    df_tail = df.iloc[-lookback:]

    x_df = df_tail[['open', 'high', 'low', 'close', 'volume', 'amount']].copy()
    x_timestamp = pd.Series(df_tail.index).reset_index(drop=True)
    last_close = float(df_tail['close'].iloc[-1])
    last_date = df_tail.index[-1]

    return x_df, x_timestamp, last_close, last_date


def _generate_future_timestamps(last_date, pred_days: int = 5):
    """生成未來 N 個交易日的時間戳記。"""
    future_dates = []
    current = last_date
    added = 0
    while added < pred_days:
        current = current + timedelta(days=1)
        # 跳過週末
        if current.weekday() < 5:
            future_dates.append(current)
            added += 1
    return pd.Series(pd.to_datetime(future_dates))


# ─── 主預測函式 ───────────────────────────────────────────────
def predict_stock_trend(
    price_df: pd.DataFrame,
    pred_days: int = 5,
    lookback: int = 100,
    temperature: float = 0.8,
    top_p: float = 0.9,
    sample_count: int = 3,
) -> dict:
    """
    對指定股票執行 Kronos 趨勢預測。

    Parameters
    ----------
    price_df : pd.DataFrame
        yfinance 格式的歷史股價 (需含 Open/High/Low/Close/Volume)
    pred_days : 預測天數 (預設 5 個交易日)
    lookback : 使用多少根歷史 K 棒作為上下文
    temperature : 取樣溫度 (越低越保守)
    top_p : nucleus sampling 閾值
    sample_count : 多次取樣取平均 (越高越穩定，但越慢)

    Returns
    -------
    dict with keys:
        - pred_df: 預測結果 DataFrame
        - last_close: 最後實際收盤價
        - trend: 趨勢判斷 (看漲/看跌/盤整)
        - trend_pct: 預估漲跌幅 %
        - support: 預估支撐價
        - resistance: 預估壓力價
        - confidence: 信心描述
        - pred_days: 預測天數
    """
    predictor = load_kronos_models()

    # 準備輸入
    x_df, x_timestamp, last_close, last_date = _prepare_kronos_input(price_df, lookback)
    y_timestamp = _generate_future_timestamps(last_date, pred_days)

    # 執行預測
    pred_df = predictor.predict(
        df=x_df,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=pred_days,
        T=temperature,
        top_p=top_p,
        sample_count=sample_count,
        verbose=False,
    )

    # ─── 分析預測結果 ────────────────────────────────────────
    pred_closes = pred_df['close'].values
    pred_highs = pred_df['high'].values
    pred_lows = pred_df['low'].values

    avg_pred_close = float(np.mean(pred_closes))
    final_pred_close = float(pred_closes[-1])
    trend_pct = ((final_pred_close - last_close) / last_close) * 100

    # 支撐 / 壓力區間
    support = float(np.min(pred_lows))
    resistance = float(np.max(pred_highs))

    # 趨勢判斷
    if trend_pct > 1.5:
        trend = "🟢 看漲"
        trend_emoji = "📈"
    elif trend_pct < -1.5:
        trend = "🔴 看跌"
        trend_emoji = "📉"
    else:
        trend = "🟡 盤整"
        trend_emoji = "↔️"

    # 信心度 (基於預測路徑的一致性)
    close_std = float(np.std(pred_closes))
    close_range = resistance - support
    volatility_ratio = close_range / last_close * 100

    if volatility_ratio < 3:
        confidence = "⭐⭐⭐ 高信心 (預測路徑收斂)"
    elif volatility_ratio < 6:
        confidence = "⭐⭐ 中等信心"
    else:
        confidence = "⭐ 低信心 (波動大，僅供參考)"

    return {
        "pred_df": pred_df,
        "last_close": last_close,
        "trend": trend,
        "trend_emoji": trend_emoji,
        "trend_pct": trend_pct,
        "final_pred_close": final_pred_close,
        "support": support,
        "resistance": resistance,
        "confidence": confidence,
        "pred_days": pred_days,
    }
