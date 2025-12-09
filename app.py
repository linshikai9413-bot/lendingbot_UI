import streamlit as st
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta
import plotly.express as px

# ================= 1. 設定與樣式 =================
st.set_page_config(
    page_title="V14 資產監控 (Pro)",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 風格設定
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

# ================= 2. 核心工具 =================

def safe_timestamp_to_datetime(ts):
    """安全轉換時間戳記"""
    try:
        return datetime.fromtimestamp(float(ts)/1000)
    except:
        return datetime.now()

def to_apy(daily_rate):
    """日利率轉年化"""
    try:
        return float(daily_rate) * 365 * 100
    except:
        return 0.0

@st.cache_resource
def init_exchange(api_key, api_secret):
    """初始化交易所並強制修補定義"""
    exchange = ccxt.bitfinex({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'nonce': lambda: int(time.time() * 1000000), 
    })
    
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"Market load failed: {e}")

    # 強制注入 USD 與 fUSD 定義
    if exchange.currencies is None: exchange.currencies = {}
    if not hasattr(exchange, 'currencies_by_id') or exchange.currencies_by_id is None:
        exchange.currencies_by_id = {}
        
    usd_def = {
        'id': 'USD', 'code': 'USD', 'uppercaseId': 'USD', 
        'name': 'US Dollar', 'active': True, 'precision': 2,
        'limits': {'amount': {'min': 0.0}, 'withdraw': {'min': 0.0}}
    }
    exchange.currencies['USD'] = usd_def
    exchange.currencies_by_id['USD'] = usd_def

    f_symbol = 'fUSD'
    if exchange.markets is None: exchange.markets = {}
    if exchange.markets_by_id is None: exchange.markets_by_id = {}
    
    market_def = {
        'id': f_symbol, 'symbol': f_symbol, 
        'base': 'USD', 'quote': 'USD', 'baseId': 'USD', 'quoteId': 'USD',
        'type': 'funding', 'spot': False, 'margin': False, 'swap': False, 'future': False,
        'active': True, 'precision': {'amount': 8, 'price': 8},
        'limits': {'amount': {'min': 150.0}, 'price': {'min': 0.0}}
    }
    exchange.markets[f_symbol] = market_def
    exchange.markets_by_id[f_symbol] = market_def
    
    return exchange

def fetch_data(exchange):
    """一次獲取所有需要的數據 (標準化版本)"""
    try:
        init_exchange(exchange.apiKey, exchange.secret)
        symbol = 'fUSD'

        # 1. 餘額
        balance = exchange.fetch_balance({'type': 'funding'})
        
        # 2. 帳本歷史 (收益)
        since_1y = exchange.milliseconds() - (365 * 24 * 60 * 60 * 1000)
        ledgers = exchange.fetch_ledger('USD', since=since_1y, limit=2500)
        
        # 3. 放貸中 (使用標準 CCXT 方法)
        # 這會自動處理 API 格式，回傳 list of dict
        try:
            active_credits = exchange.fetch_funding_credits(symbol)
        except Exception as e:
            # Fallback 如果標準方法失敗，可能是權限問題
            active_credits = []
            print(f"Fetch credits failed: {e}")

        # 4. 掛單中 (使用標準 CCXT 方法)
        try:
            active_offers = exchange.fetch_open_orders(symbol)
        except Exception as e:
            active_offers = []
            print(f"Fetch offers failed: {e}")

        # 5. 最近成交 (維持 Raw Call 以確保取得詳細歷史)
        raw_trades = exchange.private_post_auth_r_funding_trades_symbol_hist({'symbol': symbol, 'limit': 50})
        
        return balance, ledgers, active_credits, active_offers, raw_trades

    except Exception as e:
        st.error(f"API 連線異常: {str(e)}")
        return None, [], [], [], []

def process_earnings(ledgers):
    """處理收益數據"""
    data = []
    if not ledgers: return pd.DataFrame()

    keywords = ['funding', 'payment', 'interest']
    exclude_types = ['transaction', 'transfer', 'deposit', 'withdrawal']

    for entry in ledgers:
        amount = float(entry.get('amount', 0))
        if amount <= 0: continue
        
        typ = str(entry.get('type', '')).lower()
        desc = str(entry.get('description', '')).lower()
        info = str(entry.get('info', '')).lower()

        if any(x in typ for x in exclude_types): continue

        is_payout = 'payout' in typ
        has_keyword = any(k in info or k in desc or k in typ for k in keywords)

        if is_payout or has_keyword:
            dt = safe_timestamp_to_datetime(entry['timestamp'])
            data.append({
                'date': dt.date(),
                'datetime': dt,
                'amount': amount
            })
            
    return pd.DataFrame(data)

# ================= 3. 介面邏輯 =================

# 側邊欄
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
        st.rerun()

# 主畫面
st.title("💰 V14 資產監控")

if not st.session_state.api_key:
    st.warning("請輸入 API Key")
    st.stop()

