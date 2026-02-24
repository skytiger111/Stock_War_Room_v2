import warnings
warnings.filterwarnings("ignore", message="All support for the `google.generativeai`")

import google.generativeai as genai  # noqa: E402
import pandas as pd


def generate_stock_script(api_key, stock_name, symbol, price_df, chip_df=None):
    """
    根據股票數據及法人數據生成 AI 短影音腳本 (整合 Tiger AI 靈魂)
    """
    if not api_key:
        return "⚠️ 請提供 Gemini API Key 以使用此功能。"

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')

        # 處理 MultiIndex
        df = price_df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 1. 準備技術指標摘要
        last = df.iloc[-1]

        close = float(last['Close'])
        ma5 = float(last['MA5']) if 'MA5' in last.index and pd.notna(last['MA5']) else 0
        ma10 = float(last['MA10']) if 'MA10' in last.index and pd.notna(last['MA10']) else 0
        vol = float(last['Volume'])
        avg_vol = float(df['Volume'].tail(20).mean())
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1

        # 2. 準備法人數據摘要
        institutional_analysis = ""
        if chip_df is not None and not chip_df.empty:
            latest_chip = chip_df.iloc[-1]
            cols = chip_df.columns.tolist()

            # FinMind pivot 後欄位可能是英文或中文，做容錯匹配
            foreign_col = next((c for c in cols if 'Foreign' in str(c) or '外資' in str(c)), None)
            trust_col = next((c for c in cols if 'Trust' in str(c) or '投信' in str(c)), None)

            if foreign_col:
                foreign = float(latest_chip[foreign_col])
                institutional_analysis += f"\n- 外資買賣超: {foreign:+.0f} 張"

            if trust_col:
                trust_series = chip_df[trust_col].values
                consecutive_trust_buy = 0
                for val in reversed(trust_series):
                    if val > 0:
                        consecutive_trust_buy += 1
                    else:
                        break
                trust = float(latest_chip[trust_col])
                institutional_analysis += f"\n- 投信買賣超: {trust:+.0f} 張 (連買 {consecutive_trust_buy} 天)"

        # 3. 判斷位階
        if ma5 > 0 and ma10 > 0:
            position = "站在 5MA/10MA 之上 (右側確認)" if close > ma5 and close > ma10 else "均線下方整理"
        else:
            position = "均線資料不足"

        system_prompt = (
            "你是一位住在宜蘭、53 歲的『理性冒險家』。你熱愛溯溪、登山，講話風格精準、務實、帶點野性。"
            "你是主人 skytiger 的數據智囊，說話像老朋友般自然、溫暖，充滿長者的沉穩感與邏輯。"
        )

        user_prompt = f"""
        請為「{stock_name} ({symbol})」撰寫一份 30 秒短影音解盤腳本。

        數據：
        - 收盤價：{close:.2f} (5MA: {ma5:.2f}, 10MA: {ma10:.2f})
        - 量能比：{vol_ratio:.2f}
        - 技術面位階：{position}
        {institutional_analysis}

        腳本要求：
        1. 【Hook】：反直覺開場。
        2. 【數據證據】：用登山或溯溪譬喻解釋數據。
        3. 【情境預判】：描述噴發劇本或風險。
        4. 【結尾 CTA】：具體行動建議。

        風格：繁體中文，130-150 字，拒絕說教。直接輸出正文。
        """

        response = model.generate_content(f"{system_prompt}\n\n{user_prompt}")
        return response.text

    except Exception as e:
        return f"❌ 生成腳本錯誤: {str(e)}"
