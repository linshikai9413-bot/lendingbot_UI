# app.py - V16 極簡版 (只顯示核心指標 + 數據修正)
import streamlit as st
import ccxt
import pandas as pd
from datetime import datetime, timedelta
import traceback

# ================== 頁面設定 ==================
st.set_page_config(page_title="Bitfinex 資產監控 (極簡版)", page_icon="💰", layout="centered")

THEME_BG = "#0E1117"
TEXT_MAIN = "#E6E6E6"

st.markdown(f"""
    <style>
    .stApp {{ background-color: {THEME_BG}; color: {TEXT_MAIN}; }}
    div[data-testid="stMetricValue"] {{ font-size: 2rem !important; }}
    </style>
""", unsafe_allow_html=True)

# ================== 工具函式 ==================
def safe_dt(ts):
    try:
        if ts is None: return datetime.now()
        ts = int(ts)
        if ts > 1e12: return datetime.fromtimestamp(ts / 1000)
        return datetime.fromtimestamp(ts)
    except: return datetime.now()

# ================== Secrets 自動載入 ==================
def load_api():
    api_key = st.session_state.get("api_key", "")
    api_secret = st.session_state.get("api_secret", "")
    
    if not api_key or not api_secret:
        # 嘗試從 secrets.toml 讀取
        bf_block = st.secrets.get("bitfinex") if isinstance(st.secrets, dict) else None
        if bf_block:
            api_key = bf_block.get("api_key") or bf_block.get("key") or ""
            api_secret = bf_block.get("api_secret") or bf_block.get("secret") or ""
        
        # 存回 session
        if api_key: st.session_state.api_key = api_key
        if api_secret: st.session_state.api_secret = api_secret

load_api()

# ================== Exchange 初始化 ==================
@st.cache_resource
def init_exchange(api_key, api_secret):
    ex = ccxt.bitfinex({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

# ================== 核心邏輯：計算收益 (修正版) ==================
def calculate_earnings(ledgers):
    total_earn = 0.0
    last_30d_earn = 0.0
    first_date = datetime.now()
    has_data = False
    
    cutoff_30d = datetime.now() - timedelta(days=30)

    if not ledgers:
        return 0.0, 0.0, 0.0

    for r in ledgers:
        try:
            amt = float(r.get("amount", 0))
            if amt <= 0: continue # 排除支出
            
            # --- 關鍵修正：排除本金 ---
            # 必須確認描述包含 "Margin Funding Payment" (利息)
            desc = r.get("description", "") or r.get("info", {}).get("description", "")
            if "Margin Funding Payment" not in desc:
                continue 
            # -------------------------

            ts = r.get("timestamp") or r.get("mts")
            dt = safe_dt(ts)
            
            total_earn += amt
            if dt >= cutoff_30d:
                last_30d_earn += amt
            
            if not has_data or dt < first_date:
                first_date = dt
                has_data = True

        except: continue
        
    days_diff = (datetime.now() - first_date).days + 1 if has_data else 1
    return total_earn, last_30d_earn, days_diff

# ================== 側邊欄 (保留以輸入API) ==================
with st.sidebar:
    st.header("⚙️ 設定")
    # 如果 secrets 有值，這裡會自動填入
    k = st.text_input("API Key", value=st.session_state.get("api_key",""), type="password")
    s = st.text_input("API Secret", value=st.session_state.get("api_secret",""), type="password")
    st.session_state.api_key = k
    st.session_state.api_secret = s
    
    if st.button("🔄 刷新", type="primary"):
        st.cache_resource.clear()
        st.rerun()

# ================== 主程式 ==================
if not st.session_state.get("api_key"):
    st.warning("請設定 API Key")
    st.stop()

with st.spinner("更新數據中..."):
    try:
        ex = init_exchange(st.session_state.api_key, st.session_state.api_secret)
        
        # 1. 獲取餘額 (用來算資產)
        balances = ex.fetch_balance()
        
        # 2. 獲取流水帳 (用來算收益) - 抓過去 1 年
        since = ex.milliseconds() - 365 * 24 * 60 * 60 * 1000
        ledgers = ex.fetch_ledger("USD", since=since, limit=2000)
        
    except Exception as e:
        st.error(f"連線錯誤: {pretty_err(e)}")
        st.stop()

# --- 數據處理 ---

# 1. 修正資產：找 Funding Wallet
total_assets = 0.0
free_assets = 0.0
if "info" in balances and isinstance(balances["info"], list):
    for wallet in balances["info"]:
        # 找 ["funding", "USD", ...]
        if len(wallet) > 4 and wallet[0] == "funding" and wallet[1] == "USD":
            total_assets = float(wallet[2]) if wallet[2] else 0.0
            free_assets = float(wallet[4]) if wallet[4] else 0.0
            break
# 如果沒找到，fallback
if total_assets == 0:
    usd = balances.get("USD", {})
    total_assets = float(usd.get("total", 0))

# 2. 計算指標
utilization = ((total_assets - free_assets) / total_assets * 100) if total_assets > 0 else 0.0
total_income, last_30d_income, days_run = calculate_earnings(ledgers)

# 3. 計算 APY
apy = 0.0
if total_assets > 0 and days_run > 0:
    apy = (total_income / days_run / total_assets * 365 * 100)

# ================== 顯示結果 ==================
st.title("💰 Bitfinex 資產監控")
st.markdown("---")

# 第一排
c1, c2 = st.columns(2)
c1.metric("總資產 (Funding)", f"${total_assets:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")

st.markdown("---")

# 第二排
c3, c4, c5 = st.columns(3)
c3.metric("30天收益 (估)", f"${last_30d_income:,.2f}")
c4.metric("歷史總收益 (估)", f"${total_income:,.2f}")
c5.metric("全歷史 APY", f"{apy:.2f}%")

st.markdown("---")
if total_income == 0 and total_assets > 0:
    st.caption("提示：目前收益顯示為 0，可能是因為剛剛才開始放貸，尚未收到第一筆利息 (Margin Funding Payment)。")
