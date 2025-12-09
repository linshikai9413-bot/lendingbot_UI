# app.py - V14 資產監控（Bitfinex2 + 自動載入 Secrets）
import streamlit as st
import ccxt
import pandas as pd
from datetime import datetime, timedelta
import traceback
import plotly.express as px
import json

# ================== 頁面設定 ==================
st.set_page_config(page_title="V14 資產監控 (Bitfinex2)", page_icon="💰", layout="wide")

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

# ================== Secrets 自動載入（向後相容多鍵名） ==================
def load_api_from_secrets_into_session():
    # 支援多種 secrets 命名方式
    api_key = ""
    api_secret = ""
    # common nested dict (recommended)
    bitfinex_block = st.secrets.get("bitfinex") if isinstance(st.secrets, dict) else None
    if bitfinex_block:
        api_key = bitfinex_block.get("api_key") or bitfinex_block.get("apiKey") or bitfinex_block.get("key") or ""
        api_secret = bitfinex_block.get("api_secret") or bitfinex_block.get("apiSecret") or bitfinex_block.get("secret") or ""
    # flat keys fallback
    api_key = api_key or st.secrets.get("bitfinex_api_key", "") or st.secrets.get("BITFINEX_API_KEY", "")
    api_secret = api_secret or st.secrets.get("bitfinex_api_secret", "") or st.secrets.get("BITFINEX_API_SECRET", "")

    if "api_key" not in st.session_state:
        st.session_state.api_key = api_key
    if "api_secret" not in st.session_state:
        st.session_state.api_secret = api_secret

# 先載入 secrets -> session
load_api_from_secrets_into_session()

# ================== Exchange 初始化（強制 bitfinex2） ==================
@st.cache_resource
def init_exchange(api_key, api_secret):
    ex = ccxt.bitfinex({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
    })
    # load markets 確保 client 可用
    ex.load_markets()
    return ex

# ================== Funding API 多路呼叫封裝（v2） ==================
def fetch_funding_credits(ex, symbol='fUSD'):
    try:
        return ex.private_post_auth_r_funding_credits({"symbol": symbol}) or []
    except Exception as e:
        return []

def fetch_funding_offers(ex, symbol='fUSD'):
    try:
        return ex.private_post_auth_r_funding_offers({"symbol": symbol}) or []
    except Exception:
        return []

def fetch_funding_trades(ex, symbol='fUSD', limit=100):
    try:
        return ex.private_post_auth_r_funding_trades_symbol_hist({"symbol": symbol, "limit": limit}) or []
    except Exception:
        return []

