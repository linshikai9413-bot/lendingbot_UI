# app.py - V14 資產監控 (Pro) - Robust Bitfinex Funding fetcher
# 可直接覆蓋原檔
import streamlit as st
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta
import plotly.express as px
import traceback
import json

# ================= 0. 設定 =================
st.set_page_config(
    page_title="V14 資產監控 (Pro)",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

THEME_BG = "#0E1117"
THEME_CARD = "#1C2128"
TEXT_MAIN = "#E6E6E6"
TEXT_SUB = "#A1A9B3"
COLOR_BUY = "#00C896"
COLOR_SELL = "#FF4B4B"

st.markdown(f"""
    <style>
    .stApp {{ background-color: {THEME_BG}; color: {TEXT_MAIN}; }}
    div[data-testid="stMetric"] {{
        background-color: {THEME_CARD};
        padding: 15px;
        border-radius: 8px;
        border-left: 4px solid {COLOR_BUY};
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }}
    div[data-testid="stMetric"] label {{ font-size: 0.9rem; color: {TEXT_SUB}; }}
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {{ font-size: 1.6rem; color: {COLOR_BUY}; }}
    div[data-testid="stDataFrame"] {{ border: 1px solid #30363D; border-radius: 8px; }}
    </style>
""", unsafe_allow_html=True)

# ================= 工具函式 =================

def safe_timestamp_to_datetime(ts):
    try:
        if ts is None:
            return datetime.now()
        ts = float(ts)
        # bitfinex sometimes returns seconds or ms
        if ts > 1e12:
            return datetime.fromtimestamp(ts / 1000.0)
        elif ts > 1e9:
            return datetime.fromtimestamp(ts)
        else:
            return datetime.fromtimestamp(ts)
    except Exception:
        return datetime.now()

def to_apy(daily_rate):
    try:
        return float(daily_rate) * 365 * 100
    except Exception:
        return 0.0

def pretty_exception(e):
    return ''.join(traceback.format_exception_only(type(e), e)).strip()

# ================= Exchange 初始化（嘗試 bitfinex2 -> bitfinex） =================

@st.cache_resource
def build_exchange(api_key, api_secret):
    """
    嘗試建立 bitfinex2（優先）或 bitfinex（備援）連線。
    回傳 (exchange, which) 其中 which 為 'bitfinex2' 或 'bitfinex'
    """
    errs = []
    # 優先嘗試 bitfinex2 (v2)
    try:
        exch = ccxt.bitfinex2({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            # nonce使用內建即可
        })
        # 嘗試載入市場驗證連線是否可用
        try:
            exch.check_required_credentials()
            exch.load_markets()
            return exch, 'bitfinex2', None
        except Exception as e:
            errs.append(f"bitfinex2 load failed: {pretty_exception(e)}")
    except Exception as e:
        errs.append(f"bitfinex2 init failed: {pretty_exception(e)}")

    # 備援：嘗試 legacy bitfinex
    try:
        exch = ccxt.bitfinex({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
        })
        try:
            exch.check_required_credentials()
            exch.load_markets()
            return exch, 'bitfinex', None
        except Exception as e:
            errs.append(f"bitfinex load failed: {pretty_exception(e)}")
    except Exception as e:
        errs.append(f"bitfinex init failed: {pretty_exception(e)}")

    return None, None, errs

# ================= Funding / Offers / Trades 多路徵詢 =================

def try_methods_for_funding_credits(exchange, symbol='fUSD'):
    """
    嘗試多種方法取得 funding credits（放貸中）
    回傳 list 或空 list
    """
    results = []
    # 1) 標準 ccxt 高階（若有）
    try:
        if hasattr(exchange, 'fetch_funding_credits'):
            res = exchange.fetch_funding_credits(symbol)
            if res:
                return res, "fetch_funding_credits"
    except Exception as e:
        results.append(f"fetch_funding_credits failed: {pretty_exception(e)}")

    # 2) raw private_post auth r funding credits
    try:
        if hasattr(exchange, 'private_post_auth_r_funding_credits'):
            res = exchange.private_post_auth_r_funding_credits({'symbol': symbol})
            if res:
                return res, "private_post_auth_r_funding_credits"
    except Exception as e:
        results.append(f"private_post_auth_r_funding_credits failed: {pretty_exception(e)}")

    # 3) some ccxt builds expose different naming: without 'auth' or with trailing symbol
    try:
        if hasattr(exchange, 'private_post_r_funding_credits'):
            res = exchange.private_post_r_funding_credits({'symbol': symbol})
            if res:
                return res, "private_post_r_funding_credits"
    except Exception as e:
        results.append(f"private_post_r_funding_credits failed: {pretty_exception(e)}")

    try:
        # fallback call to generic request for v2 path
        if hasattr(exchange, 'request'):
            path = '/v2/auth/r/funding/credits'
            # ccxt.request expects method, path, params, headers?
            res = exchange.request('privatePostAuthRFundingCredits', path, {'symbol': symbol})
            if res:
                return res, "request_private_v2"
    except Exception as e:
        results.append(f"generic request to v2 credits failed: {pretty_exception(e)}")

    # 4) 如果都失敗，回傳空 list 與 debug info
    return [], "all_failed: " + " | ".join(results)

def try_methods_for_funding_offers(exchange, symbol='fUSD'):
    """嘗試多種方法取得 funding offers（掛單中）"""
    results = []
    try:
        if hasattr(exchange, 'fetch_funding_offers'):
            res = exchange.fetch_funding_offers(symbol)
            if res:
                return res, "fetch_funding_offers"
    except Exception as e:
        results.append(f"fetch_funding_offers failed: {pretty_exception(e)}")

    try:
        if hasattr(exchange, 'private_post_auth_r_funding_offers'):
            res = exchange.private_post_auth_r_funding_offers({'symbol': symbol})
            if res:
                return res, "private_post_auth_r_funding_offers"
    except Exception as e:
        results.append(f"private_post_auth_r_funding_offers failed: {pretty_exception(e)}")

    try:
        if hasattr(exchange, 'private_post_r_funding_offers'):
            res = exchange.private_post_r_funding_offers({'symbol': symbol})
            if res:
                return res, "private_post_r_funding_offers"
    except Exception as e:
        results.append(f"private_post_r_funding_offers failed: {pretty_exception(e)}")

    try:
        if hasattr(exchange, 'request'):
            path = '/v2/auth/r/funding/offers'
            res = exchange.request('privatePostAuthRFundingOffers', path, {'symbol': symbol})
            if res:
                return res, "request_private_v2_offers"
    except Exception as e:
        results.append(f"generic request to v2 offers failed: {pretty_exception(e)}")

    return [], "all_failed: " + " | ".join(results)

def try_methods_for_funding_trades(exchange, symbol='fUSD', limit=50):
    """嘗試多種方法取得 funding trades（成交歷史）"""
    results = []
    try:
        # 有些版本的 ccxt 提供 helper
        if hasattr(exchange, 'fetch_funding_trades'):
            res = exchange.fetch_funding_trades(symbol, limit=limit)
            if res:
                return res, "fetch_funding_trades"
    except Exception as e:
        results.append(f"fetch_funding_trades failed: {pretty_exception(e)}")

    try:
        if hasattr(exchange, 'private_post_auth_r_funding_trades_symbol_hist'):
            res = exchange.private_post_auth_r_funding_trades_symbol_hist({'symbol': symbol, 'limit': limit})
            if res:
                return res, "private_post_auth_r_funding_trades_symbol_hist"
    except Exception as e:
        results.append(f"private_post_auth_r_funding_trades_symbol_hist failed: {pretty_exception(e)}")

    try:
        if hasattr(exchange, 'private_post_r_funding_trades_symbol_hist'):
            res = exchange.private_post_r_funding_trades_symbol_hist({'symbol': symbol, 'limit': limit})
            if res:
                return res, "private_post_r_funding_trades_symbol_hist_alt"
    except Exception as e:
        results.append(f"private_post_r_funding_trades_symbol_hist failed: {pretty_exception(e)}")

    try:
        if hasattr(exchange, 'request'):
            path = f'/v2/auth/r/funding/trades/{symbol}/hist'
            res = exchange.request('privatePostAuthRFundingTradesSymbolHist', path, {'symbol': symbol, 'limit': limit})
            if res:
                return res, "request_private_v2_trades"
    except Exception as e:
        results.append(f"generic request to v2 trades failed: {pretty_exception(e)}")

    return [], "all_failed: " + " | ".join(results)

# ================= 資料處理 =================

def process_earnings(ledgers):
    data = []
    if not ledgers:
        return pd.DataFrame()
    # ledgers 可能為 list of dict 或其他
    keywords = ['funding', 'payment', 'interest', 'payout']
    exclude_types = ['transaction', 'transfer', 'deposit', 'withdrawal']
    for entry in ledgers:
        try:
            amount = float(entry.get('amount', 0))
            if amount <= 0:
                continue
            typ = str(entry.get('type', '')).lower()
            desc = str(entry.get('description', '')).lower()
            info = json.dumps(entry.get('info', entry.get('details', {}))).lower()
            if any(x in typ for x in exclude_types):
                continue
            is_payout = 'payout' in typ or 'funding' in typ or any(k in info for k in keywords)
            if is_payout:
                ts = entry.get('timestamp') or entry.get('date') or entry.get('time')
                dt = safe_timestamp_to_datetime(ts) if ts else datetime.now()
                data.append({
                    'date': dt.date(),
                    'datetime': dt,
                    'amount': amount
                })
        except Exception:
            continue
    return pd.DataFrame(data)

# ================= 主流程 =================

# 側欄：設定
with st.sidebar:
    st.header("⚙️ 設定")
    if "api_key" not in st.session_state:
        st.session_state.api_key = ""
        st.session_state.api_secret = ""

    if "bitfinex" in st.secrets:
        st.session_state.api_key = st.secrets["bitfinex"]["api_key"]
        st.session_state.api_secret = st.secrets["bitfinex"]["api_secret"]
        st.success("🔒 API Key 已載入")
    else:
        st.session_state.api_key = st.text_input("API Key", type="password")
        st.session_state.api_secret = st.text_input("API Secret", type="password")

    st.markdown("---")
    debug_mode = st.checkbox("🐞 除錯模式 (Debug Mode)", value=False)

    if st.button("🔄 刷新數據", type="primary", use_container_width=True):
        st.cache_resource.clear()
        st.experimental_rerun()

st.title("💰 V14 資產監控 - Robust Funding Fetcher")

if not st.session_state.api_key:
    st.warning("請輸入 API Key")
    st.stop()

# 建立 exchange
with st.spinner("建立交易所連線..."):
    exchange, which, errs = build_exchange(st.session_state.api_key, st.session_state.api_secret)
    if exchange is None:
        st.error("無法建立交易所連線。錯誤：")
        st.text('\n'.join(errs))
        st.stop()
    else:
        st.info(f"已建立連線：{which}")

# 取得資料
with st.spinner("更新資料中..."):
    balance_data = None
    raw_ledgers = []
    loans = []
    offers = []
    trades = []
    debug_info = {}

    # 1) balance（不指定 type，先拿通用）
    try:
        balance_data = exchange.fetch_balance()
    except Exception as e:
        debug_info['balance_error'] = pretty_exception(e)
        # fallback: try funding-type balance
        try:
            balance_data = exchange.fetch_balance({'type': 'funding'})
        except Exception as e2:
            debug_info['balance_error_2'] = pretty_exception(e2)
            balance_data = {}

    # 2) ledgers (交易帳本)
    try:
        # 取得近一年交易紀錄（limit 2500 可能過大，視 API 而定）
        since_1y = exchange.milliseconds() - (365 * 24 * 60 * 60 * 1000)
        raw_ledgers = exchange.fetch_ledger('USD', since=since_1y, limit=2500)
    except Exception as e:
        debug_info['ledgers_error'] = pretty_exception(e)
        raw_ledgers = []

    # 3) loans - 放貸中
    try:
        loans_res, loans_method = try_methods_for_funding_credits(exchange, 'fUSD')
        loans = loans_res or []
        debug_info['loans_method'] = loans_method
    except Exception as e:
        debug_info['loans_exception'] = pretty_exception(e)
        loans = []

    # 4) offers - 掛單中
    try:
        offers_res, offers_method = try_methods_for_funding_offers(exchange, 'fUSD')
        offers = offers_res or []
        debug_info['offers_method'] = offers_method
    except Exception as e:
        debug_info['offers_exception'] = pretty_exception(e)
        offers = []

    # 5) trades - 成交歷史
    try:
        trades_res, trades_method = try_methods_for_funding_trades(exchange, 'fUSD', limit=50)
        trades = trades_res or []
        debug_info['trades_method'] = trades_method
    except Exception as e:
        debug_info['trades_exception'] = pretty_exception(e)
        trades = []

    # 6) trades fallback: some endpoints return arrays under different names
    # debug_info snapshot
    debug_info['loans_count'] = len(loans) if hasattr(loans, '__len__') else 0
    debug_info['offers_count'] = len(offers) if hasattr(offers, '__len__') else 0
    debug_info['trades_count'] = len(trades) if hasattr(trades, '__len__') else 0

# 處理收益
df_earnings = process_earnings(raw_ledgers)

# 指標計算
usd_bal = {}
if balance_data:
    # balance_data 可能是 dict with 'USD'
    if isinstance(balance_data, dict):
        usd_bal = balance_data.get('USD', balance_data.get('usd', {'total': 0.0, 'free': 0.0}))
    else:
        usd_bal = {'total': 0.0, 'free': 0.0}
else:
    usd_bal = {'total': 0.0, 'free': 0.0}

total_assets = float(usd_bal.get('total', 0.0) or 0.0)
free_assets = float(usd_bal.get('free', 0.0) or 0.0)
utilization = ((total_assets - free_assets) / total_assets * 100) if total_assets > 0 else 0.0

total_income = 0.0
last_30d_income = 0.0
apy_all_time = 0.0

if not df_earnings.empty:
    total_income = df_earnings['amount'].sum()
    cutoff_30d = pd.Timestamp.now().date() - timedelta(days=30)
    df_earnings['date'] = pd.to_datetime(df_earnings['date']).dt.date
    last_30d_income = df_earnings[df_earnings['date'] >= cutoff_30d]['amount'].sum()

    first_date = df_earnings['date'].min()
    days_diff = (pd.Timestamp.now().date() - first_date).days + 1
    if days_diff > 0 and total_assets > 0:
        apy_all_time = (total_income / days_diff / total_assets) * 365 * 100

# 第一層：指標
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("總資產", f"${total_assets:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")
c3.metric("30天收益", f"${last_30d_income:,.2f}")
c4.metric("歷史總收益", f"${total_income:,.2f}")
c5.metric("全歷史 APY", f"{apy_all_time:.2f}%")

st.markdown("---")
st.subheader("📊 每日績效")

if not df_earnings.empty:
    range_opt = st.radio("範圍", ["7天", "30天", "1年", "全部"], index=1, horizontal=True)
    end_date = pd.Timestamp.now().date()
    start_date = df_earnings['date'].min()

    if range_opt == "7天": start_date = end_date - timedelta(days=7)
    elif range_opt == "30天": start_date = end_date - timedelta(days=30)
    elif range_opt == "1年": start_date = end_date - timedelta(days=365)

    if start_date > end_date: start_date = end_date

    full_dates = pd.DataFrame(pd.date_range(start=start_date, end=end_date).date, columns=['date'])
    mask = (df_earnings['date'] >= start_date) & (df_earnings['date'] <= end_date)

    df_chart = df_earnings.loc[mask].groupby('date')['amount'].sum().reset_index()
    df_chart = pd.merge(full_dates, df_chart, on='date', how='left').fillna(0)
    df_chart['daily_apy'] = (df_chart['amount'] / total_assets * 365 * 100) if total_assets > 0 else 0.0

    if not df_chart.empty:
        fig = px.bar(
            df_chart, x='date', y='amount',
            title=f"區間收益: ${df_chart['amount'].sum():.2f}",
            labels={'date': '日期', 'amount': '收益 (USD)'},
            color_discrete_sequence=[COLOR_BUY]
        )
        fig.update_layout(
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor='#333'),
            bargap=0.1, height=350, margin=dict(l=20, r=20, t=40, b=20)
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("無數據")
else:
    st.info("尚無收益資料")

# 明細分頁
st.markdown("---")
st.subheader("📋 資產明細")
t1, t2, t3, t4 = st.tabs(["放貸中 (Loans)", "掛單中 (Offers)", "最近成交 (Trades)", "每日收益 (Earnings)"])

with t1:
    if loans and len(loans) > 0:
        # 嘗試解析多種格式
        rows = []
        # loans 可以是 list of dict 或 list of arrays
        for l in loans:
            try:
                if isinstance(l, dict):
                    amount = float(l.get('amount', l.get('amount_lent', 0) or 0))
                    rate = float(l.get('rate', l.get('rate_percent', 0) or 0))
                    ts = l.get('timestamp') or l.get('created') or l.get('mts') or l.get('date')
                    created = safe_timestamp_to_datetime(ts)
                    info = l.get('info', l.get('details', {})) or {}
                    days = int(info.get('period', l.get('period', 2) or 2))
                elif isinstance(l, (list, tuple)):
                    # bitfinex raw arrays might be used; best-effort parsing
                    # common raw array shapes vary; attempt typical indices
                    created = safe_timestamp_to_datetime(l[3] if len(l) > 3 else None)
                    amount = abs(float(l[5])) if len(l) > 5 else 0.0
                    rate = float(l[11]) if len(l) > 11 else 0.0
                    days = int(l[12]) if len(l) > 12 else 2
                else:
                    continue
                due = created + timedelta(days=days)
                remain = max(0.0, (due - datetime.now()).total_seconds() / 86400)
                rows.append({
                    "開單": created.strftime('%Y-%m-%d %H:%M'),
                    "金額": amount,
                    "APY": to_apy(rate),
                    "天數": days,
                    "剩餘": f"{remain:.1f} 天",
                    "到期": due.strftime('%Y-%m-%d %H:%M')
                })
            except Exception:
                continue
        if rows:
            st.dataframe(pd.DataFrame(rows).sort_values("APY", ascending=False), use_container_width=True,
                         column_config={"APY": st.column_config.NumberColumn(format="%.2f%%"), "金額": st.column_config.NumberColumn(format="$%.2f")})
        else:
            st.info("無放貸資料")
    else:
        st.info("無放貸 (請確認 API 權限 'Margin Funding' 是否開啟 或 API Key 是否使用 v2)")

with t2:
    if offers and len(offers) > 0:
        rows = []
        for o in offers:
            try:
                if isinstance(o, dict):
                    amount = float(o.get('amount', o.get('amount_lent', 0) or 0))
                    rate = float(o.get('rate', o.get('price', 0) or 0))
                    ts = o.get('timestamp') or o.get('created') or o.get('mts') or o.get('date')
                    created = safe_timestamp_to_datetime(ts)
                    info = o.get('info', o.get('details', {})) or {}
                    days = int(info.get('period', o.get('period', 2) or 2))
                    flags = info.get('flags', o.get('flags', 0) or 0)
                    is_frr = (flags & 1024) > 0 or rate == 0
                elif isinstance(o, (list, tuple)):
                    rate = float(o[14]) if len(o) > 14 else 0
                    is_frr = rate == 0
                    amount = float(o[4]) if len(o) > 4 else 0
                    days = int(o[15]) if len(o) > 15 else 2
                    created = safe_timestamp_to_datetime(o[2] if len(o) > 2 else None)
                else:
                    continue
                rows.append({
                    "金額": amount,
                    "類型": "FRR" if is_frr else "Limit",
                    "APY": "FRR" if is_frr else f"{to_apy(rate):.2f}%",
                    "天數": days,
                    "建立": created.strftime('%Y-%m-%d %H:%M')
                })
            except Exception:
                continue
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         column_config={"金額": st.column_config.NumberColumn(format="$%.2f")})
        else:
            st.info("無掛單資料")
    else:
        st.info("無掛單")

