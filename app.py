import streamlit as st
import pandas as pd
import mplfinance as mpf
from utils.data_engine import DataEngine
from utils.tw_chip_scanner import (
    get_tw_chip_top_buys, scan_consecutive_buys,
    scan_chip_concentration, calc_chip_strength, run_full_scan,
)
from utils.ai_writer import generate_stock_script
from utils.ai_predictor import predict_stock_trend
from utils.openinsider_fetcher import get_latest_cluster_buys, get_tw_block_trades
from utils.stock2881int import calculate_arbitrage
from datetime import datetime
import numpy as np
import os
from dotenv import load_dotenv

load_dotenv()  # 自動載入 .env

# --- 1. 頁面配置 ---
st.set_page_config(page_title="Tiger AI 股市戰情室 v2.0", layout="wide")

# --- 2. 初始化引擎 ---
engine = DataEngine()

# --- 3. 側邊欄：設定與輸入 ---
st.sidebar.header("🎯 控制中心")
st.sidebar.info("Tiger AI v2.0 — 專注右側交易與籌碼分析")

# 讀取庫存
inventory = engine.load_inventory()

# 持股快選
if inventory:
    st.sidebar.subheader("💼 我的持股")
    inv_labels = [f"{item['name']} ({item['code']})" for item in inventory]
    selected_inv = st.sidebar.selectbox("從持股挑選", ["手動輸入"] + inv_labels)
    if selected_inv != "手動輸入":
        idx = inv_labels.index(selected_inv)
        st.session_state['auto_stock'] = inventory[idx]['symbol']
    else:
        st.session_state.pop('auto_stock', None)

# AI 設定
st.sidebar.divider()
st.sidebar.subheader("🔑 AI 設定")
default_api_key = os.getenv("GOOGLE_API_KEY", "")
gemini_api_key = st.sidebar.text_input("Gemini API Key", value=default_api_key, type="password")

# 更新按鈕
st.sidebar.divider()
if st.sidebar.button("🔄 清除快取 / 更新數據"):
    st.cache_data.clear()
    st.rerun()

# --- 4. 主畫面架構 ---
st.title("🐅 股市戰情室 v2.0")
st.caption(f"更新時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📉 個股戰情中心", "🕵️ 台股籌碼偵探", "💼 庫存健康監控", "🏦 大戶動向", "💰 質押套利試算"])

