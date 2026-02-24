import requests
import pandas as pd


def get_latest_cluster_buys():
    """
    原 OpenInsider 美股內部人集中買入 (保留備用)
    """
    url = "http://openinsider.com/latest-cluster-buys"
    try:
        dfs = pd.read_html(url)
        df = max(dfs, key=lambda x: x.shape[0])
        df.columns = df.columns.str.replace(r'\xa0', ' ', regex=True)
        df = df.dropna(axis=1, how='all')

        if 'Filing Date' in df.columns:
            df['Filing Date'] = pd.to_datetime(df['Filing Date'], errors='coerce')
        if 'Trade Date' in df.columns:
            df['Trade Date'] = pd.to_datetime(df['Trade Date'], errors='coerce')

        currency_cols = ['Price', 'Value']
        for col in currency_cols:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False)
                df[col] = pd.to_numeric(df[col], errors='coerce')

        pct_cols = ['ΔOwn', '1d', '1w', '1m', '6m']
        for col in pct_cols:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace('%', '', regex=False).str.replace('>', '', regex=False).str.replace('+', '', regex=False)
                df[col] = pd.to_numeric(df[col], errors='coerce')

        return df
    except Exception as e:
        print(f"Error fetching OpenInsider data: {e}")
        return pd.DataFrame()


def get_tw_block_trades():
    """
    取得台股今日鉅額交易資料 (證交所 API)
    鉅額交易 = 大戶/法人之間的大宗買賣，門檻 500 張或成交金額 1,500 萬以上
    """
    url = "https://www.twse.com.tw/rwd/zh/block/BFIAUU"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        r = requests.get(url, params={'response': 'json'}, headers=headers, timeout=15)
        data = r.json()

        if data.get('stat') != 'OK' or 'data' not in data or not data['data']:
            return pd.DataFrame()

        df = pd.DataFrame(data['data'], columns=data['fields'])

        # 清理數值欄位
        for col in ['成交價', '成交股數', '成交金額']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace(',', '', regex=False)
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 計算張數
        if '成交股數' in df.columns:
            df['成交張數'] = (df['成交股數'] / 1000).astype(int)

        # 排序：成交金額由大到小
        if '成交金額' in df.columns:
            df = df.sort_values('成交金額', ascending=False)

        return df

    except Exception as e:
        print(f"Error fetching TW block trades: {e}")
        return pd.DataFrame()


if __name__ == "__main__":
    print("=== 台股鉅額交易 ===")
    df = get_tw_block_trades()
    if not df.empty:
        print(f"共 {len(df)} 筆")
        print(df.head(10).to_string())
    else:
        print("無資料")
