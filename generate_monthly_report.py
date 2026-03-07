#!/usr/bin/env python3
"""
ETF 全能月報 自動化生成腳本 v2
═══════════════════════════════════════════════════
動態讀取 vault_master.md 持股 → 抓取數據 → 生成 HTML 月報 → Email 寄送。
設計為 one-shot 執行，可直接掛載 cron。

用法:
    python generate_monthly_report.py                    # 生成報告
    python generate_monthly_report.py --send-email       # 生成 + 寄送
    python generate_monthly_report.py --dry-run          # 模擬模式 (不呼叫 API)
"""

import sys
import os
import re
import argparse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path
import time

import pandas as pd
import yfinance as yf
from FinMind.data import DataLoader

# ─── 路徑設定 ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
REPORT_DIR = PROJECT_ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)

_RAW_INVENTORY_PATH = "/home/skytiger/.openclaw/workspace/agents/vault/vault_master.md"
if sys.platform == "win32":
    # 若在 Windows 原生執行，利用 UNC 路徑對應到 WSL 內部
    _RAW_INVENTORY_PATH = r"\\wsl$\Ubuntu" + _RAW_INVENTORY_PATH.replace("/", "\\")
INVENTORY_PATH = Path(_RAW_INVENTORY_PATH)

# ─── .env 載入 ────────────────────────────────────────────
_ENV_CACHE: dict = {}

def _load_env():
    if _ENV_CACHE:
        return _ENV_CACHE
    env_path = PROJECT_ROOT.parent / ".env"
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            _ENV_CACHE[k.strip()] = v
    return _ENV_CACHE

def _env(key: str, default: str = "") -> str:
    return os.getenv(key) or _load_env().get(key, default)

def _load_api_key():
    return _env("OPENROUTER_API_KEY") or _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")

BENCHMARK = "^TWII"

# ─── 分類推斷 ─────────────────────────────────────────────
_CATEGORY_MAP = {
    "0050": "大盤 ETF", "0052": "科技 ETF", "0056": "高息 ETF",
    "00919": "高息 ETF", "00878": "高息 ETF", "00929": "高息 ETF",
    "00981A": "主動型 ETF",
    "2330": "半導體", "2454": "半導體",
    "2881": "金融股", "2882": "金融股", "2884": "金融股",
    "2885": "金融股", "2886": "金融股", "2887": "金融股",
    "2889": "金融股", "2890": "金融股", "2891": "金融股",
    "2892": "金融股", "5880": "金融股",
    "3481": "面板股", "2603": "航運股",
}

def _infer_category(code: str) -> str:
    return _CATEGORY_MAP.get(code, "個股")


# ═══════════════════════════════════════════════════════════
# 庫存讀取
# ═══════════════════════════════════════════════════════════

def load_inventory_for_report() -> list[dict]:
    """
    從 vault_master.md 讀取完整持股清單。
    回傳 list of dict: name, code, symbol, shares, avg_price, note, category
    """
    if not INVENTORY_PATH.exists():
        print(f"  ⚠️ 找不到庫存檔: {INVENTORY_PATH}")
        return []

    content = INVENTORY_PATH.read_text(encoding="utf-8")
    rows = re.findall(
        r"\|\s*(\d{4,6}[A-Z]?)\s*\|\s*([^\|]+?)\s*\|\s*([\d,]+)\s*\|\s*([\d,.]+)\s*\|",
        content,
    )

    inventory = []
    for code, name, shares, price in rows:
        inventory.append({
            "name": name.strip(),
            "code": code.strip(),
            "symbol": f"{code.strip()}.TW",
            "shares": int(shares.strip().replace(",", "")),
            "avg_price": float(price.strip().replace(",", "")),
            "note": "",
            "category": _infer_category(code.strip()),
        })
    return inventory


# ═══════════════════════════════════════════════════════════
# 數據層
# ═══════════════════════════════════════════════════════════

def get_month_range():
    now = datetime.now()
    first_day = now.replace(day=1)
    return first_day.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), now.strftime("%Y年%m月")

