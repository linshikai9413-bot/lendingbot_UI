import streamlit as st
import ccxt
import pandas as pd
from datetime import datetime, timedelta
import time
import plotly.express as px
import json
import traceback

st.set_page_config(
    page_title="V14 資產監控 (Bitfinex2 Final)",
    page_icon="💰",
    layout="wide"
)

THEME_BG = "#0E1117"
THEME_CARD = "#1C2128"
TEXT_MAIN = "#E6E6E6"
TEXT_SUB = "#A1A9B3"
COLOR_BUY = "#00C896"

st.markdown(f"""
<style>
.stApp {{
    background-color: {THEME_BG}; 
    color: {TEXT_MAIN};
}}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 工具函式
# ==========================================

def safe_dt(ts):
    if ts is None:
        return datetime.now()
    ts = int(ts)
    if ts > 1e12:
        return datetime.fromtimestamp(ts / 1000)
    return datetime.fromtimestamp(ts)

def to_apy(rate):
    try:
        return float(rate) * 365 * 100
    except:
        return 0.0

def pretty_err(e):
    return ''.join(traceback.format_exception_only(type(e), e)).strip()

# ==========================================
# **強制使用 Bitfinex2（v2）**
# ==========================================

@st.cache_resource
def init_exchange(api_key, api_secret):
    ex = ccxt.bitfinex2({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

# ==========================================
# Funding API：Loans / Offers / Trades
# ==========================================

def fetch_funding_credits(ex):
    try:
        # active loans
        return ex.private_post_auth_r_funding_credits({"symbol": "fUSD"})
    except Exception as e:
        return []

def fetch_funding_offers(ex):
    try:
        return ex.private_post_auth_r_funding_offers({"symbol": "fUSD"})
    except Exception:
        return []

def fetch_funding_trades(ex):
    try:
        return ex.private_post_auth_r_funding_trades_symbol_hist({
            "symbol": "fUSD",
            "limit": 100
        })
    except Exception:
        return []

# ==========================================
# Ledgers：收益分析
# ==========================================

def process_earnings(ledgers):
    records = []
    for row in ledgers:
        try:
            amt = float(row.get("amount", 0))
            if amt > 0:
                dt = safe_dt(row.get("timestamp"))
                records.append({"date": dt.date(), "amount": amt})
        except:
            continue
    return pd.DataFrame(records)

# ==========================================
# UI - 側邊欄
# ==========================================

with st.sidebar:
    st.header("設定")

    if "api_key" not in st.session_state:
        st.session_state.api_key = ""
        st.session_state.api_secret = ""

    st.session_state.api_key = st.text_input("API Key", type="password")
    st.session_state.api_secret = st.text_input("API Secret", type="password")

    debug = st.checkbox("Debug Mode", False)

    if st.button("刷新資料", use_container_width=True):
        st.cache_resource.clear()
        st.experimental_rerun()

if not st.session_state.api_key:
    st.warning("請輸入 API Key")
    st.stop()

# ==========================================
# 建立連線
# ==========================================

with st.spinner("正在連線 Bitfinex2..."):
    try:
        ex = init_exchange(st.session_state.api_key, st.session_state.api_secret)
    except Exception as e:
        st.error("Bitfinex2 初始化失敗")
        st.text(pretty_err(e))
        st.stop()

# ==========================================
# 抓取資料
# ==========================================

with st.spinner("更新資料中..."):
    try:
        balances = ex.fetch_balance()  # v2 wallet
    except Exception as e:
        balances = {}
        bal_error = pretty_err(e)
    else:
        bal_error = None

    try:
        since = ex.milliseconds() - 365 * 24 * 60 * 60 * 1000
        ledgers = ex.fetch_ledger("USD", since=since, limit=2500)
    except:
        ledgers = []

    loans = fetch_funding_credits(ex)
    offers = fetch_funding_offers(ex)
    trades = fetch_funding_trades(ex)

# ==========================================
# 統計
# ==========================================

usd_info = balances.get("USD", {"total": 0, "free": 0})
total_assets = float(usd_info.get("total", 0))
free_assets = float(usd_info.get("free", 0))

utilization = 0
if total_assets > 0:
    utilization = (total_assets - free_assets) / total_assets * 100

df_earn = process_earnings(ledgers)

total_income = df_earn["amount"].sum() if not df_earn.empty else 0
last_30 = df_earn[df_earn["date"] >= (datetime.now().date() - timedelta(days=30))]["amount"].sum() if not df_earn.empty else 0

if not df_earn.empty:
    first = df_earn["date"].min()
    days = (datetime.now().date() - first).days + 1
    apy = (total_income / days / total_assets * 365 * 100) if total_assets else 0
else:
    apy = 0

# ==========================================
# UI：頂部指標
# ==========================================

st.title("💰 V14 資產監控 – Bitfinex2 最終修復版")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("總資產", f"${total_assets:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")
c3.metric("30天收益", f"${last_30:,.2f}")
c4.metric("歷史總收益", f"${total_income:.2f}")
c5.metric("全歷史 APY", f"{apy:.2f}%")

# ==========================================
# 圖表
# ==========================================

st.subheader("📊 每日收益")

if not df_earn.empty:
    df_chart = df_earn.groupby("date")["amount"].sum().reset_index()
    fig = px.bar(df_chart, x="date", y="amount", color_discrete_sequence=[COLOR_BUY])
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("尚無收益資料")

# ==========================================
# 明細
# ==========================================

st.subheader("📋 明細")
t1, t2, t3 = st.tabs(["放貸中 (Loans)", "掛單中 (Offers)", "成交記錄 (Trades)"])

# Loans
with t1:
    if loans:
        rows = []
        for x in loans:
            ts = safe_dt(x[3])
            amount = float(x[5])
            rate = to_apy(float(x[11]))
            days = int(x[12])
            rows.append({
                "建立": ts.strftime("%Y-%m-%d %H:%M"),
                "金額": amount,
                "APY": rate,
                "天數": days
            })
        st.dataframe(pd.DataFrame(rows))
    else:
        st.info("目前無放貸中")

# Offers
with t2:
    if offers:
        rows = []
        for x in offers:
            ts = safe_dt(x[2])
            amount = float(x[4])
            rate = float(x[14])
            rows.append({
                "建立": ts.strftime("%Y-%m-%d %H:%M"),
                "金額": amount,
                "APY": "FRR" if rate == 0 else f"{to_apy(rate):.2f}%"
            })
        st.dataframe(pd.DataFrame(rows))
    else:
        st.info("目前無掛單")

# Trades
with t3:
    if trades:
        rows = []
        for x in trades:
            ts = safe_dt(x[2])
            amount = float(x[4])
            rate = float(x[5])
            days = int(x[6])
            rows.append({
                "成交": ts.strftime("%Y-%m-%d %H:%M"),
                "金額": amount,
                "APY": to_apy(rate),
                "天數": days
            })
        st.dataframe(pd.DataFrame(rows))
    else:
        st.info("尚無成交紀錄")

# Debug Mode
if debug:
    st.subheader("DEBUG INFO")
    st.write("Balances Error", bal_error)
    st.write("Raw balances", balances)
    st.write("Raw loans", loans)
    st.write("Raw offers", offers)
    st.write("Raw trades", trades)
    st.write("Markets", ex.markets)