with t3:
    if trades and isinstance(trades, (list, tuple)) and len(trades) > 0:
        rows = []
        # trades 可能為 list of arrays 或 list of dicts
        for t in (trades[:200] if hasattr(trades, '__len__') else trades):
            try:
                if isinstance(t, dict):
                    ts = t.get('timestamp') or t.get('mts') or t.get('date')
                    created = safe_timestamp_to_datetime(ts)
                    amt = float(t.get('amount', t.get('amount_lent', 0) or 0))
                    rate = float(t.get('rate', t.get('rate_percent', 0) or 0))
                    days = int(t.get('period', 2))
                elif isinstance(t, (list, tuple)):
                    # typical raw trade array: [id, ..., mts, amount, rate, period, ...]
                    created = safe_timestamp_to_datetime(t[2] if len(t) > 2 else None)
                    amt = float(t[4]) if len(t) > 4 else 0
                    rate = float(t[5]) if len(t) > 5 else 0
                    days = int(t[6]) if len(t) > 6 else 2
                else:
                    continue
                if amt > 0:
                    rows.append({
                        "成交": created.strftime('%Y-%m-%d %H:%M'),
                        "金額": abs(amt),
                        "APY": to_apy(rate),
                        "天數": days
                    })
            except Exception:
                continue
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         column_config={"APY": st.column_config.NumberColumn(format="%.2f%%"), "金額": st.column_config.NumberColumn(format="$%.2f")})
        else:
            st.info("無有效借出成交")
    else:
        st.info("無成交紀錄")

