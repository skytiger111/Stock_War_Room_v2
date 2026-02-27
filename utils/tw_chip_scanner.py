"""
台股籌碼掃描引擎 v2.0
提供法人買賣超掃描、連續買超偵測、籌碼集中度計算。
"""

import pandas as pd
from FinMind.data import DataLoader
from datetime import datetime, timedelta
import streamlit as st


# ─── 掃描標的清單 ───────────────────────────────────────────
SCAN_TARGETS = list(set([
    # 台灣 50 核心
    "2330", "2317", "2454", "2308", "2303", "2881", "2882", "2886", "2884", "2891",
    "2892", "5880", "2880", "2885", "2887", "2890", "3037", "3008", "2327", "2357",
    "2382", "3231", "2376", "2377", "2603", "2609", "2615", "2610", "2618", "2002",
    "1301", "1303", "1326", "6505", "1216", "2912", "9910", "1476", "1101", "1102",
    # 熱門 ETF
    "0050", "0056", "00878", "00919", "00929",
    # AI / 半導體概念
    "2383", "2356", "6669", "3035", "3017", "2345", "4938", "3711",
    # 其他權值
    "2301", "2409", "3481", "2606", "2637",
]))


# ─── 內部工具 ─────────────────────────────────────────────
def _fetch_chip_history(dl: DataLoader, stock_id: str, days: int = 30) -> pd.DataFrame:
    """
    抓取單檔個股的法人買賣超歷史 (回傳 pivot: index=date, columns=法人名稱, values=net_buy 張)。
    """
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')

    try:
        df = dl.taiwan_stock_institutional_investors(
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )
        if df.empty:
            return pd.DataFrame()

        df['net_buy'] = (df['buy'] - df['sell']) / 1000  # 轉為張
        pivot = df.groupby(['date', 'name'])['net_buy'].sum().unstack(fill_value=0)
        pivot.index = pd.to_datetime(pivot.index)
        pivot = pivot.sort_index()
        return pivot
    except Exception:
        return pd.DataFrame()


def _resolve_columns(pivot: pd.DataFrame):
    """找出 Foreign / Trust / Dealer 對應欄位名稱。"""
    cols = pivot.columns.tolist()
    foreign_col = next((c for c in cols if c == 'Foreign_Investor'), None)
    trust_col = next((c for c in cols if c == 'Investment_Trust'), None)
    dealer_cols = [c for c in cols if 'Dealer' in str(c)]
    return foreign_col, trust_col, dealer_cols


# ─── 公開 API ─────────────────────────────────────────────