exchange = init_exchange(st.session_state.api_key, st.session_state.api_secret)

with st.spinner("更新數據中..."):
    balance_data, raw_ledgers, loans, offers, trades = fetch_data(exchange)
    df_earnings = process_earnings(raw_ledgers)

# 指標計算
usd_bal = balance_data.get('USD', {'total': 0.0, 'free': 0.0}) if balance_data else {'total': 0.0, 'free': 0.0}
total_assets = float(usd_bal['total'])
utilization = ((total_assets - float(usd_bal['free'])) / total_assets * 100) if total_assets > 0 else 0.0

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

# 第二層：圖表
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

# 第三層：明細
st.markdown("---")
st.subheader("📋 資產明細")
t1, t2, t3, t4 = st.tabs(["放貸中 (Loans)", "掛單中 (Offers)", "最近成交 (Trades)", "每日收益 (Earnings)"])

with t1:
    if loans:
        data = []
        for l in loans:
            try:
                # 兼容處理：CCXT標準物件 vs Raw List
                if isinstance(l, dict):
                    # 標準 CCXT 格式
                    amount = float(l.get('amount', 0))
                    rate = float(l.get('rate', 0))
                    created = safe_timestamp_to_datetime(l.get('timestamp'))
                    # 嘗試從 info 獲取天數，預設 2 天
                    info = l.get('info', {})
                    days = int(info.get('period', 2)) if info else 2
                elif isinstance(l, list) and len(l) >= 12:
                    # 原始 Raw 格式 (Fallback)
                    created = safe_timestamp_to_datetime(l[3])
                    amount = abs(float(l[5]))
                    rate = float(l[11])
                    days = int(l[12])
                else:
                    continue

                due = created + timedelta(days=days)
                remain = max(0.0, (due - datetime.now()).total_seconds() / 86400)
                
                data.append({
                    "開單": created.strftime('%m-%d %H:%M'),
                    "金額": amount,
                    "APY": to_apy(rate),
                    "天數": days,
                    "剩餘": f"{remain:.1f} 天",
                    "到期": due.strftime('%m-%d %H:%M')
                })
            except Exception as e:
                pass # Skip problematic rows
        
        if data:
            st.dataframe(pd.DataFrame(data).sort_values("APY", ascending=False), use_container_width=True,
                        column_config={"APY": st.column_config.NumberColumn(format="%.2f%%"), "金額": st.column_config.NumberColumn(format="$%.2f")})
        else:
            st.info("無放貸")
    else:
        st.info("無放貸 (請確認 API 權限 'Margin Funding' 是否開啟)")

with t2:
    if offers:
        data = []
        for o in offers:
            try:
                # 兼容處理
                if isinstance(o, dict):
                    amount = float(o.get('amount', 0))
                    rate = float(o.get('price', 0)) # 在 Open Orders 裡，rate 通常在 price 欄位
                    created = safe_timestamp_to_datetime(o.get('timestamp'))
                    # 判斷 FRR
                    flags = o.get('info', {}).get('flags', 0)
                    is_frr = (flags & 1024) > 0 or rate == 0 # Bitfinex flag 1024 is FRR usually
                    
                    info = o.get('info', {})
                    days = int(info.get('period', 2)) if info else 2
                    
                elif isinstance(o, list) and len(o) >= 15:
                    rate = float(o[14])
                    is_frr = rate == 0
                    amount = float(o[4])
                    days = int(o[15])
                    created = safe_timestamp_to_datetime(o[2])
                else:
                    continue

                data.append({
                    "金額": amount,
                    "類型": "FRR" if is_frr else "Limit",
                    "APY": "FRR" if is_frr else f"{to_apy(rate):.2f}%",
                    "天數": days,
                    "建立": created.strftime('%m-%d %H:%M')
                })
            except Exception as e:
                pass

        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True,
                        column_config={"金額": st.column_config.NumberColumn(format="$%.2f")})
        else:
            st.info("無掛單")
    else:
        st.info("無掛單")

with t3:
    if trades and isinstance(trades, list):
        data = []
        sorted_trades = sorted(trades, key=lambda x: x[2] if len(x)>2 else 0, reverse=True)
        for t in sorted_trades[:20]: 
            if len(t) >= 7:
                amt = float(t[4])
                if amt > 0: # 濾掉借入，只看借出
                    data.append({
                        "成交": safe_timestamp_to_datetime(t[2]).strftime('%m-%d %H:%M'),
                        "金額": abs(amt),
                        "APY": to_apy(t[5]),
                        "天數": int(t[6])
                    })
        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True,
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

# ================= DEBUG 專區 =================
if debug_mode:
    st.markdown("---")
    st.error("🚧 DEBUG MODE 🚧")
    st.write("Current API Permissions Check:")
    if not loans: st.warning("Loans list is empty. Check 'Margin Funding' -> 'Read' permission.")
    st.subheader("Raw Loans Data")
    st.write(loans)
    st.subheader("Raw Offers Data")
    st.write(offers)