with t4:
    if 'df_chart' in locals() and not df_chart.empty:
        df_show = df_chart.sort_values('date', ascending=False)[['date', 'amount', 'daily_apy']]
        df_show.columns = ['日期', '收益 (USD)', '當日 APY']
        st.dataframe(df_show, use_container_width=True,
                     column_config={
                         "日期": st.column_config.DateColumn(format="YYYY-MM-DD"),
                         "收益 (USD)": st.column_config.NumberColumn(format="$%.2f"),
                         "當日 APY": st.column_config.NumberColumn(format="%.2f%%")
                     })
    else:
        st.info("無數據")

# DEBUG 專區
if debug_mode:
    st.markdown("---")
    st.error("🚧 DEBUG MODE 🚧")
    st.subheader("Exchange Info")
    st.write({
        "exchange_id": getattr(exchange, 'id', None),
        "exchange_class": which,
        "has_fetch_funding_credits": hasattr(exchange, 'fetch_funding_credits'),
        "has_fetch_funding_offers": hasattr(exchange, 'fetch_funding_offers'),
        "has_fetch_funding_trades": hasattr(exchange, 'fetch_funding_trades'),
    })
    st.subheader("Debug Info")
    st.write(debug_info)

    st.subheader("Raw Loans Data")
    try:
        st.write(loans)
    except Exception as e:
        st.write("Cannot display loans:", pretty_exception(e))

    st.subheader("Raw Offers Data")
    try:
        st.write(offers)
    except Exception as e:
        st.write("Cannot display offers:", pretty_exception(e))

    st.subheader("Raw Trades Data")
    try:
        st.write(trades)
    except Exception as e:
        st.write("Cannot display trades:", pretty_exception(e))

    st.markdown("---")
    st.info("若 Raw Data 為空，請確認：")
    st.write("""
    1. API Key 是否有 'Margin Funding' 或 'Funding' 的 Read 權限 (Bitfinex v2 權限)。
    2. Key 是否針對 v2 API（若你在 Bitfinex UI 建立 key，可選 v1 或 v2；建議使用 v2）。
    3. 若使用 API Key 的權限、版本皆無問題但仍為空，請把上方 Raw Loans / Offers / Trades 的內容貼給我以便進一步診斷。
    """)

# 结束
st.markdown("---")
st.caption("若要我直接幫你修掉程式邏輯或客製額外的欄位（例如 USD 淨值合併、不同 Funding 幣別），把 Debug Mode 的 Raw Data 貼上，我會給出修正碼。")
