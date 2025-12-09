# app.py - V15 資產監控（Bitfinex V2 修復版：正確讀取Funding錢包 + 收益過濾）
import streamlit as st
import ccxt
import pandas as pd
from datetime import datetime, timedelta
import traceback
import plotly.express as px

# ================== 頁面設定 ==================
st.set_page_config(page_title="V15 資產監控 (Bitfinex)", page_icon="💰", layout="wide")

THEME_BG = "#0E1117"
THEME_CARD = "#1C2128"
TEXT_MAIN = "#E6E6E6"
TEXT_SUB = "#A1A9B3"
COLOR_BUY = "#00C896"

st.markdown(f"""
    <style>
    .stApp {{ background-color: {THEME_BG}; color: {TEXT_MAIN}; }}
    </style>
""", unsafe_allow_html=True)

# ================== 工具函式 ==================
def safe_dt(ts):
    try:
        if ts is None:
            return datetime.now()
        ts = int(ts)
        # bitfinex sometimes uses ms timestamp
        if ts > 1e12:
            return datetime.fromtimestamp(ts / 1000)
        return datetime.fromtimestamp(ts)
    except Exception:
        return datetime.now()

def to_apy(rate):
    try:
        return float(rate) * 365 * 100
    except Exception:
        return 0.0

def pretty_err(e):
    return ''.join(traceback.format_exception_only(type(e), e)).strip()

# ================== Secrets 自動載入 ==================
def load_api_from_secrets_into_session():
    api_key = ""
    api_secret = ""
    # 優先讀取巢狀 [bitfinex]
    bitfinex_block = st.secrets.get("bitfinex") if isinstance(st.secrets, dict) else None
    if bitfinex_block:
        api_key = bitfinex_block.get("api_key") or bitfinex_block.get("apiKey") or bitfinex_block.get("key") or ""
        api_secret = bitfinex_block.get("api_secret") or bitfinex_block.get("apiSecret") or bitfinex_block.get("secret") or ""
    # 備用：讀取頂層 keys
    api_key = api_key or st.secrets.get("bitfinex_api_key", "") or st.secrets.get("BITFINEX_API_KEY", "")
    api_secret = api_secret or st.secrets.get("bitfinex_api_secret", "") or st.secrets.get("BITFINEX_API_SECRET", "")

    if "api_key" not in st.session_state:
        st.session_state.api_key = api_key
    if "api_secret" not in st.session_state:
        st.session_state.api_secret = api_secret

load_api_from_secrets_into_session()