# ================== Ledger 處理 ==================
def process_earnings(ledgers):
    recs = []
    if not ledgers:
        return pd.DataFrame()
    for r in ledgers:
        try:
            amt = float(r.get("amount", 0))
            if amt <= 0:
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
    # 若 session 已有值，text_input 以 session value 作為初始值，實現「一開網頁自動填入」
    api_key_input = st.text_input("API Key", value=st.session_state.get("api_key", ""), type="password")
    api_secret_input = st.text_input("API Secret", value=st.session_state.get("api_secret", ""), type="password")
    # 使用者修改時同步到 session_state
    st.session_state.api_key = api_key_input
    st.session_state.api_secret = api_secret_input

    debug_mode = st.checkbox("🐞 除錯模式 (Debug Mode)", value=False)

    if st.button("🔄 刷新資料", type="primary", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()

# 必要檢查
if not st.session_state.get("api_key"):
    st.warning("請在側欄輸入 API Key 或將 API Key 放入 Streamlit Secrets 中（建議）。")
    st.stop()

# ================== 主流程：建立 exchange 並抓資料 ==================
with st.spinner("建立交易所（bitfinex2）連線..."):
    try:
        ex = init_exchange(st.session_state.api_key, st.session_state.api_secret)
    except Exception as e:
        st.error("建立 Bitfinex2 連線失敗，請確認 API Key/Secret 與網路連線。")
        st.text(pretty_err(e))
        st.stop()

with st.spinner("更新資料中..."):
    balances = {}
    ledgers = []
    loans = []
    offers = []
    trades = []
    debug_info = {}
    # balance (v2 wallet)
    try:
        balances = ex.fetch_balance()
    except Exception as e:
        debug_info['balance_error'] = pretty_err(e)
        balances = {}
    # ledgers
    try:
        since = ex.milliseconds() - 365 * 24 * 60 * 60 * 1000
        ledgers = ex.fetch_ledger("USD", since=since, limit=2500)
    except Exception as e:
        debug_info['ledgers_error'] = pretty_err(e)
        ledgers = []
    # loans / offers / trades using v2 endpoints
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

    debug_info['loans_count'] = len(loans) if hasattr(loans, '__len__') else 0
    debug_info['offers_count'] = len(offers) if hasattr(offers, '__len__') else 0
    debug_info['trades_count'] = len(trades) if hasattr(trades, '__len__') else 0

# ================== 收益統計與指標 ==================
df_earn = process_earnings(ledgers)

# --- 修改開始：強制讀取 Funding Wallet (融資錢包) ---
total_assets = 0.0
free_assets = 0.0

# 嘗試從原始 info 解析 Funding 錢包
found_funding = False
if "info" in balances and isinstance(balances["info"], list):
    for wallet in balances["info"]:
        # wallet 結構通常為: [Type, Currency, Total, Interest, Available, ...]
        # 例如: ["funding", "USD", 745.54, 0, 67.09, ...]
        if len(wallet) > 4 and wallet[0] == "funding" and wallet[1] == "USD":
            total_assets = float(wallet[2]) if wallet[2] else 0.0
            free_assets = float(wallet[4]) if wallet[4] else 0.0
            found_funding = True
            break

# 如果沒找到 Funding 錢包，才使用預設 fallback
if not found_funding:
    usd_info = balances.get("USD", balances.get("usd", {"total": 0, "free": 0}))
    total_assets = float(usd_info.get("total", 0) or 0)
    free_assets = float(usd_info.get("free", 0) or 0)
# --- 修改結束 ---

utilization = ((total_assets - free_assets) / total_assets * 100) if total_assets > 0 else 0.0

total_income = df_earn["amount"].sum() if not df_earn.empty else 0.0
cutoff_30d = datetime.now().date() - timedelta(days=30)
last_30d_income = df_earn[df_earn["date"] >= cutoff_30d]["amount"].sum() if not df_earn.empty else 0.0
apy_all_time = 0.0
if not df_earn.empty and total_assets > 0:
    first = df_earn["date"].min()
    days = (datetime.now().date() - first).days + 1
    apy_all_time = (total_income / days / total_assets * 365 * 100) if days > 0 else 0.0

# ================== UI：頂部指標 ==================
st.title("💰 V14 資產監控（Bitfinex2 + Secrets 自動載入）")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("總資產", f"${total_assets:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")
c3.metric("30天收益", f"${last_30d_income:,.2f}")
c4.metric("歷史總收益", f"${total_income:,.2f}")
c5.metric("全歷史 APY", f"{apy_all_time:.2f}%")

st.markdown("---")
st.subheader("📊 每日績效")
if not df_earn.empty:
    df_chart = df_earn.groupby("date")["amount"].sum().reset_index()
    fig = px.bar(df_chart, x="date", y="amount", color_discrete_sequence=[COLOR_BUY])
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("尚無收益資料")

# ================== 明細分頁 ==================
st.markdown("---")
st.subheader("📋 明細")
t1, t2, t3 = st.tabs(["放貸中 (Loans)", "掛單中 (Offers)", "成交記錄 (Trades)"])

with t1:
    if loans:
        rows = []
        # loans often return arrays; support both dict/list
        for l in loans:
            try:
                if isinstance(l, dict):
                    ts = l.get("timestamp") or l.get("mts") or l.get("created")
                    created = safe_dt(ts)
                    amount = float(l.get("amount", l.get("amount_lent", 0) or 0))
                    rate = float(l.get("rate", 0))
                    days = int(l.get("period", 2))
                else:
                    created = safe_dt(l[3] if len(l) > 3 else None)
                    amount = float(l[5]) if len(l) > 5 else 0
                    rate = float(l[11]) if len(l) > 11 else 0
                    days = int(l[12]) if len(l) > 12 else 2
                rows.append({"建立": created.strftime("%Y-%m-%d %H:%M"), "金額": amount, "APY": to_apy(rate), "天數": days})
            except Exception:
                continue
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("目前無放貸中")

with t2:
    if offers:
        rows = []
        for o in offers:
            try:
                if isinstance(o, dict):
                    created = safe_dt(o.get("timestamp") or o.get("created"))
                    amount = float(o.get("amount", 0))
                    rate = float(o.get("rate", o.get("price", 0)))
                    flags = o.get("flags", 0) if isinstance(o.get("flags", 0), int) else 0
                    is_frr = (flags & 1024) > 0 or rate == 0
                else:
                    created = safe_dt(o[2] if len(o) > 2 else None)
                    amount = float(o[4]) if len(o) > 4 else 0
                    rate = float(o[14]) if len(o) > 14 else 0
                    is_frr = rate == 0
                rows.append({"建立": created.strftime("%Y-%m-%d %H:%M"), "金額": amount, "APY": "FRR" if is_frr else f"{to_apy(rate):.2f}%"})
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
                if isinstance(tr, dict):
                    ts = tr.get("timestamp") or tr.get("mts") or tr.get("date")
                    created = safe_dt(ts)
                    amount = float(tr.get("amount", 0))
                    rate = float(tr.get("rate", 0))
                    days = int(tr.get("period", 2))
                else:
                    created = safe_dt(tr[2] if len(tr) > 2 else None)
                    amount = float(tr[4]) if len(tr) > 4 else 0
                    rate = float(tr[5]) if len(tr) > 5 else 0
                    days = int(tr[6]) if len(tr) > 6 else 2
                if amount > 0:
                    rows.append({"成交": created.strftime("%Y-%m-%d %H:%M"), "金額": amount, "APY": to_apy(rate), "天數": days})
            except Exception:
                continue
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("尚無成交紀錄")

# ================== Debug 資訊 ==================
if debug_mode:
    st.markdown("---")
    st.subheader("DEBUG INFO")
    st.write("Exchange id:", getattr(ex, "id", None))
    st.write("Raw balances:", balances)
    st.write("Raw loans:", loans)
    st.write("Raw offers:", offers)
    st.write("Raw trades:", trades)
    st.write("Debug Info:", debug_info)
    st.write("Markets sample:", {k: ex.markets[k] for k in list(ex.markets)[:5]})

st.markdown("---")
st.caption("說明：若出現 InvalidNonce 或授權錯誤，請在 Bitfinex 後台重新建立 API Key；若 Loans/Offers 為空但你確定有掛單，請在 Debug Mode 下貼上 Raw offers 資料以便進一步診斷。")
