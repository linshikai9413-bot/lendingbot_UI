# app.py - V19 最終極簡版 (自動連線 + 智能過濾 + 純淨介面)
import streamlit as st
import ccxt
from datetime import datetime, timedelta
import traceback

# ================== 頁面設定 (極簡風格) ==================
st.set_page_config(page_title="Bitfinex 資產監控", page_icon="💰", layout="centered")

THEME_BG = "#0E1117"
TEXT_MAIN = "#E6E6E6"

st.markdown(f"""
    <style>
    .stApp {{ background-color: {THEME_BG}; color: {TEXT_MAIN}; }}
    /* 隱藏預設選單，讓畫面更乾淨 */
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    /* 加大數據字體 */
    div[data-testid="stMetricValue"] {{ font-size: 2.2rem !important; font-weight: 600; }}
    div[data-testid="stMetricLabel"] {{ font-size: 1rem !important; color: #A1A9B3; }}
    </style>
""", unsafe_allow_html=True)

# ================== 核心邏輯 ==================

def safe_dt(ts):
    try:
        if ts is None: return datetime.now()
        ts = int(ts)
        if ts > 1e12: return datetime.fromtimestamp(ts / 1000)
        return datetime.fromtimestamp(ts)
    except: return datetime.now()

@st.cache_resource
def init_exchange(api_key, api_secret):
    ex = ccxt.bitfinex({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

def load_secrets():
    """自動從 [bitfinex] 區塊載入 API"""
    if st.session_state.get("api_key"): return

    # 針對你的結構：直接讀取 [bitfinex]
    bf_block = st.secrets.get("bitfinex")
    if bf_block and isinstance(bf_block, dict):
        # 嘗試各種可能的 key 名稱
        k = bf_block.get("api_key") or bf_block.get("key") or bf_block.get("apiKey")
        s = bf_block.get("api_secret") or bf_block.get("secret") or bf_block.get("apiSecret")
        
        if k and s:
            st.session_state.api_key = k
            st.session_state.api_secret = s
            st.session_state.secrets_loaded = True

# 執行載入
load_secrets()

# ================== 主程式 ==================

# 標題區 (狀態燈號)
status_col, title_col = st.columns([1, 8])
with status_col:
    if st.session_state.get("secrets_loaded"):
        st.success("連線中")
    else:
        st.warning("請設定")
with title_col:
    st.markdown("### Bitfinex 資產監控")

# 檢查 API
if not st.session_state.get("api_key"):
    st.info("⚠️ 請在 `.streamlit/secrets.toml` 中設定 `[bitfinex]` 區塊，或使用側邊欄輸入。")
    # 這裡保留側邊欄作為備用，以防 Secrets 格式有誤
    with st.sidebar:
        k = st.text_input("API Key", type="password")
        s = st.text_input("API Secret", type="password")
        if k and s:
            st.session_state.api_key = k
            st.session_state.api_secret = s
            st.rerun()
    st.stop()

# 獲取數據
with st.spinner("正在分析帳本..."):
    try:
        ex = init_exchange(st.session_state.api_key, st.session_state.api_secret)
        balances = ex.fetch_balance()
        # 抓取過去 90 天帳本
        since = ex.milliseconds() - 90 * 24 * 60 * 60 * 1000
        ledgers = ex.fetch_ledger("USD", since=since, limit=1000)
    except Exception as e:
        st.error(f"連線失敗: {str(e)}")
        st.stop()

# --- 1. 計算總資產 (強制讀取 Funding Wallet) ---
total_assets = 0.0
free_assets = 0.0
if "info" in balances and isinstance(balances["info"], list):
    for wallet in balances["info"]:
        # 尋找 ["funding", "USD", ...]
        if len(wallet) > 4 and wallet[0] == "funding" and wallet[1] == "USD":
            total_assets = float(wallet[2]) if wallet[2] else 0.0
            free_assets = float(wallet[4]) if wallet[4] else 0.0
            break
# Fallback
if total_assets == 0:
    usd = balances.get("USD", {})
    total_assets = float(usd.get("total", 0))

# --- 2. 計算收益 (智能過濾本金) ---
total_earn = 0.0
last_30d_earn = 0.0
first_date = datetime.now()
has_data = False
cutoff_30d = datetime.now() - timedelta(days=30)

# 設定門檻：單筆入帳超過總資產的 0.5% 即視為本金轉入 (例如 745 * 0.005 = 3.7 USD)
threshold = (total_assets * 0.005) if total_assets > 0 else 10.0

if ledgers:
    for r in ledgers:
        try:
            amt = float(r.get("amount", 0))
            if amt <= 0: continue # 排除支出
            if amt > threshold: continue # 排除本金 (關鍵修復)

            # 來到這裡的都是 < 0.5% 的小額入帳 (視為利息)
            ts = r.get("timestamp") or r.get("mts")
            dt = safe_dt(ts)
            
            total_earn += amt
            if dt >= cutoff_30d:
                last_30d_earn += amt
            
            if not has_data or dt < first_date:
                first_date = dt
                has_data = True
        except: continue

days_run = (datetime.now() - first_date).days + 1 if has_data else 1

# --- 3. 計算指標 ---
utilization = ((total_assets - free_assets) / total_assets * 100) if total_assets > 0 else 0.0
apy = 0.0
if total_assets > 0 and days_run > 0:
    apy = (total_earn / days_run / total_assets * 365 * 100)

# ================== 顯示結果 (純淨版) ==================

st.markdown("---")

# 第一排：資產核心
c1, c2 = st.columns(2)
c1.metric("總資產 (Funding)", f"${total_assets:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")

st.markdown("---")

# 第二排：收益表現
c3, c4, c5 = st.columns(3)
c3.metric("30天收益 (估)", f"${last_30d_earn:,.2f}")
c4.metric("歷史總收益 (估)", f"${total_earn:,.2f}")
c5.metric("全歷史 APY", f"{apy:.2f}%")

st.markdown("---")

# 重新整理按鈕 (放在最下方，不干擾視線)
if st.button("🔄 更新數據", type="secondary", use_container_width=True):
    st.cache_resource.clear()
    st.rerun()
