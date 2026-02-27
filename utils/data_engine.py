import yfinance as yf
import pandas as pd
from FinMind.data import DataLoader
from datetime import datetime, timedelta
import streamlit as st
import os
import re


class DataEngine:
    def __init__(self):
        self.dl = DataLoader()
        # 如果有 FinMind Token 可以加在這裡
        # self.dl.login_by_token("YOUR_TOKEN")

    @st.cache_data(ttl=3600)
    def get_price_data(_self, symbol, period="1y"):
        """抓取歷史股價數據"""
        try:
            df = yf.download(symbol, period=period, progress=False)
            if df.empty:
                return pd.DataFrame()
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # 計算基礎 MA
            df['MA5'] = df['Close'].rolling(window=5).mean()
            df['MA10'] = df['Close'].rolling(window=10).mean()
            df['MA20'] = df['Close'].rolling(window=20).mean()
            return df
        except Exception as e:
            st.error(f"價量數據抓取失敗: {e}")
            return pd.DataFrame()

    @st.cache_data(ttl=7200)
    def get_chip_data(_self, symbol, days=30):
        """抓取三大法人籌碼數據 (FinMind)"""
        pure_id = symbol.split('.')[0]
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        try:
            df = _self.dl.taiwan_stock_institutional_investors(
                stock_id=pure_id,
                start_date=start_date
            )
            if df.empty:
                return pd.DataFrame()

            # 整理數據：將買賣超張數合併
            df['net_buy'] = (df['buy'] - df['sell']) / 1000  # 轉為「張」
            pivot_df = df.groupby(['date', 'name'])['net_buy'].sum().unstack(fill_value=0)

            # 欄位正規化：確保外資/投信/自營商明確可辨識
            rename_map = {}
            for c in pivot_df.columns:
                if c == 'Foreign_Investor':
                    rename_map[c] = 'Foreign_Investor'
                elif c == 'Investment_Trust':
                    rename_map[c] = 'Investment_Trust'
                elif c in ('Dealer_self', 'Dealer_Hedging', 'Foreign_Dealer_Self'):
                    rename_map[c] = c  # 保留原名

            # 合併自營商子類為單一 Dealer 欄位
            dealer_cols = [c for c in pivot_df.columns
                           if c in ('Dealer_self', 'Dealer_Hedging', 'Foreign_Dealer_Self')]
            if dealer_cols:
                pivot_df['Dealer_Total'] = pivot_df[dealer_cols].sum(axis=1)

            return pivot_df
        except Exception as e:
            print(f"籌碼數據抓取失敗: {e}")
            return pd.DataFrame()

    @st.cache_data(ttl=3600)
    def load_inventory(_self):
        """讀取 memory/inventory.md 並解析持股"""
        path = "/home/skytiger/.openclaw/workspace/memory/inventory.md"
        if not os.path.exists(path):
            return []

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # 正則匹配表格行: | 股票名稱 (代號) | 持有股數 | 成本均價 | 備註 |
            rows = re.findall(
                r"\|\s*([^\|]+?)\s*\((\d{4,6}[A-Z]?)\)\s*\|\s*([\d,]+)\s*\|\s*([\d,.]+)\s*\|\s*(.*?)\s*\|",
                content
            )

            inventory = []
            for name, code, shares, price, note in rows:
                inventory.append({
                    "name": name.strip(),
                    "code": code.strip(),
                    "symbol": f"{code.strip()}.TW",
                    "shares": int(shares.strip().replace(',', '')),
                    "avg_price": float(price.strip().replace(',', '')),
                    "note": note.strip()
                })
            return inventory
        except Exception as e:
            st.error(f"讀取庫存失敗: {e}")
            return []

    def check_right_side_signal(self, df):
        """判斷右側交易訊號"""
        if df.empty or len(df) < 2:
            return "無資料"

        # 處理 MultiIndex (yfinance 有時回傳多層欄位)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(last['Close'])
        ma5 = float(last['MA5']) if pd.notna(last['MA5']) else None
        ma10 = float(last['MA10']) if pd.notna(last['MA10']) else None
        ma20 = float(last['MA20']) if pd.notna(last['MA20']) else None

        if ma5 is None or ma10 is None:
            return "🟡 資料不足"

        # 條件 1: 股價在 5MA 與 10MA 之上
        above_ma = close > ma5 and close > ma10

        # 條件 2: 5MA 向上
        prev_ma5 = float(prev['MA5']) if pd.notna(prev['MA5']) else ma5
        ma5_up = ma5 > prev_ma5

        if above_ma and ma5_up:
            return "🔥 強勢多頭 (右側確認)"
        elif above_ma:
            return "✅ 偏多整理"
        elif ma20 is not None and close < ma20:
            return "💀 跌破月線 (謹慎)"
        else:
            return "🟡 觀望"
