# Stock War Room v2.0 (股市戰情室)

## 專案簡介
這是一個專為「理性的冒險家」設計的台股分析與自動化報表系統。結合了 Python、Streamlit 與 FinMind API，提供精準的籌碼分析、移動平均線 (MA) 強度監控與自動化報告。

## 核心功能
- **股市戰情中心**: 監控大盤與核心標的（台積電、聯發科等）。
- **籌碼強度儀表板**: 分析外資、投信與三大法人的買賣超動向。
- **自動化月報 (ETF 月報)**: 定期生成分析報告並透過電子郵件發送。
- **質押雙配息套利計算機**: 針對任何股票 (預設以 2881 為例)，提供最新股價連動與質押、配股配息套利情境的量化結算。
- **健康數據整合**: 同步個人運動數據 (Garmin) 與健檢報告分析。

## 技術棧
- **語言**: Python 3.12+ (WSL/Ubuntu 環境)
- **視覺化**: Streamlit
- **數據源**: FinMind API, Yahoo Finance
- **人工智慧**: Google GenAI SDK (Gemini), OpenRouter
- **自動化**: OpenClaw Gateway Scheduler

## 安裝與執行
1. **環境設定**:
   ```bash
   python3 -m venv venv_linux
   source venv_linux/bin/activate
   pip install -r requirements.txt
   ```
2. **啟動 Dashboard**:
   ```bash
   streamlit run app.py
   ```

## 開發紀錄
- **2026-03-07**: 遷移至全新 `google-genai` SDK，整合 `.env` 環境變數機制；庫存清單改以 `vault_master.md` 為唯一真理，並支援 Windows 對應 WSL UNC 絕對路徑；此外新增「質押雙配息套利計算機」。
- **2026-02-24**: 修復 FinMind API 抓取邏輯與 GitHub 版本控管初始化。

---
Developed with ❤️ by Skytiger & **Google Deepmind Antigravity Team**
