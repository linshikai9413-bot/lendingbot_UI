# app.py - V21 絕對連線版 (直接指定路徑 + 去除空白 + 智能過濾)
import streamlit as st
import ccxt
from datetime import datetime, timedelta
import traceback

# ================== 頁面設定 ==================
st.set_page_config(page_title="Bitfinex 資產監控", page_icon="💰", layout="centered")

THEME_BG = "#0E1117"
TEXT_MAIN = "#E6E6E6"

st.markdown(f"""
    <style>
    .stApp {{ background-color: {THEME_BG}; color: {TEXT_MAIN}; }}
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    div[data-testid="stMetricValue"] {{ font-size: 2.2rem !important; font-weight: 600; }}
    div[data-testid="stMetricLabel"] {{ font-size: 1rem !important; color: #A1A9B3; }}
    </style>
""", unsafe_allow_html=True)

# ================== 核心功能 ==================

def safe_dt(ts):
    try:
        if ts is None: return datetime.now()
        ts = int(ts)
        if ts > 1e12: return datetime.fromtimestamp(ts / 1000)
        return datetime.fromtimestamp(ts)
    except: return datetime.now()

@st.cache_resource
def init_exchange(api_key, api_secret):
    # 這裡加上 strip() 確保去除前後空白，避免複製貼上時的隱形錯誤
    ex = ccxt.bitfinex({
        "apiKey": api_key.strip(),
        "secret": api_secret.strip(),
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

def load_secrets_direct():
    """
    V21 改進：直接讀取診斷確認存在的路徑 st.secrets['bitfinex']['api_key']
    不再進行模糊搜尋，避免邏輯錯誤。
    """
    # 1. 如果 Session 已經有值，就不用再載入
    if st.session_state.get("api_key"): 
        return

    key = ""
    secret = ""

    # 2. 直接讀取 (根據你的診斷結果)
    try:
        if "bitfinex" in st.secrets:
            section = st.secrets["bitfinex"]
            key = section.get("api_key")
            secret = section.get("api_secret")
    except Exception:
        pass

    # 3. 存入 Session
    if key and secret:
        st.session_state.api_key = key
        st.session_state.api_secret = secret
        st.session_state.secrets_loaded = True

# 執行載入
load_secrets_direct()

# ================== 主程式 ==================

# 標題與狀態
status_col, title_col = st.columns([1.5, 8.5])
with status_col:
    if st.session_state.get("secrets_loaded"):
        st.success("已連線")
    else:
        st.warning("未連線")
with title_col:
    st.markdown("### Bitfinex 資產監控")

# 檢查 API
if not st.session_state.get("api_key"):
    st.error("⚠️ 讀取失敗。雖然診斷看到了 Keys，但程式無法讀取。")
    st.info("請檢查 secrets.toml 內容是否包含特殊字元。")
    
    # 顯示診斷 (再次確認)
    with st.expander("診斷資訊"):
        st.write("Root keys:", list(st.secrets.keys()))
        if "bitfinex" in st.secrets:
            st.write("Bitfinex keys:", list(st.secrets["bitfinex"].keys()))
            
    # 備用輸入框
    k = st.text_input("手動輸入 API Key", type="password")
    s = st.text_input("手動輸入 API Secret", type="password")
    if k and s:
        st.session_state.api_key = k
        st.session_state.api_secret = s
        st.rerun()
    st.stop()

# 獲取與計算數據
with st.spinner("正在分析帳本..."):
    try:
        ex = init_exchange(st.session_state.api_key, st.session_state.api_secret)
        balances = ex.fetch_balance()
        since = ex.milliseconds() - 90 * 24 * 60 * 60 * 1000
        ledgers = ex.fetch_ledger("USD", since=since, limit=1000)
    except Exception as e:
        st.error(f"連線失敗: {str(e)}")
        st.caption("請檢查 API Key 是否正確，或權限是否開啟 (Margin Funding: Read)。")
        st.stop()

# 1. 總資產 (Funding Wallet)
total_assets = 0.0
free_assets = 0.0
if "info" in balances and isinstance(balances["info"], list):
    for wallet in balances["info"]:
        if len(wallet) > 4 and wallet[0] == "funding" and wallet[1] == "USD":
            total_assets = float(wallet[2]) if wallet[2] else 0.0
            free_assets = float(wallet[4]) if wallet[4] else 0.0
            break
if total_assets == 0:
    usd = balances.get("USD", {})
    total_assets = float(usd.get("total", 0))

# 2. 收益計算 (智能門檻過濾本金)
total_earn = 0.0
last_30d_earn = 0.0
first_date = datetime.now()
has_data = False
cutoff_30d = datetime.now() - timedelta(days=30)
threshold = (total_assets * 0.005) if total_assets > 0 else 10.0 # 0.5% 門檻

if ledgers:
    for r in ledgers:
        try:
            amt = float(r.get("amount", 0))
            if amt <= 0: continue
            if amt > threshold: continue # 過濾本金

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

# 3. 指標
utilization = ((total_assets - free_assets) / total_assets * 100) if total_assets > 0 else 0.0
apy = 0.0
if total_assets > 0 and days_run > 0:
    apy = (total_earn / days_run / total_assets * 365 * 100)

# ================== 顯示結果 ==================

st.markdown("---")
c1, c2 = st.columns(2)
c1.metric("總資產 (Funding)", f"${total_assets:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")

st.markdown("---")
c3, c4, c5 = st.columns(3)
c3.metric("30天收益 (估)", f"${last_30d_earn:,.2f}")
c4.metric("歷史總收益 (估)", f"${total_earn:,.2f}")
c5.metric("全歷史 APY", f"{apy:.2f}%")

st.markdown("---")
if st.button("🔄 更新數據", type="secondary", use_container_width=True):
    st.cache_resource.clear()
    st.rerun()