def fetch_price_data(symbol: str, period: str = "3mo") -> pd.DataFrame:
    try:
        df = yf.download(symbol, period=period, progress=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df["MA5"] = df["Close"].rolling(5).mean()
        df["MA10"] = df["Close"].rolling(10).mean()
        df["MA20"] = df["Close"].rolling(20).mean()
        return df
    except Exception as e:
        print(f"  ⚠️ 抓取 {symbol} 失敗: {e}")
        return pd.DataFrame()

def calc_monthly_return(df: pd.DataFrame, month_start: str) -> float:
    if df.empty:
        return 0.0
    month_df = df[df.index >= month_start]
    if len(month_df) < 2:
        return 0.0
    return ((float(month_df["Close"].iloc[-1]) - float(month_df["Close"].iloc[0]))
            / float(month_df["Close"].iloc[0])) * 100

def get_ma_position(df: pd.DataFrame) -> tuple[str, str]:
    """回傳 (emoji_label, css_class)。"""
    if df.empty or len(df) < 2:
        return "無資料", "neutral"
    last = df.iloc[-1]
    close = float(last["Close"])
    ma5 = float(last["MA5"]) if pd.notna(last.get("MA5")) else None
    ma10 = float(last["MA10"]) if pd.notna(last.get("MA10")) else None
    ma20 = float(last["MA20"]) if pd.notna(last.get("MA20")) else None
    if ma5 is None or ma10 is None:
        return "🟡 資料不足", "neutral"
    if close > ma5 > ma10:
        return "🔥 多頭排列", "strong"
    elif close > ma5:
        return "✅ 站上 5MA", "ok"
    elif ma20 and close < ma20:
        return "💀 跌破月線", "danger"
    else:
        return "🟡 均線糾結", "neutral"

def get_right_side_grade(df: pd.DataFrame) -> tuple[str, str, str]:
    """回傳 (等級, 說明, css_class)。"""
    if df.empty or len(df) < 3:
        return "⚪ 無法判斷", "資料不足", "neutral"
    last, prev = df.iloc[-1], df.iloc[-2]
    close = float(last["Close"])
    ma5 = float(last["MA5"]) if pd.notna(last.get("MA5")) else None
    ma10 = float(last["MA10"]) if pd.notna(last.get("MA10")) else None
    if ma5 is None or ma10 is None:
        return "⚪ 無法判斷", "均線資料不足", "neutral"
    prev_ma5 = float(prev["MA5"]) if pd.notna(prev.get("MA5")) else ma5
    above_both = close > ma5 and close > ma10
    if above_both and ma5 > prev_ma5:
        return "🟢 強勢", "5MA/10MA 之上且 5MA 向上 — 右側確認", "strong"
    elif above_both:
        return "🟡 偏多", "均線上方，5MA 走平 — 觀察動能", "ok"
    elif close > ma5:
        return "🟠 整理", "僅站上 5MA — 尚未確認", "warn"
    else:
        return "🔴 弱勢", "均線下方 — 等待止穩", "danger"

def fetch_chip_summary(stock_id: str, days: int = 20) -> dict:
    """抓取三大法人籌碼摘要。具備高強度重試與異常隔離機制。"""
    dl = DataLoader()
    pure_id = stock_id.replace(".TW", "")
    # 抓取範圍稍微放寬以確保能取到足夠的天數
    start = (datetime.now() - timedelta(days=days + 20)).strftime("%Y-%m-%d")
    
    # 預設回傳值 (防止 API 徹底掛掉導致報表崩潰)
    empty_res = {"foreign_net": 0, "trust_net": 0, "total_net": 0, "days": 0}
    
    try:
        raw = None
        # FinMind 無 Token 時極易觸發 Rate Limit，採用漸進式重試
        for i in range(1, 6):
            try:
                # 這裡直接捕獲底層可能的異常
                raw = dl.taiwan_stock_institutional_investors(stock_id=pure_id, start_date=start)
                if raw is not None and not raw.empty:
                    break
            except Exception as inner_e:
                print(f"  (重試 {i}/5) {pure_id} API 暫時異常: {inner_e}")
            
            # 漸進式等待: 2s, 4s, 6s, 8s, 10s
            time.sleep(i * 2) 
        
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            print(f"  ⚠️ {pure_id} 籌碼數據抓取失敗 (已重試 5 次)，返回空值")
            return empty_res
        
        # 數據清理與格式轉換
        try:
            # 確保數值正確解析
            for col in ["buy", "sell"]:
                raw[col] = pd.to_numeric(raw[col], errors='coerce').fillna(0)
            
            # 單位轉換：股 -> 張
            raw["net"] = (raw["buy"] - raw["sell"]) / 1000
            
            # 依照日期與法人名稱彙整
            pivot = raw.groupby(["date", "name"])["net"].sum().unstack(fill_value=0)
            
            if pivot.empty:
                return empty_res

            # 取得最近的天數
            pivot = pivot.sort_index().tail(days)
            
            cols = pivot.columns.tolist()
            # 彈性匹配法人名稱
            f_cols = [c for c in cols if "Foreign" in c]
            t_cols = [c for c in cols if "Investment_Trust" in c or "Trust" in c]
            d_cols = [c for c in cols if "Dealer" in c and c not in f_cols]
            
            foreign_net = pivot[f_cols].sum().sum() if f_cols else 0
            trust_net = pivot[t_cols].sum().sum() if t_cols else 0
            dealer_net = pivot[d_cols].sum().sum() if d_cols else 0
            
            return {
                "foreign_net": int(foreign_net),
                "trust_net": int(trust_net),
                "total_net": int(foreign_net + trust_net + dealer_net),
                "days": len(pivot),
            }
        except Exception as parse_e:
            print(f"  ⚠️ {pure_id} 籌碼數據解析異常: {parse_e}")
            return empty_res

    except Exception as e:
        print(f"  ⚠️ {pure_id} 籌碼模組嚴重故障: {e}")
        return empty_res

def fetch_dividend_yield(symbol: str) -> str:
    try:
        info = yf.Ticker(symbol).info
        dy = info.get("dividendYield") or info.get("trailingAnnualDividendYield")
        if dy is not None and dy > 0:
            pct = dy * 100 if dy < 1 else dy
            if pct > 30:
                return "N/A"
            return f"{pct:.2f}%"
        return "N/A"
    except Exception:
        return "N/A"


# ═══════════════════════════════════════════════════════════
# AI 點評
# ═══════════════════════════════════════════════════════════

def generate_ai_outlook(api_key: str, report_context: str) -> str:
    # 嘗試獲取 API Key
    import os
    import json
    
    # 優先從傳入參數獲取，若無則嘗試從系統或 env 獲取
    or_key = api_key or os.getenv("OPENROUTER_API_KEY")

    if not or_key:
        # 最後嘗試手動讀取一次 .env
        env_vars = _load_env()
        or_key = env_vars.get("OPENROUTER_API_KEY")

    if not or_key:
        return "<em>（未提供 OPENROUTER_API_KEY，AI 點評略過。請檢查 /mnt/d/code/.env）</em>"
    
    # 使用 OpenRouter 調用 Gemini 模型
    try:
        import requests
        headers = {
            "Authorization": f"Bearer {or_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "google/gemini-2.0-flash-001",
            "messages": [
                {"role": "system", "content": "你是 Tiger AI — 一位住在宜蘭、53 歲的理性冒險家暨資深投資人。務實、精準、像老朋友聊天，帶點登山/溯溪的譬喻。"},
                {"role": "user", "content": f"請針對以下持股績效與籌碼撰寫下月展望：\n{report_context}"}
            ]
        }
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, data=json.dumps(payload), timeout=30)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            return f"<em>OpenRouter 請求失敗 ({response.status_code}): {response.text}</em>"
    except Exception as e:
        return f"<em>AI 點評生成異常: {e}</em>"