# ================== Exchange 初始化 (修正版) ==================
@st.cache_resource
def init_exchange(api_key, api_secret):
    # 使用 ccxt.bitfinex (預設即為 V2)，移除已棄用的 bitfinex2
    ex = ccxt.bitfinex({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

# ================== Funding API (錯誤拋出版) ==================
# 這裡移除 try...except，讓主程式能捕捉並顯示錯誤
def fetch_funding_credits(ex, symbol='fUSD'):
    return ex.private_post_auth_r_funding_credits({"symbol": symbol}) or []

def fetch_funding_offers(ex, symbol='fUSD'):
    return ex.private_post_auth_r_funding_offers({"symbol": symbol}) or []

def fetch_funding_trades(ex, symbol='fUSD', limit=100):
    return ex.private_post_auth_r_funding_trades_symbol_hist({"symbol": symbol, "limit": limit}) or []

# ================== Ledger 處理 (過濾本金版) ==================
def process_earnings(ledgers):
    recs = []
    if not ledgers:
        return pd.DataFrame()
    for r in ledgers:
        try:
            amt = float(r.get("amount", 0))
            # 1. 排除支出
            if amt <= 0:
                continue
            
            # 2. 關鍵修正：排除本金轉入，只保留利息
            # 利息通常標記為 "Margin Funding Payment"
            desc = r.get("description", "") or r.get("info", {}).get("description", "")
            if desc and "Margin Funding Payment" not in desc:
                # 如果有描述但不是利息，視為存款或轉帳，跳過
                continue

            ts = r.get("timestamp") or r.get("mts") or r.get("date")
            dt = safe_dt(ts)
            recs.append({"date": dt.date(), "datetime": dt, "amount": amt})
        except Exception:
            continue
    return pd.DataFrame(recs)

# ================== 側邊欄設定 ==================
with st.sidebar:
    st.header("⚙️ 設定")
    api_key_input = st.text_input("API Key", value=st.session_state.get("api_key", ""), type="password")
    api_secret_input = st.text_input("API Secret", value=st.session_state.get("api_secret", ""), type="password")
    st.session_state.api_key = api_key_input
    st.session_state.api_secret = api_secret_input

    debug_mode = st.checkbox("🐞 除錯模式 (Debug Mode)", value=False)

    if st.button("🔄 刷新資料", type="primary", use_container_width=True):
        st.cache_resource.clear()
        st.rerun() # 修正：使用 st.rerun 取代 experimental_rerun

# ================== 主流程 ==================
if not st.session_state.get("api_key"):
    st.warning("請輸入 API Key")
    st.stop()

with st.spinner("建立連線 (Bitfinex V2)..."):
    try:
        ex = init_exchange(st.session_state.api_key, st.session_state.api_secret)
    except Exception as e:
        st.error(f"連線失敗：{pretty_err(e)}")
        st.stop()

with st.spinner("更新資料中..."):
    balances = {}
    ledgers = []
    loans = []
    offers = []
    trades = []
    debug_info = {}

    # 1. Balances
    try:
        balances = ex.fetch_balance()
    except Exception as e:
        debug_info['balance_error'] = pretty_err(e)

    # 2. Ledgers
    try:
        since = ex.milliseconds() - 365 * 24 * 60 * 60 * 1000
        ledgers = ex.fetch_ledger("USD", since=since, limit=2500)
    except Exception as e:
        debug_info['ledgers_error'] = pretty_err(e)

    # 3. Funding Data (分開 try-catch 以便捕捉特定錯誤)
    try:
        loans = fetch_funding_credits(ex, 'fUSD')
    except Exception as e:
        debug_info['loans_error'] = pretty_err(e)
        loans = []
    
    try:
        offers = fetch_funding_offers(ex, 'fUSD')
    except Exception as e:
        debug_info['offers_error'] = pretty_err(e)
        offers = []

    try:
        trades = fetch_funding_trades(ex, 'fUSD', limit=200)
    except Exception as e:
        debug_info['trades_error'] = pretty_err(e)
        trades = []

    debug_info['loans_count'] = len(loans) if isinstance(loans, list) else 0
    debug_info['offers_count'] = len(offers) if isinstance(offers, list) else 0
    debug_info['trades_count'] = len(trades) if isinstance(trades, list) else 0

# ================== 計算邏輯修正 ==================
df_earn = process_earnings(ledgers)

# --- 資產修正：強制讀取 Funding Wallet ---
total_assets = 0.0
free_assets = 0.0
found_funding_wallet = False

# Bitfinex 的 'info' 欄位包含所有錢包類型的原始數據
if "info" in balances and isinstance(balances["info"], list):
    for wallet in balances["info"]:
        # wallet 結構通常為: [Type, Currency, Total, Interest, Available, ...]
        # 例如: ["funding", "USD", 745.54, 0, 67.09, ...]
        if len(wallet) > 4 and wallet[0] == "funding" and wallet[1] == "USD":
            total_assets = float(wallet[2]) if wallet[2] else 0.0
            free_assets = float(wallet[4]) if wallet[4] else 0.0
            found_funding_wallet = True
            break

# 如果沒找到 Funding Wallet，才回退到預設 (通常預設只抓到 Exchange Wallet)
if not found_funding_wallet:
    usd_info = balances.get("USD", balances.get("usd", {"total": 0, "free": 0}))
    total_assets = float(usd_info.get("total", 0) or 0)
    free_assets = float(usd_info.get("free", 0) or 0)

utilization = ((total_assets - free_assets) / total_assets * 100) if total_assets > 0 else 0.0

total_income = df_earn["amount"].sum() if not df_earn.empty else 0.0
cutoff_30d = datetime.now().date() - timedelta(days=30)
last_30d_income = df_earn[df_earn["date"] >= cutoff_30d]["amount"].sum() if not df_earn.empty else 0.0

apy_all_time = 0.0
if not df_earn.empty and total_assets > 0:
    first = df_earn["date"].min()
    days = (datetime.now().date() - first).days + 1
    # 簡單年化公式
    apy_all_time = (total_income / days / total_assets * 365 * 100) if days > 0 else 0.0

# ================== UI 顯示 ==================
st.title("💰 V15 資產監控（Bitfinex2 修復版）")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("總資產 (Funding)", f"${total_assets:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")
c3.metric("30天收益 (估)", f"${last_30d_income:,.2f}")
c4.metric("歷史總收益 (估)", f"${total_income:,.2f}")
c5.metric("全歷史 APY", f"{apy_all_time:.2f}%")

st.markdown("---")
st.subheader("📊 每日績效 (排除本金)")
if not df_earn.empty:
    df_chart = df_earn.groupby("date")["amount"].sum().reset_index()
    fig = px.bar(df_chart, x="date", y="amount", color_discrete_sequence=[COLOR_BUY])
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("尚無收益資料")

st.markdown("---")
st.subheader("📋 明細")
t1, t2, t3 = st.tabs(["放貸中 (Loans)", "掛單中 (Offers)", "成交記錄 (Trades)"])

# 顯示用的輔助函式，兼容 list 和 dict
def get_val(item, keys, idx, default=0):
    if isinstance(item, dict):
        for k in keys:
            if k in item: return item[k]
        return default
    elif isinstance(item, list) and len(item) > idx:
        return item[idx]
    return default

with t1:
    if loans:
        rows = []
        for l in loans:
            try:
                # Loan 結構 (list): [id, symbol, side, mts_create, mts_update, amount, flags, status, rate, period, ...]
                ts = get_val(l, ["timestamp", "mts", "created"], 3)
                amount = float(get_val(l, ["amount"], 5))
                rate = float(get_val(l, ["rate"], 11)) # 注意：list index 依賴 API 版本，若錯誤需看 debug
                days = int(get_val(l, ["period"], 12, 2))
                
                rows.append({
                    "建立": safe_dt(ts).strftime("%m-%d %H:%M"), 
                    "金額": amount, 
                    "APY": f"{to_apy(rate):.2f}%", 
                    "天數": days
                })
            except Exception:
                continue
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("目前無放貸中 (若確定有單，請看下方 Debug Info 的 loans_error)")

with t2:
    if offers:
        rows = []
        for o in offers:
            try:
                # Offer 結構 (list): [id, symbol, mts_create, mts_update, amount, original_amount, type, ..., rate, period, ...]
                ts = get_val(o, ["timestamp", "created"], 2)
                amount = float(get_val(o, ["amount"], 4))
                rate = float(get_val(o, ["rate", "price"], 14))
                
                rows.append({
                    "建立": safe_dt(ts).strftime("%m-%d %H:%M"), 
                    "金額": amount, 
                    "APY": f"{to_apy(rate):.2f}%"
                })
            except Exception:
                continue
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("目前無掛單")

with t3:
    if trades:
        rows = []
        for tr in trades:
            try:
                ts = get_val(tr, ["timestamp", "mts", "date"], 2)
                amount = float(get_val(tr, ["amount"], 4))
                rate = float(get_val(tr, ["rate"], 5))
                days = int(get_val(tr, ["period"], 6, 2))
                
                if amount > 0:
                    rows.append({
                        "成交": safe_dt(ts).strftime("%m-%d %H:%M"), 
                        "金額": amount, 
                        "APY": f"{to_apy(rate):.2f}%", 
                        "天數": days
                    })
            except Exception:
                continue
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("尚無成交紀錄")

# ================== Debug 資訊 ==================
if debug_mode:
    st.markdown("---")
    st.subheader("🛠️ DEBUG INFO")
    st.write("Exchange ID:", getattr(ex, "id", "Unknown"))
    
    if debug_info:
        st.error("⚠️ 偵測到錯誤訊息：")
        st.json(debug_info)
    
    with st.expander("查看原始數據 (Raw Data)"):
        st.write("Funding Wallet Found:", found_funding_wallet)
        st.write("Calculated Assets:", total_assets)
        st.write("Loans (Raw):", loans)
        st.write("Offers (Raw):", offers)
        st.write("Balances Info (Partial):", balances.get("info", [])[:3])