# ========================
# Tab 1: 個股戰情中心
# ========================
with tab1:
    col_input1, col_input2 = st.columns([3, 1])
    with col_input1:
        default_val = st.session_state.get('auto_stock', '2890.TW')
        target_stock = st.text_input("輸入代號 (例如: 2330.TW)", value=default_val)
    with col_input2:
        time_period = st.selectbox("觀測區間", ["3mo", "6mo", "1y", "2y"], index=2)

    if target_stock:
        with st.spinner('正在搜集數據中...'):
            price_df = engine.get_price_data(target_stock, period=time_period)
            try:
                chip_df = engine.get_chip_data(target_stock)
            except Exception:
                chip_df = pd.DataFrame()

        if not price_df.empty:
            # --- KPI 列 ---
            col1, col2, col3, col4 = st.columns(4)

            last_price = float(price_df['Close'].iloc[-1])
            prev_price = float(price_df['Close'].iloc[-2])
            change = last_price - prev_price
            change_pct = (change / prev_price) * 100

            col1.metric("目前股價", f"{last_price:.2f}",
                         f"{change:+.2f} ({change_pct:+.2f}%)")

            try:
                signal = engine.check_right_side_signal(price_df)
            except Exception:
                signal = "N/A"
            col2.metric("右側訊號", signal)

            # 法人概況
            if not chip_df.empty:
                cols = chip_df.columns.tolist()
                foreign_col = next((c for c in cols if c == 'Foreign_Investor'), None)
                trust_col = next((c for c in cols if c == 'Investment_Trust'), None)

                if foreign_col:
                    col3.metric("外資買賣超", f"{float(chip_df[foreign_col].iloc[-1]):+,.0f} 張")
                else:
                    col3.metric("外資買賣超", "N/A")

                if trust_col:
                    col4.metric("投信買賣超", f"{float(chip_df[trust_col].iloc[-1]):+,.0f} 張")
                else:
                    col4.metric("投信買賣超", "N/A")
            else:
                col3.metric("外資買賣超", "N/A")
                col4.metric("投信買賣超", "N/A")

            # --- K 線圖 ---
            st.subheader("📈 技術位階分析 (5MA / 10MA / 20MA)")
            mc = mpf.make_marketcolors(up='red', down='green',
                                        edge='inherit', wick='inherit', volume='in')
            s = mpf.make_mpf_style(base_mpf_style='yahoo', marketcolors=mc)

            try:
                fig, _ = mpf.plot(
                    price_df.tail(120),
                    type='candle', style=s, volume=True,
                    mav=(5, 10, 20),
                    figsize=(16, 8),
                    returnfig=True, tight_layout=True
                )
                st.pyplot(fig)
            except Exception as e:
                st.error(f"K 線圖繪製失敗: {e}")

            # --- Kronos AI 趨勢預測 ---
            st.subheader("🔮 AI 趨勢預測 (Kronos)")
            st.caption("基於 Kronos 時序基礎模型，預測未來 K 線走勢與支撐/壓力區間")

            col_k1, col_k2, col_k3 = st.columns(3)
            with col_k1:
                kronos_days = st.selectbox(
                    "預測天數", [3, 5, 10], index=1, key="kronos_days"
                )
            with col_k2:
                kronos_temp = st.select_slider(
                    "取樣溫度 (越低越保守)",
                    options=[0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
                    value=0.8, key="kronos_temp"
                )
            with col_k3:
                kronos_samples = st.selectbox(
                    "取樣次數 (越高越穩定)", [1, 3, 5], index=1, key="kronos_samples"
                )

            if st.button("🚀 啟動 Kronos 預測", key="kronos_btn"):
                with st.spinner("🧠 Kronos AI 運算中... (首次載入模型約需 30 秒)"):
                    try:
                        result = predict_stock_trend(
                            price_df=price_df,
                            pred_days=kronos_days,
                            temperature=kronos_temp,
                            sample_count=kronos_samples,
                        )

                        # --- 預測結果 KPI ---
                        st.divider()
                        kc1, kc2, kc3, kc4 = st.columns(4)
                        kc1.metric(
                            f"{result['trend_emoji']} AI 趨勢判斷",
                            result['trend'],
                        )
                        kc2.metric(
                            "預估漲跌幅",
                            f"{result['trend_pct']:+.2f}%",
                            f"目標價 {result['final_pred_close']:.2f}",
                        )
                        kc3.metric(
                            "📊 預估支撐",
                            f"{result['support']:.2f}",
                            f"距現價 {((result['support'] - result['last_close']) / result['last_close'] * 100):+.1f}%"
                        )
                        kc4.metric(
                            "📊 預估壓力",
                            f"{result['resistance']:.2f}",
                            f"距現價 {((result['resistance'] - result['last_close']) / result['last_close'] * 100):+.1f}%"
                        )

                        # 信心度
                        st.info(f"**信心度：** {result['confidence']}")

                        # --- 預測走勢圖 ---
                        st.markdown(f"**未來 {kronos_days} 日預測走勢**")
                        pred_df = result['pred_df']

                        # 組合歷史 + 預測收盤價做折線圖
                        hist_close = price_df.copy()
                        if isinstance(hist_close.columns, pd.MultiIndex):
                            hist_close.columns = hist_close.columns.get_level_values(0)
                        hist_tail = hist_close['Close'].tail(20).copy()
                        hist_tail.name = "歷史收盤"

                        pred_close = pred_df['close'].copy()
                        pred_close.name = "AI 預測"

                        # 用 bridge 把歷史最後一點接上預測第一點
                        bridge = pd.DataFrame({
                            "歷史收盤": [float(hist_tail.iloc[-1]), np.nan],
                            "AI 預測": [float(hist_tail.iloc[-1]), float(pred_close.iloc[0])],
                        }, index=[hist_tail.index[-1], pred_close.index[0]])

                        chart_hist = pd.DataFrame({"歷史收盤": hist_tail, "AI 預測": np.nan})
                        chart_pred = pd.DataFrame({"歷史收盤": np.nan, "AI 預測": pred_close})
                        chart_data = pd.concat([chart_hist, bridge, chart_pred])
                        chart_data = chart_data[~chart_data.index.duplicated(keep='last')]
                        chart_data = chart_data.sort_index()

                        st.line_chart(chart_data, use_container_width=True)

                        # --- 預測明細表 ---
                        with st.expander("📋 查看預測明細"):
                            display_pred = pred_df[['open', 'high', 'low', 'close']].copy()
                            display_pred.columns = ['開盤', '最高', '最低', '收盤']
                            display_pred.index.name = '日期'
                            st.dataframe(
                                display_pred.style.format("{:.2f}"),
                                use_container_width=True,
                            )

                    except Exception as e:
                        st.error(f"Kronos 預測失敗: {e}")
                        st.caption("可能原因：模型尚未下載、CUDA 記憶體不足、或歷史資料不足。")

            st.divider()

            # --- AI 解盤 ---
            st.subheader("🤖 Tiger AI 解盤")
            if st.button("🚀 生成 30s 影音腳本", key="ai_btn"):
                if gemini_api_key:
                    with st.spinner("Tiger AI 正在思考中..."):
                        script = generate_stock_script(
                            gemini_api_key, target_stock, target_stock,
                            price_df, chip_df
                        )
                    st.info(script)
                else:
                    st.warning("請在側邊欄設定 Gemini API Key")

            # --- 籌碼趨勢 ---
            if not chip_df.empty:
                st.subheader("🕵️ 三大法人籌碼趨勢 (近 20 日)")
                st.bar_chart(chip_df.tail(20))

                # --- 籌碼強度儀表板 ---
                st.subheader("💪 籌碼強度儀表板")
                chip_str = calc_chip_strength(chip_df)

                cs1, cs2, cs3, cs4, cs5 = st.columns(5)
                cs1.metric(
                    "籌碼評分",
                    f"{chip_str['chip_score']} / 100",
                    chip_str['chip_grade'],
                )
                cs2.metric(
                    "外資動向",
                    f"連{'買' if chip_str['foreign_streak'] >= 0 else '賣'} {abs(chip_str['foreign_streak'])} 日",
                    f"近5日 {chip_str['foreign_5d_sum']:+,.0f} 張",
                )
                cs3.metric(
                    "投信動向",
                    f"連{'買' if chip_str['trust_streak'] >= 0 else '賣'} {abs(chip_str['trust_streak'])} 日",
                    f"近5日 {chip_str['trust_5d_sum']:+,.0f} 張",
                )
                cs4.metric(
                    "三法人近10日",
                    f"{chip_str['total_10d_sum']:+,.0f} 張",
                    "合計淨買超" if chip_str['total_10d_sum'] > 0 else "合計淨賣超",
                )

                # 籌碼分數進度條
                with cs5:
                    st.markdown("**籌碼強度條**")
                    bar_pct = chip_str['chip_score'] / 100
                    st.progress(bar_pct)
                    if chip_str['chip_score'] >= 65:
                        st.success("法人積極佈局中")
                    elif chip_str['chip_score'] >= 45:
                        st.info("籌碼中性，觀察方向")
                    else:
                        st.warning("法人偏空，留意風險")

        else:
            st.error("無法找到該股票資料，請檢查代號是否正確 (台股請加 .TW)。")

# ========================
# Tab 2: 台股籌碼偵探
# ========================
with tab2:
    st.header("🔍 台股籌碼偵探 v2.0")
    st.markdown(
        "深度掃描台灣權值股與熱門標的，找出法人同步佈局、連續加碼、籌碼集中的潛力標的。"
    )

    # ── 一鍵全掃 ──────────────────────────────────────
    if st.button("🚀 一鍵啟動完整籌碼掃描", key="full_scan_btn", type="primary"):
        with st.spinner("正在連線 FinMind 全面截獲法人動向 (約 30-60 秒)..."):
            scan_results = run_full_scan()
            st.session_state['scan_results'] = scan_results

    if 'scan_results' not in st.session_state:
        st.info("👆 點擊上方按鈕啟動掃描，或選擇下方個別掃描功能。")
    else:
        scan = st.session_state['scan_results']
        st.success(f"✅ 掃描完成 — {scan['scan_time']}")

    # ── 分頁式結果展示 ──────────────────────────────────
    chip_tab1, chip_tab2, chip_tab3, chip_tab4 = st.tabs([
        "🔥 熱門掃描", "📈 連續買超", "🎯 籌碼集中度", "📊 單日法人"
    ])

    # ───────── 熱門掃描 ─────────
    with chip_tab1:
        st.subheader("🔥 今日熱門掃描")
        st.markdown("一眼掌握目前最值得關注的籌碼訊號。")

        if 'scan_results' in st.session_state:
            scan = st.session_state['scan_results']

            # (A) 土洋同買 (外資+投信同日淨買超)
            single = scan['single_day']
            if not single.empty:
                consensus = single[
                    (single['Foreign'] > 0) & (single['Trust'] > 0)
                ].copy()

                st.markdown("### 🤝 土洋同買 (外資+投信同步加碼)")
                if not consensus.empty:
                    consensus['同買力道'] = consensus['Foreign'] + consensus['Trust']
                    consensus = consensus.sort_values('同買力道', ascending=False)
                    st.dataframe(
                        consensus.style.format({
                            "Foreign": "{:+,.0f}", "Trust": "{:+,.0f}",
                            "Dealer": "{:+,.0f}", "Total": "{:+,.0f}",
                            "同買力道": "{:+,.0f}",
                        }).background_gradient(subset=['同買力道'], cmap='YlOrRd'),
                        use_container_width=True, hide_index=True,
                    )
                else:
                    st.warning("今日無土洋同買標的。")

            # (B) 連買 5 日精選
            cons5 = scan.get('consecutive_5d', pd.DataFrame())
            if not cons5.empty:
                st.markdown("### 🏆 法人連續買超 5 日以上")
                st.dataframe(
                    cons5.style.background_gradient(
                        subset=['外資連買天數', '投信連買天數'], cmap='Greens'
                    ),
                    use_container_width=True, hide_index=True,
                )
            else:
                st.info("目前無連買 5 日以上標的。")

            # (C) 籌碼高度集中
            conc = scan.get('concentration', pd.DataFrame())
            if not conc.empty:
                hot_conc = conc[conc['集中度(%)'] > 100]
                if not hot_conc.empty:
                    st.markdown("### 🎯 籌碼高度集中 (集中度 > 100%)")
                    st.dataframe(
                        hot_conc.style.background_gradient(
                            subset=['集中度(%)'], cmap='OrRd'
                        ),
                        use_container_width=True, hide_index=True,
                    )
        else:
            st.caption("請先點擊上方「一鍵啟動完整籌碼掃描」。")

    # ───────── 連續買超 ─────────
    with chip_tab2:
        st.subheader("📈 法人連續買超偵測")
        st.markdown("找出外資或投信連續 N 日淨買超的標的 — 持續加碼 = 有計畫性佈局。")

        cons_col1, cons_col2 = st.columns(2)
        with cons_col1:
            cons_days_opt = st.radio(
                "連續買超天數門檻", [3, 5], index=0, horizontal=True,
                key="cons_days_radio"
            )

        if 'scan_results' in st.session_state:
            key = f'consecutive_{cons_days_opt}d'
            cons_df = st.session_state['scan_results'].get(key, pd.DataFrame())
        else:
            cons_df = pd.DataFrame()

        if st.button(f"🔍 單獨掃描 (連買 ≥ {cons_days_opt} 日)", key="cons_scan_btn"):
            with st.spinner(f"掃描連續 {cons_days_opt} 日買超..."):
                cons_df = scan_consecutive_buys(cons_days_opt)

        if not cons_df.empty:
            st.success(f"找到 {len(cons_df)} 檔符合條件！")

            # 依標籤篩選
            all_tags = set()
            for t in cons_df['標籤'].values:
                for part in str(t).split('|'):
                    part = part.strip()
                    if part:
                        all_tags.add(part)

            if all_tags:
                tag_filter = st.multiselect(
                    "依標籤篩選", sorted(all_tags), default=list(all_tags),
                    key="cons_tag_filter"
                )
                if tag_filter:
                    mask = cons_df['標籤'].apply(
                        lambda x: any(t in str(x) for t in tag_filter)
                    )
                    cons_df = cons_df[mask]

            st.dataframe(
                cons_df.style.background_gradient(
                    subset=['外資連買天數', '投信連買天數'], cmap='Greens'
                ).background_gradient(
                    subset=['外資累計(張)', '投信累計(張)'], cmap='Blues'
                ),
                use_container_width=True, hide_index=True,
            )

            # 視覺化：連買天數比較
            if len(cons_df) > 0:
                chart_df = cons_df[['Stock', '外資連買天數', '投信連買天數']].set_index('Stock')
                st.bar_chart(chart_df, use_container_width=True)
        elif 'scan_results' in st.session_state or st.session_state.get('_cons_scanned'):
            st.info(f"目前無連買 ≥ {cons_days_opt} 日的標的。")

    # ───────── 籌碼集中度 ─────────
    with chip_tab3:
        st.subheader("🎯 籌碼集中度分析")
        st.markdown(
            "衡量近 N 日法人買賣超相對於市場的「吃貨力道」。"
            "集中度越高，代表法人佔整體交易比重越大，籌碼越乾淨。"
        )

        conc_days = st.selectbox(
            "分析區間 (交易日)", [5, 10, 20], index=1, key="conc_days"
        )

        if 'scan_results' in st.session_state and conc_days == 10:
            conc_df = st.session_state['scan_results'].get('concentration', pd.DataFrame())
        else:
            conc_df = pd.DataFrame()

        if st.button(f"🔍 掃描籌碼集中度 ({conc_days}日)", key="conc_scan_btn"):
            with st.spinner(f"計算近 {conc_days} 日籌碼集中度..."):
                conc_df = scan_chip_concentration(conc_days)

        if not conc_df.empty:
            st.success(f"分析完成，共 {len(conc_df)} 檔。")

            # 等級篩選
            levels = conc_df['集中度等級'].unique().tolist()
            level_filter = st.multiselect(
                "依集中度等級篩選", levels, default=levels, key="conc_level_filter"
            )
            filtered_conc = conc_df[conc_df['集中度等級'].isin(level_filter)]

            st.dataframe(
                filtered_conc.style.background_gradient(
                    subset=['集中度(%)'], cmap='YlOrRd'
                ),
                use_container_width=True, hide_index=True,
            )

            # 集中度排行圖
            top15 = filtered_conc.head(15)
            if not top15.empty:
                st.markdown("**📊 集中度 Top 15**")
                chart_conc = top15[['Stock', '集中度(%)']].set_index('Stock')
                st.bar_chart(chart_conc, use_container_width=True)

    # ───────── 單日法人 ─────────
    with chip_tab4:
        st.subheader("📊 單日法人買賣超總覽")
        st.markdown("掃描台灣 50 成份股與熱門個股，列出最新一日各法人淨買超。")

        if 'scan_results' in st.session_state:
            chip_scan_df = st.session_state['scan_results'].get('single_day', pd.DataFrame())
        else:
            chip_scan_df = pd.DataFrame()

        if st.button("🔍 單獨掃描單日法人", key="single_scan_btn"):
            with st.spinner("正在連線 FinMind 截獲法人動向..."):
                chip_scan_df = get_tw_chip_top_buys()

        if not chip_scan_df.empty:
            st.success(f"掃描完成！共分析 {len(chip_scan_df)} 檔。")

            min_buy = st.slider(
                "篩選買超張數 ≥", 0, 10000, 100, step=100, key="single_min_buy"
            )
            filtered = chip_scan_df[chip_scan_df['Total'] >= min_buy]

            st.dataframe(
                filtered.style.format({
                    "Foreign": "{:+,.0f}", "Trust": "{:+,.0f}",
                    "Dealer": "{:+,.0f}", "Total": "{:+,.0f}",
                }).background_gradient(subset=['Total'], cmap='Greens'),
                use_container_width=True, hide_index=True,
            )
        elif 'scan_results' in st.session_state:
            st.warning("目前抓取不到數據，可能是假日或資料尚未更新。")

# ========================
# Tab 3: 庫存健康監控
# ========================
with tab3:
    st.header("💼 持股健康度總覽")

    if inventory:
        # 建立庫存表格並加入即時股價
        results = []
        progress = st.progress(0, text="正在載入即時報價...")

        for i, item in enumerate(inventory):
            progress.progress((i + 1) / len(inventory),
                              text=f"載入 {item['name']}...")
            p_df = engine.get_price_data(item['symbol'], period="1mo")

            if not p_df.empty:
                current_price = float(p_df['Close'].iloc[-1])
                cost = item['avg_price']
                shares = item['shares']
                pnl = (current_price - cost) * shares
                pnl_pct = ((current_price - cost) / cost) * 100
                sig = engine.check_right_side_signal(p_df)
            else:
                current_price = 0
                pnl = 0
                pnl_pct = 0
                sig = "無資料"

            results.append({
                "股票": f"{item['name']} ({item['code']})",
                "持股": f"{item['shares']:,}",
                "成本均價": f"{item['avg_price']:.2f}",
                "現價": f"{current_price:.2f}",
                "損益": f"{pnl:+,.0f}",
                "報酬率": f"{pnl_pct:+.1f}%",
                "右側訊號": sig,
                "備註": item['note']
            })

        progress.empty()

        result_df = pd.DataFrame(results)
        st.dataframe(result_df, use_container_width=True, hide_index=True)

        # 統計
        total_cost = sum(item['avg_price'] * item['shares'] for item in inventory)
        total_market = sum(
            float(engine.get_price_data(item['symbol'], period="1mo")['Close'].iloc[-1])
            * item['shares']
            for item in inventory
            if not engine.get_price_data(item['symbol'], period="1mo").empty
        )
        total_pnl = total_market - total_cost

        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("總成本", f"${total_cost:,.0f}")
        c2.metric("總市值", f"${total_market:,.0f}")
        c3.metric("總損益", f"${total_pnl:+,.0f}",
                  f"{(total_pnl/total_cost)*100:+.1f}%" if total_cost > 0 else "")
    else:
        st.warning("找不到庫存資料 (agents/vault/vault_master.md)。")

# ========================
# Tab 4: 大戶動向
# ========================
with tab4:
    st.header("🏦 大戶動向 — 鉅額交易 & 美股內部人")

    sub_tab1, sub_tab2 = st.tabs(["🇹🇼 台股鉅額交易", "🇺🇸 美股內部人 (OpenInsider)"])

    with sub_tab1:
        st.markdown(
            "**鉅額交易**：成交 500 張以上或金額達 1,500 萬之大宗交易，"
            "反映法人與大戶的真實資金動向。"
        )

        if st.button("🔍 載入今日鉅額交易", key="block_btn"):
            with st.spinner("正在從證交所抓取鉅額交易資料..."):
                block_df = get_tw_block_trades()

            if not block_df.empty:
                st.success(f"今日共 {len(block_df)} 筆鉅額交易")

                # 篩選
                min_amount = st.slider(
                    "篩選成交金額 ≥ (萬元)", 0, 100000, 5000, step=1000,
                    key="block_slider"
                )
                filtered = block_df[block_df['成交金額'] >= min_amount * 10000]

                st.dataframe(filtered, use_container_width=True, hide_index=True)

                # 統計：哪些股票鉅額交易最多
                if '證券名稱' in block_df.columns and '成交金額' in block_df.columns:
                    st.subheader("🔥 鉅額交易金額排行 (依個股加總)")
                    agg = block_df.groupby(['證券代號', '證券名稱']).agg(
                        筆數=('成交金額', 'count'),
                        總金額=('成交金額', 'sum'),
                        總張數=('成交張數', 'sum')
                    ).sort_values('總金額', ascending=False).reset_index()

                    agg['總金額(億)'] = (agg['總金額'] / 1e8).round(2)
                    st.dataframe(agg.head(15), use_container_width=True, hide_index=True)

                    # 檢查我的持股是否出現在鉅額交易
                    if inventory:
                        my_codes = [item['code'] for item in inventory]
                        my_blocks = block_df[block_df['證券代號'].isin(my_codes)]
                        if not my_blocks.empty:
                            st.subheader("⚠️ 我的持股出現在鉅額交易！")
                            st.dataframe(my_blocks, use_container_width=True, hide_index=True)
            else:
                st.warning("目前無鉅額交易資料（可能是盤前或假日）。")

    with sub_tab2:
        st.markdown(
            "追蹤美股**內部人集中買入 (Cluster Buys)**，"
            "當多位高管同時買入自家股票，往往是強烈的信心訊號。"
        )

        if st.button("🔍 載入最新 Cluster Buys", key="insider_btn"):
            with st.spinner("正在從 OpenInsider 抓取數據..."):
                insider_df = get_latest_cluster_buys()

            if not insider_df.empty:
                st.success(f"取得 {len(insider_df)} 筆內部人交易紀錄")

                display_cols = [c for c in [
                    'Filing Date', 'Trade Date', 'Ticker', 'Insider Name',
                    'Title', 'Trade Type', 'Price', 'Qty', 'Value',
                    'ΔOwn', '1d', '1w', '1m', '6m'
                ] if c in insider_df.columns]

                st.dataframe(
                    insider_df[display_cols].head(30),
                    use_container_width=True, hide_index=True
                )

                if 'Ticker' in insider_df.columns:
                    st.subheader("🔥 內部人集中買入排行")
                    ticker_counts = insider_df['Ticker'].value_counts().head(10)
                    st.bar_chart(ticker_counts)
            else:
                st.warning("目前無法取得 OpenInsider 數據，請稍後再試。")

# ========================
# Tab 5: 質押套利試算
# ========================
with tab5:
    calculate_arbitrage()

# --- 6. 頁腳 ---
st.markdown("---")
st.caption("🐅 Tiger AI 數據智囊團 | 僅供參考，投資請自行承擔風險。")
