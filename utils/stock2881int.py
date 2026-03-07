import streamlit as st
import pandas as pd
import yfinance as yf


def calculate_arbitrage():
    st.subheader("💰 質押雙配息套利計算機")

    st.markdown("### ⚙️ 參數設定")

    # 股票輸入列
    col_stock1, col_stock2 = st.columns([1, 2])
    with col_stock1:
        stock_code = st.text_input("股票代號", value="2881", key="arb_stock_code")
    with col_stock2:
        # 即時抓取股票名稱與股價
        symbol = f"{stock_code}.TW"
        stock_name = stock_code
        live_price = 0.0
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            stock_name = info.get("shortName") or info.get("longName") or stock_code
            hist = ticker.history(period="1d")
            if not hist.empty:
                live_price = float(hist["Close"].iloc[-1])
        except Exception:
            pass
        st.metric("股票名稱", stock_name)

    col1, col2 = st.columns(2)

    with col1:
        current_price = st.number_input(
            "目前股價 (元)", value=live_price if live_price > 0 else 89.60, step=0.1,
            key="arb_price"
        )
        total_shares_k = st.number_input("總持股張數 (張)", value=20, step=1, key="arb_shares")
        cash_dividend = st.slider("預估現金股利 (元/股)", min_value=0.0, max_value=10.0, value=3.0, step=0.1, key="arb_cash_div")
        stock_dividend = st.slider("預估股票股利 (元/股)", min_value=0.0, max_value=5.0, value=0.5, step=0.1, key="arb_stock_div")

    with col2:
        loan_amount = st.number_input("質押借款總額 (元)", value=800000, step=10000, key="arb_loan")
        interest_rate = st.slider("質押年利率 (%)", min_value=1.0, max_value=5.0, value=2.5, step=0.1, key="arb_rate")

    st.markdown("---")

    # 核心計算邏輯
    total_shares = total_shares_k * 1000

    # 1. 支出：計算年利息
    yearly_interest = loan_amount * (interest_rate / 100)

    # 2. 收入：計算現金股利
    total_cash_dividend = total_shares * cash_dividend

    # 3. 收入：計算配股股數
    # 股票股利 1 元代表配發 0.1 股 (即 100 股/張)
    new_shares_received = total_shares * (stock_dividend / 10)

    # 4. 現金流計算 (現金股利 - 利息)
    net_cash_flow = total_cash_dividend - yearly_interest

    # 5. 除權息參考價計算
    # 公式：(除息前股價 - 現金股利) / (1 + 股票股利/10)
    ex_dividend_price = (current_price - cash_dividend) / (1 + (stock_dividend / 10))

    # 6. 新增股票價值
    new_shares_value = new_shares_received * ex_dividend_price

    # 7. 總套利價值
    total_arbitrage_value = net_cash_flow + new_shares_value

    st.markdown(f"### 🏆 {stock_name} ({stock_code}) 雙配息套利戰果結算")

    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("實收淨現金流 (已扣利息)", f"{int(net_cash_flow):,} 元")
    metric_col2.metric("免費獲得配股", f"{int(new_shares_received):,} 股")
    metric_col3.metric("總套利價值 (現金+股票)", f"{int(total_arbitrage_value):,} 元")

    st.markdown("#### 📝 詳細數據解析")
    data = {
        "項目": ["總借款利息支出", "總現金股利收入", "除權息參考價", "新增股票等值市值"],
        "金額 / 數據": [
            f"- {int(yearly_interest):,} 元",
            f"+ {int(total_cash_dividend):,} 元",
            f"{ex_dividend_price:.2f} 元",
            f"+ {int(new_shares_value):,} 元"
        ]
    }
    df = pd.DataFrame(data)
    st.table(df)

    st.info(
        f"💡 **戰略洞察**：這 {loan_amount:,} 元的借款，不僅用 {stock_name} 發的現金幫您把利息全繳清了，"
        f"還為您創造了 {int(net_cash_flow):,} 元的現金，"
        f"並為您的資產庫免費增添了 {int(new_shares_received):,} 股的金雞母！"
    )


if __name__ == "__main__":
    calculate_arbitrage()