# ═══════════════════════════════════════════════════════════
# 天氣
# ═══════════════════════════════════════════════════════════

def _get_yilan_weather() -> tuple[str, str]:
    """回傳 (weather_now, suggestion)。優先使用本地 CWA 腳本。"""
    try:
        import subprocess
        # 呼叫主人的 CWA 氣象腳本
        script_path = "/home/skytiger/.openclaw/workspace/scripts/get_cwa_weather.py"
        python_path = "/mnt/d/code/venv_linux/bin/python3"
        
        if os.path.exists(script_path):
            result = subprocess.run(
                [python_path, script_path, "宜蘭縣"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                # 簡化輸出，只取第一段預報
                lines = [l for l in result.stdout.splitlines() if l.strip()]
                if len(lines) >= 3:
                    # 格式: 📅 2026-02-27 12:00 \n 🌧 陰短暫雨 | 🌡 17-19°C \n ☔ 降雨機率: 70%
                    weather_info = f"{lines[2]} ({lines[3].replace('☔ ', '')})"
                    return weather_info, ""
    except Exception as e:
        print(f"  ⚠️ CWA 腳本調用失敗: {e}")

    # Fallback to wttr.in
    try:
        import requests
        resp = requests.get(
            "https://wttr.in/Yilan,Taiwan?format=%C+%t+%w+%p&lang=zh-tw", timeout=8
        )
        if resp.ok and resp.text.strip():
            return resp.text.strip(), ""
    except Exception:
        pass
    return "（暫時無法取得）", "出門前請查看 CWA 氣象預報"


# ═══════════════════════════════════════════════════════════
# 報告組裝
# ═══════════════════════════════════════════════════════════

def build_report(dry_run: bool = False) -> tuple[str, str, str]:
    """
    建構完整月報。
    回傳 (html_content, md_content, file_path)。
    """
    month_start, today, month_label = get_month_range()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"📋 ETF 全能月報 — {month_label}")
    print(f"   期間: {month_start} ~ {today}")

    # ── 0. 讀取庫存 ──────────────────────────────────
    print("📂 讀取持股清單...")
    inventory = load_inventory_for_report()
    if not inventory:
        print("  ⚠️ 庫存為空，使用預設清單")
        inventory = [
            {"name": "群益台灣精選高息", "code": "00919", "symbol": "00919.TW",
             "shares": 75000, "avg_price": 21.25, "note": "", "category": "高息 ETF"},
        ]
    print(f"   共 {len(inventory)} 檔持股")

    # ── 1. 大盤基準 ──────────────────────────────────
    print("📊 抓取大盤基準...")
    bench_df = fetch_price_data(BENCHMARK, period="3mo")
    bench_return = calc_monthly_return(bench_df, month_start)
    bench_close = float(bench_df["Close"].iloc[-1]) if not bench_df.empty else 0

    # ── 2. 各標的數據 ────────────────────────────────
    rows = []
    context_parts = []
    total_cost = 0.0
    total_market = 0.0

    for item in inventory:
        sym, name, code = item["symbol"], item["name"], item["code"]
        print(f"  📈 {name} ({sym})...")

        price_df = fetch_price_data(sym, period="3mo")
        monthly_ret = calc_monthly_return(price_df, month_start)
        ma_label, ma_css = get_ma_position(price_df)
        grade, grade_desc, grade_css = get_right_side_grade(price_df)
        last_close = float(price_df["Close"].iloc[-1]) if not price_df.empty else 0

        if not dry_run:
            div_yield = fetch_dividend_yield(sym)
            chip = fetch_chip_summary(sym)
        else:
            div_yield = "5.20%"
            chip = {"foreign_net": 1200, "trust_net": 800, "total_net": 2500, "days": 20}

        alpha = monthly_ret - bench_return

        # 損益
        cost_basis = item["avg_price"] * item["shares"]
        market_val = last_close * item["shares"]
        pnl = market_val - cost_basis
        pnl_pct = ((last_close - item["avg_price"]) / item["avg_price"]) * 100 if item["avg_price"] > 0 else 0
        total_cost += cost_basis
        total_market += market_val

        rows.append({
            "name": name, "code": code, "symbol": sym,
            "category": item["category"],
            "shares": item["shares"], "avg_price": item["avg_price"],
            "close": last_close, "monthly_ret": monthly_ret, "alpha": alpha,
            "ma_label": ma_label, "ma_css": ma_css,
            "grade": grade, "grade_desc": grade_desc, "grade_css": grade_css,
            "div_yield": div_yield, "chip": chip,
            "pnl": pnl, "pnl_pct": pnl_pct,
            "market_val": market_val, "note": item["note"],
        })

        context_parts.append(
            f"- {name} ({code}): 月報酬 {monthly_ret:+.2f}%, 收盤 {last_close:.2f}, "
            f"殖利率 {div_yield}, 均線 {ma_label}, "
            f"法人{chip['days']}日淨買超 {chip['total_net']:+,} 張, "
            f"持有 {item['shares']:,} 股, 成本 {item['avg_price']:.2f}"
        )

    total_pnl = total_market - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    report_context = "\n".join(context_parts)

    # ── 3. AI 點評 ───────────────────────────────────
    print("🤖 生成 AI 點評...")
    api_key = _load_api_key()
    if dry_run:
        ai_outlook = ("本月大盤走勢像溯溪時遇到的緩流段。各標的均處多頭排列，"
                       "建議維持現有配置，留意聯準會動態。記住：山不會跑，但天氣會變。🏔️")
    else:
        ai_outlook = generate_ai_outlook(api_key, report_context)

    # ── 4. 天氣 ──────────────────────────────────────
    weather_now, weather_fallback = _get_yilan_weather()

    # ── 5. 組裝 HTML ─────────────────────────────────
    print("📝 組裝 HTML 報告...")
    html = _render_html(
        month_label=month_label, now_str=now_str,
        bench_close=bench_close, bench_return=bench_return,
        rows=rows,
        total_cost=total_cost, total_market=total_market,
        total_pnl=total_pnl, total_pnl_pct=total_pnl_pct,
        ai_outlook=ai_outlook,
        weather_now=weather_now, weather_fallback=weather_fallback,
    )

    # ── 6. 也存一份 Markdown ─────────────────────────
    md = _render_markdown(
        month_label, now_str, bench_close, bench_return, rows,
        total_cost, total_market, total_pnl, total_pnl_pct,
        ai_outlook, weather_now,
    )

    # ── 7. 儲存 ──────────────────────────────────────
    ts = datetime.now().strftime("%Y%m")
    html_path = REPORT_DIR / f"etf_monthly_{ts}.html"
    md_path = REPORT_DIR / f"etf_monthly_{ts}.md"
    html_path.write_text(html, encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    print(f"✅ HTML 報告: {html_path}")
    print(f"✅ MD   報告: {md_path}")

    return html, md, str(html_path)


# ═══════════════════════════════════════════════════════════
# HTML 渲染 — 深色質感・全 inline CSS・零 class
# ═══════════════════════════════════════════════════════════

def _render_html(*, month_label, now_str, bench_close, bench_return,
                 rows, total_cost, total_market, total_pnl, total_pnl_pct,
                 ai_outlook, weather_now, weather_fallback) -> str:

    # ── 色彩系統 ──
    C_UP, C_DOWN, C_FLAT = "#e74c3c", "#27ae60", "#7f8c8d"
    C_BG, C_CARD, C_BORDER = "#0f1117", "#1a1d28", "#2a2e3a"
    C_TEXT, C_SUB, C_ACCENT, C_GOLD = "#e8e8e8", "#8a8fa3", "#4fc3f7", "#ffd54f"
    C_TH_BG, C_ROW_ALT = "#22263a", "#14171f"

    def _c(v):
        return C_UP if v > 0 else C_DOWN if v < 0 else C_FLAT

    def _gbg(css):
        return {"strong":"#27ae60","ok":"#f39c12","warn":"#e67e22","danger":"#e74c3c","neutral":"#636e72"}.get(css,"#636e72")

    def _cv(total):
        if total > 1000:  return "🔥 大量吸籌", C_UP
        if total > 0:     return "✅ 小幅買超", C_DOWN
        if total > -1000: return "🟡 微幅賣超", "#f39c12"
        return "🔴 大量出貨", "#8e44ad"

    S_TH = f"padding:10px 8px;text-align:left;font-size:11px;font-weight:700;color:{C_SUB};background-color:{C_TH_BG};border-bottom:2px solid {C_BORDER};text-transform:uppercase"
    S_TD = f"padding:10px 8px;border-bottom:1px solid {C_BORDER};font-size:13px;color:{C_TEXT};vertical-align:top"
    S_BDG = "display:inline-block;padding:3px 10px;border-radius:12px;color:#fff;font-size:11px;font-weight:700"
    S_SUB = f"font-size:10px;color:{C_SUB}"

    def _kpi(icon, label, value, delta, dc=C_SUB):
        return f"""<td width="25%" style="text-align:center;padding:20px 8px;background-color:{C_CARD};border-radius:12px;border:1px solid {C_BORDER}">
        <p style="font-size:22px;margin:0">{icon}</p>
        <p style="font-size:10px;color:{C_SUB};text-transform:uppercase;margin:4px 0 0 0">{label}</p>
        <p style="font-size:22px;font-weight:800;color:{C_TEXT};margin:4px 0">{value}</p>
        <p style="font-size:12px;color:{dc};margin:0">{delta}</p></td>"""

    kpi = f'<table width="100%" cellpadding="0" cellspacing="8" style="margin-bottom:16px"><tr>{_kpi("📈","大盤指數",f"{bench_close:,.0f}",f"本月 {bench_return:+.2f}%",_c(bench_return))}{_kpi("📋","持股檔數",str(len(rows)),month_label)}{_kpi("💰","總市值",f"${total_market:,.0f}",f"成本 ${total_cost:,.0f}")}{_kpi("📊","總損益",f"${total_pnl:+,.0f}",f"{total_pnl_pct:+.1f}%",_c(total_pnl))}</tr></table>'

    def _co(accent=C_ACCENT):
        return f'<div style="background-color:{C_CARD};border-radius:12px;padding:24px;margin-bottom:16px;border:1px solid {C_BORDER};border-top:3px solid {accent}">'

    def _h2(e,t,a=C_ACCENT):
        return f'<h2 style="font-size:18px;margin:0 0 16px 0;padding-bottom:10px;border-bottom:1px solid {C_BORDER};color:{C_TEXT}">{e} {t}</h2>'

    # 績效
    pr = ""
    for i,r in enumerate(rows):
        bg = C_CARD if i%2==0 else C_ROW_ALT
        rc,ac,pc = _c(r["monthly_ret"]),_c(r["alpha"]),_c(r["pnl"])
        pr += f'<tr style="background-color:{bg}"><td style="{S_TD}"><strong style="color:{C_TEXT}">{r["name"]}</strong><br><span style="{S_SUB}">{r["code"]} · {r["category"]}</span></td><td style="{S_TD};text-align:right">{r["close"]:.2f}</td><td style="{S_TD};text-align:right;color:{rc};font-weight:700">{r["monthly_ret"]:+.2f}%</td><td style="{S_TD};text-align:right;color:{ac}">{r["alpha"]:+.2f}%</td><td style="{S_TD};text-align:center">{r["div_yield"]}</td><td style="{S_TD};text-align:center"><span style="{S_BDG};background-color:{_gbg(r["ma_css"])}">{r["ma_label"]}</span></td><td style="{S_TD};text-align:right">{r["shares"]:,}</td><td style="{S_TD};text-align:right;color:{pc};font-weight:700">{r["pnl"]:+,.0f}<br><span style="{S_SUB}">{r["pnl_pct"]:+.1f}%</span></td></tr>'

    # 紅綠燈
    sr = ""
    for i,r in enumerate(rows):
        bg = C_CARD if i%2==0 else C_ROW_ALT
        sr += f'<tr style="background-color:{bg}"><td style="{S_TD}"><strong style="color:{C_TEXT}">{r["name"]}</strong> <span style="{S_SUB}">({r["code"]})</span></td><td style="{S_TD};text-align:center"><span style="{S_BDG};background-color:{_gbg(r["grade_css"])}">{r["grade"]}</span></td><td style="{S_TD};color:{C_SUB}">{r["grade_desc"]}</td></tr>'

    # 籌碼
    cr = ""
    for i,r in enumerate(rows):
        bg = C_CARD if i%2==0 else C_ROW_ALT
        ch = r["chip"]; v,vc = _cv(ch["total_net"])
        cr += f'<tr style="background-color:{bg}"><td style="{S_TD}"><strong style="color:{C_TEXT}">{r["name"]}</strong> <span style="{S_SUB}">({r["code"]})</span></td><td style="{S_TD};text-align:right;color:{_c(ch["foreign_net"])}">{ch["foreign_net"]:+,}</td><td style="{S_TD};text-align:right;color:{_c(ch["trust_net"])}">{ch["trust_net"]:+,}</td><td style="{S_TD};text-align:right;font-weight:700;color:{C_TEXT}">{ch["total_net"]:+,}</td><td style="{S_TD};color:{vc};font-weight:600">{v}</td></tr>'

    # AI
    ap = "".join(f'<p style="margin:0 0 12px 0;font-size:14px;line-height:1.8;color:{C_TEXT}">{l}</p>' for l in ai_outlook.strip().split("\n") if l.strip())

    # 復健
    rr = ""
    for j,(w,wh,ho) in enumerate([("本週","🏊 低衝擊有氧","游泳/飛輪 30 分鐘，避免下坡"),("本週","🧘 股四頭肌強化","靠牆半蹲 3×30 秒"),("本週","🧊 冰敷+伸展","冰敷 15 分鐘 + IT Band 滾筒"),("下週","🏃 慢跑測試","無痛可嘗試平地 15 分鐘")]):
        bg = C_CARD if j%2==0 else C_ROW_ALT
        rr += f'<tr style="background:{bg}"><td style="{S_TD}">{w}</td><td style="{S_TD}">{wh}</td><td style="{S_TD}">{ho}</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background-color:{C_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Noto Sans TC','Helvetica Neue',Arial,sans-serif;color:{C_TEXT};line-height:1.6">
<div style="max-width:700px;margin:0 auto;padding:16px">

  <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:16px;margin-bottom:20px;background:linear-gradient(135deg,#0d1b2a,#1b2838,#2d1b69)">
    <tr><td style="padding:36px 24px;text-align:center">
      <p style="font-size:40px;margin:0">🐅</p>
      <h1 style="color:#ffffff;font-size:24px;margin:8px 0 4px 0;letter-spacing:1px">Tiger AI 財富與冒險月報</h1>
      <p style="color:{C_GOLD};font-size:16px;font-weight:700;margin:0">— {month_label} —</p>
      <p style="color:rgba(255,255,255,.5);font-size:12px;margin:8px 0 0 0">generated {now_str}</p>
    </td></tr>
  </table>

  {kpi}

  {_co(C_ACCENT)}
    {_h2("📊","資產績效總覽")}
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><th style="{S_TH}">標的</th><th style="{S_TH};text-align:right">收盤</th><th style="{S_TH};text-align:right">月報酬</th><th style="{S_TH};text-align:right">Alpha</th><th style="{S_TH};text-align:center">殖利率</th><th style="{S_TH};text-align:center">均線</th><th style="{S_TH};text-align:right">持股</th><th style="{S_TH};text-align:right">損益</th></tr>
      {pr}
    </table>
    <p style="margin:12px 0 0 0;font-size:11px;color:{C_SUB}">📌 大盤本月 <strong style="color:{_c(bench_return)}">{bench_return:+.2f}%</strong> ｜ Alpha = 個股 − 大盤</p>
  </div>

  {_co("#f39c12")}
    {_h2("🚦","庫存紅綠燈 — 右側交易強度","#f39c12")}
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><th style="{S_TH}">標的</th><th style="{S_TH};text-align:center">強度</th><th style="{S_TH}">說明</th></tr>
      {sr}
    </table>
  </div>

  {_co("#9b59b6")}
    {_h2("🕵️","法人籌碼概況（近 20 日）","#9b59b6")}
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><th style="{S_TH}">標的</th><th style="{S_TH};text-align:right">外資(張)</th><th style="{S_TH};text-align:right">投信(張)</th><th style="{S_TH};text-align:right">合計(張)</th><th style="{S_TH}">判讀</th></tr>
      {cr}
    </table>
  </div>

  {_co("#e17055")}
    {_h2("🤖","Tiger AI 下月展望","#e17055")}
    {ap}
  </div>

  {_co("#00b894")}
    {_h2("🌿","Tiger 生活指南","#00b894")}
    <h3 style="font-size:15px;margin:0 0 8px 0;color:{C_TEXT}">🌦️ 宜蘭天氣</h3>
    <p style="font-size:20px;font-weight:700;margin:0 0 12px 0;color:{C_GOLD}">{weather_now}</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px">
      <tr><td style="padding:5px 0;font-size:13px;color:{C_TEXT}">☀️ 天氣好 → 林美石磐步道 / 蘇澳冷泉</td></tr>
      <tr><td style="padding:5px 0;font-size:13px;color:{C_TEXT}">🌧️ 下雨天 → 羅東夜市 / 宜蘭美術館</td></tr>
      <tr><td style="padding:5px 0;font-size:13px;color:{C_TEXT}">🌤️ 多雲 → 冬山河自行車道（膝蓋友善）</td></tr>
    </table>
    <h3 style="font-size:15px;margin:0 0 8px 0;color:{C_TEXT}">🦵 跑者膝復健進度</h3>
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><th style="{S_TH}">時程</th><th style="{S_TH}">項目</th><th style="{S_TH}">說明</th></tr>
      {rr}
    </table>
    <p style="margin:10px 0 0 0;font-size:12px;color:{C_SUB}">💡 疼痛 &gt; 3/10 就停。恢復是螺旋上升，耐心比速度重要。</p>
  </div>

  <p style="text-align:center;color:{C_SUB};font-size:11px;padding:20px 0">
    🐅 Tiger AI 股市戰情室 v2.0 ｜ {now_str} ｜ 僅供參考，投資請自行評估風險
  </p>
</div>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════
# Markdown 渲染（備份 + 附件用）
# ═══════════════════════════════════════════════════════════

def _render_markdown(month_label, now_str, bench_close, bench_return, rows,
                     total_cost, total_market, total_pnl, total_pnl_pct,
                     ai_outlook, weather_now) -> str:
    md = [f"# 🐅 Tiger AI 財富與冒險月報 — {month_label}",
          f"> 生成時間: {now_str} | 大盤: {bench_close:,.0f} ({bench_return:+.2f}%)",
          f"> 總市值: ${total_market:,.0f} | 總損益: ${total_pnl:+,.0f} ({total_pnl_pct:+.1f}%)", ""]
    md.append("## 📊 資產績效")
    md.append("| 標的 | 收盤 | 月報酬 | Alpha | 殖利率 | 均線 | 持股 | 損益 |")
    md.append("|------|------|--------|-------|--------|------|------|------|")
    for r in rows:
        md.append(f"| {r['name']} ({r['code']}) | {r['close']:.2f} | {r['monthly_ret']:+.2f}% "
                  f"| {r['alpha']:+.2f}% | {r['div_yield']} | {r['ma_label']} "
                  f"| {r['shares']:,} | {r['pnl']:+,.0f} ({r['pnl_pct']:+.1f}%) |")
    md += ["", "## 🚦 紅綠燈", "| 標的 | 強度 | 說明 |", "|------|------|------|"]
    for r in rows:
        md.append(f"| {r['name']} | {r['grade']} | {r['grade_desc']} |")
    md += ["", "## 🕵️ 籌碼", "| 標的 | 外資 | 投信 | 合計 |", "|------|------|------|------|"]
    for r in rows:
        c = r["chip"]
        md.append(f"| {r['name']} | {c['foreign_net']:+,} | {c['trust_net']:+,} | {c['total_net']:+,} |")
    md += ["", "## 🤖 AI 展望", "", ai_outlook, "",
           "## 🌿 生活指南", f"宜蘭天氣: {weather_now}", "",
           "---", f"*Tiger AI v2.0 | {now_str}*"]
    return "\n".join(md)


# ═══════════════════════════════════════════════════════════
# Email (HTML + MD 附件)
# ═══════════════════════════════════════════════════════════

def send_email(subject: str, html_body: str, text_body: str, md_path: str = ""):
    """寄送 HTML 郵件，同時附帶 Markdown 原檔作為附件。"""
    from email.mime.base import MIMEBase
    from email import encoders

    sender = _env("GMAIL_USER", "skytiger123@gmail.com")
    app_password = _env("GMAIL_APP_PASSWORD")
    # 寄送
    recipients_raw = _env("GMAIL_RECIPIENTS", sender)
    recipients = [r.strip() for r in recipients_raw.replace(",", ";").split(";") if r.strip()]
    if not app_password:
        print("⚠️ 未設定 GMAIL_APP_PASSWORD，跳過寄送。")
        return False
    if not recipients:
        print("⚠️ 無收件人，跳過寄送。")
        return False

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    # HTML + 純文字備援 (alternative part)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    # MD 附件
    if md_path and Path(md_path).exists():
        md_file = Path(md_path)
        part = MIMEBase("text", "markdown")
        part.set_payload(md_file.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={md_file.name}")
        msg.attach(part)
        print(f"📎 附件: {md_file.name}")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender, app_password)
            server.sendmail(sender, recipients, msg.as_string())
        print(f"📧 Email 寄送成功！收件人: {', '.join(recipients)}")
        return True
    except Exception as e:
        print(f"❌ Email 寄送失敗: {e}")
        return False


# ═══════════════════════════════════════════════════════════
# 主程式
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Tiger AI 財富與冒險月報生成器")
    parser.add_argument("--send-email", action="store_true", help="生成後寄送 Email")
    parser.add_argument("--dry-run", action="store_true", help="模擬模式")
    args = parser.parse_args()

    print("=" * 60)
    print("  🐅 Tiger AI 財富與冒險月報 自動化系統")
    print("=" * 60)
    print()

    html, md, filepath = build_report(dry_run=args.dry_run)

    if args.send_email:
        _, _, month_label = get_month_range()
        subject = f"🐅 Tiger AI 財富與冒險月報 — {month_label}"
        md_path = str(REPORT_DIR / f"etf_monthly_{datetime.now().strftime('%Y%m')}.md")
        send_email(subject, html, md, md_path)

    print()
    print("🎉 完成！")


if __name__ == "__main__":
    main()