def get_tw_chip_top_buys():
    """
    原始單日掃描 — 回傳各標的最新一日的法人淨買超。
    (保持向後相容)
    """
    dl = DataLoader()
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')

    results = []
    for stock_id in SCAN_TARGETS:
        try:
            df = dl.taiwan_stock_institutional_investors(
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
            if df.empty:
                continue

            latest_date = df['date'].max()
            latest = df[df['date'] == latest_date]

            foreign = (latest[latest['name'] == 'Foreign_Investor']['buy'].sum()
                       - latest[latest['name'] == 'Foreign_Investor']['sell'].sum())
            trust = (latest[latest['name'] == 'Investment_Trust']['buy'].sum()
                     - latest[latest['name'] == 'Investment_Trust']['sell'].sum())
            dealer = (latest[latest['name'].str.contains('Dealer')]['buy'].sum()
                      - latest[latest['name'].str.contains('Dealer')]['sell'].sum())

            results.append({
                "Stock": stock_id, "Date": latest_date,
                "Foreign": int(foreign / 1000), "Trust": int(trust / 1000),
                "Dealer": int(dealer / 1000), "Total": int((foreign + trust + dealer) / 1000),
            })
        except Exception:
            pass

    if results:
        return pd.DataFrame(results).sort_values('Total', ascending=False)
    return pd.DataFrame()


# ─── 連續買超掃描 ─────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def scan_consecutive_buys(consecutive_days: int = 3) -> pd.DataFrame:
    """
    掃描 SCAN_TARGETS，找出外資或投信連續 N 日淨買超的標的。

    Returns DataFrame:
        Stock, 外資連買天數, 外資累計張數, 投信連買天數, 投信累計張數,
        三大法人合計, 最大連買來源, 標籤
    """
    dl = DataLoader()
    results = []

    for stock_id in SCAN_TARGETS:
        pivot = _fetch_chip_history(dl, stock_id, days=max(consecutive_days * 3, 20))
        if pivot.empty or len(pivot) < consecutive_days:
            continue

        foreign_col, trust_col, dealer_cols = _resolve_columns(pivot)

        # 計算外資連續買超天數
        foreign_streak, foreign_accum = 0, 0.0
        if foreign_col:
            vals = pivot[foreign_col].values
            for v in reversed(vals):
                if v > 0:
                    foreign_streak += 1
                    foreign_accum += v
                else:
                    break

        # 計算投信連續買超天數
        trust_streak, trust_accum = 0, 0.0
        if trust_col:
            vals = pivot[trust_col].values
            for v in reversed(vals):
                if v > 0:
                    trust_streak += 1
                    trust_accum += v
                else:
                    break

        # 自營商合計最新一日
        dealer_latest = sum(pivot[c].iloc[-1] for c in dealer_cols) if dealer_cols else 0

        # 三大法人最新日合計
        foreign_latest = pivot[foreign_col].iloc[-1] if foreign_col else 0
        trust_latest = pivot[trust_col].iloc[-1] if trust_col else 0
        total_latest = foreign_latest + trust_latest + dealer_latest

        # 只保留至少一方連買 >= consecutive_days
        if foreign_streak >= consecutive_days or trust_streak >= consecutive_days:
            # 標籤
            tags = []
            if foreign_streak >= consecutive_days and trust_streak >= consecutive_days:
                tags.append("🔥 土洋同步連買")
            elif foreign_streak >= consecutive_days:
                tags.append("🌍 外資連買")
            elif trust_streak >= consecutive_days:
                tags.append("🏦 投信連買")

            max_source = "外資" if foreign_streak >= trust_streak else "投信"

            results.append({
                "Stock": stock_id,
                "外資連買天數": foreign_streak,
                "外資累計(張)": int(foreign_accum),
                "投信連買天數": trust_streak,
                "投信累計(張)": int(trust_accum),
                "今日合計(張)": int(total_latest),
                "最強主力": max_source,
                "標籤": " | ".join(tags),
            })

    if results:
        df = pd.DataFrame(results)
        # 排序：土洋同步優先，其次連買天數
        df['_sort'] = df['外資連買天數'] + df['投信連買天數']
        df = df.sort_values('_sort', ascending=False).drop(columns=['_sort'])
        return df
    return pd.DataFrame()


# ─── 籌碼集中度 ─────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def scan_chip_concentration(lookback_days: int = 10) -> pd.DataFrame:
    """
    計算籌碼集中度：近 N 日法人合計淨買超 / 該期間平均成交量。
    數值越高 = 法人吃貨占比越高 = 籌碼越集中。

    Returns DataFrame:
        Stock, 外資淨買(張), 投信淨買(張), 法人合計(張),
        集中度(%), 集中度等級
    """
    dl = DataLoader()
    results = []

    for stock_id in SCAN_TARGETS:
        pivot = _fetch_chip_history(dl, stock_id, days=max(lookback_days * 2, 20))
        if pivot.empty or len(pivot) < 3:
            continue

        foreign_col, trust_col, dealer_cols = _resolve_columns(pivot)
        tail = pivot.tail(lookback_days)

        foreign_sum = tail[foreign_col].sum() if foreign_col else 0
        trust_sum = tail[trust_col].sum() if trust_col else 0
        dealer_sum = sum(tail[c].sum() for c in dealer_cols) if dealer_cols else 0
        total_sum = foreign_sum + trust_sum + dealer_sum

        # 簡易集中度 = 法人合計 / 近 N 日每日平均淨量的絕對值
        # 用各法人每日淨量的絕對值平均做分母，衡量「法人佔市場的力道」
        daily_totals = tail.sum(axis=1).abs()
        avg_daily = daily_totals.mean() if daily_totals.mean() != 0 else 1
        concentration = (total_sum / avg_daily) * 100 if avg_daily > 0 else 0

        # 等級
        if concentration > 200:
            level = "🔴 極度集中"
        elif concentration > 100:
            level = "🟠 高度集中"
        elif concentration > 50:
            level = "🟡 中度集中"
        elif concentration > 0:
            level = "🟢 輕度集中"
        else:
            level = "⚪ 分散 / 賣超"

        results.append({
            "Stock": stock_id,
            f"外資{lookback_days}日淨買(張)": int(foreign_sum),
            f"投信{lookback_days}日淨買(張)": int(trust_sum),
            f"法人合計(張)": int(total_sum),
            "集中度(%)": round(concentration, 1),
            "集中度等級": level,
        })

    if results:
        df = pd.DataFrame(results)
        df = df.sort_values('集中度(%)', ascending=False)
        return df
    return pd.DataFrame()


# ─── 個股籌碼強度 (給 Tab 1 用) ─────────────────────────────

def calc_chip_strength(chip_df: pd.DataFrame) -> dict:
    """
    針對單一個股的 chip_df (from DataEngine.get_chip_data) 計算籌碼強度指標。

    Returns dict:
        foreign_streak: 外資連買天數 (負數=連賣)
        trust_streak: 投信連買天數 (負數=連賣)
        foreign_5d_sum: 外資近5日合計
        trust_5d_sum: 投信近5日合計
        total_10d_sum: 三法人近10日合計
        chip_score: 綜合籌碼分數 (0~100)
        chip_grade: 等級文字
        chip_color: 顏色碼
    """
    if chip_df.empty:
        return {
            "foreign_streak": 0, "trust_streak": 0,
            "foreign_5d_sum": 0, "trust_5d_sum": 0,
            "total_10d_sum": 0, "chip_score": 0,
            "chip_grade": "無資料", "chip_color": "gray",
        }

    cols = chip_df.columns.tolist()
    foreign_col = next((c for c in cols if c == 'Foreign_Investor'), None)
    trust_col = next((c for c in cols if c == 'Investment_Trust'), None)

    # 連買天數 (正=連買, 負=連賣)
    def _streak(series):
        if series.empty:
            return 0
        vals = series.dropna().values
        if len(vals) == 0:
            return 0
        direction = 1 if vals[-1] > 0 else -1
        count = 0
        for v in reversed(vals):
            if (direction > 0 and v > 0) or (direction < 0 and v < 0):
                count += 1
            else:
                break
        return count * direction

    foreign_streak = _streak(chip_df[foreign_col]) if foreign_col else 0
    trust_streak = _streak(chip_df[trust_col]) if trust_col else 0

    # 近 5 日 / 10 日合計
    foreign_5d = chip_df[foreign_col].tail(5).sum() if foreign_col else 0
    trust_5d = chip_df[trust_col].tail(5).sum() if trust_col else 0
    total_10d = chip_df.tail(10).sum().sum()

    # 籌碼分數 (0~100)
    score = 50  # 基準分
    # 外資連買/連賣 (+/- 最多 20 分)
    score += min(max(foreign_streak * 4, -20), 20)
    # 投信連買/連賣 (+/- 最多 15 分)
    score += min(max(trust_streak * 3, -15), 15)
    # 近 5 日外資方向 (+/- 最多 10 分)
    if foreign_5d > 0:
        score += min(10, 10)
    elif foreign_5d < 0:
        score -= min(10, 10)
    # 近 5 日投信方向 (+/- 最多 5 分)
    if trust_5d > 0:
        score += 5
    elif trust_5d < 0:
        score -= 5

    score = max(0, min(100, score))

    # 等級
    if score >= 80:
        grade, color = "🔥 極強", "#FF4B4B"
    elif score >= 65:
        grade, color = "💪 偏強", "#FF8C00"
    elif score >= 45:
        grade, color = "😐 中性", "#FFD700"
    elif score >= 25:
        grade, color = "⚠️ 偏弱", "#87CEEB"
    else:
        grade, color = "💀 極弱", "#4169E1"

    return {
        "foreign_streak": foreign_streak,
        "trust_streak": trust_streak,
        "foreign_5d_sum": round(float(foreign_5d), 1),
        "trust_5d_sum": round(float(trust_5d), 1),
        "total_10d_sum": round(float(total_10d), 1),
        "chip_score": score,
        "chip_grade": grade,
        "chip_color": color,
    }


# ─── 熱門掃描總入口 ─────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def run_full_scan() -> dict:
    """
    一次完整掃描，回傳所有結果。
    Returns dict:
        single_day: 單日法人買賣超 DataFrame
        consecutive_3d: 連續 3 日買超 DataFrame
        consecutive_5d: 連續 5 日買超 DataFrame
        concentration: 籌碼集中度 DataFrame
        scan_time: 掃描完成時間
    """
    single_day = get_tw_chip_top_buys()
    consecutive_3d = scan_consecutive_buys(3)
    consecutive_5d = scan_consecutive_buys(5)
    concentration = scan_chip_concentration(10)

    return {
        "single_day": single_day,
        "consecutive_3d": consecutive_3d,
        "consecutive_5d": consecutive_5d,
        "concentration": concentration,
        "scan_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


if __name__ == "__main__":
    print("=== 連續 3 日買超 ===")
    df3 = scan_consecutive_buys(3)
    print(df3.head(10) if not df3.empty else "無結果")

    print("\n=== 籌碼集中度 ===")
    conc = scan_chip_concentration(10)
    print(conc.head(10) if not conc.empty else "無結果")
