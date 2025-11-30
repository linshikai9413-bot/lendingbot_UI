import streamlit as st
import ccxt
import pandas as pd
import time
import statistics
import math
from datetime import datetime, timedelta, timezone
import plotly.express as px
import plotly.graph_objects as go

# ================= 1. 頁面設定與 V14 風格 CSS =================
st.set_page_config(
    page_title="V14 資產監控看板",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 配色：專業深色金融風
THEME_BG = "#0E1117"
THEME_CARD = "#1C2128"
TEXT_MAIN = "#E6E6E6"
TEXT_SUB = "#A1A9B3"
COLOR_BUY = "#00C896"  # 綠 (收益)
COLOR_ACCENT = "#4F8BF9" # 藍 (重點)

st.markdown(f"""
    <style>
    .stApp {{ background-color: {THEME_BG}; color: {TEXT_MAIN}; }}
    
    /* Metric 優化 */
    div[data-testid="stMetric"] {{
        background-color: {THEME_CARD};
        padding: 15px;
        border-radius: 8px;
        border-left: 4px solid {COLOR_ACCENT};
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }}
    div[data-testid="stMetric"] label {{ font-size: 0.9rem; color: {TEXT_SUB}; }}
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {{ font-size: 1.6rem; color: {COLOR_BUY}; }}

    /* 表格優化 */
    div[data-testid="stDataFrame"] {{ border: 1px solid #30363D; border-radius: 8px; }}
    
    /* 偵錯區塊 */
    .debug-box {{
        border: 1px solid #FF5252;
        padding: 10px;
        border-radius: 5px;
        margin-top: 10px;
        background-color: #2b1d1d;
    }}
    </style>
""", unsafe_allow_html=True)

# ================= 2. 核心邏輯工具 =================

@st.cache_resource
def init_exchange(api_key, api_secret):
    return ccxt.bitfinex({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'nonce': lambda: int(time.time() * 1000000), 
    })

def to_apy(daily_rate): return float(daily_rate) * 365 * 100

def fetch_account_data(exchange, currency='USD'):
    """獲取帳戶餘額、收益、掛單、放貸中"""
    try:
        # 1. Balance
        balance = exchange.fetch_balance({'type': 'funding'})
        usd_bal = balance.get(currency, {'total': 0.0, 'free': 0.0, 'used': 0.0})
        
        # 2. Earnings History
        since_1y = exchange.milliseconds() - (365 * 24 * 60 * 60 * 1000)
        ledgers = exchange.fetch_ledger(currency, since=since_1y, limit=2500)
        
        # 3. Active Credits (放貸中)
        active_credits = exchange.private_post_auth_r_funding_credits(params={'symbol': f'f{currency}'})
        
        # 4. Active Offers (掛單中)
        active_offers = exchange.private_post_auth_r_funding_offers(params={'symbol': f'f{currency}'})
        
        return usd_bal, ledgers, active_credits, active_offers
    except Exception as e:
        st.error(f"數據獲取失敗: {e}")
        return None, [], [], []

def process_earnings(ledgers_data):
    """處理收益數據，排除雜訊"""
    if not ledgers_data:
        return pd.DataFrame()

    data = []
    for entry in ledgers_data:
        amount = float(entry.get('amount', 0))
        info_str = str(entry.get('info', '')).lower()
        desc = str(entry.get('description', '')).lower()
        typ = str(entry.get('type', '')).lower()
        
        # 過濾邏輯
        if amount <= 0: continue
        # 排除本金操作 (transfer, transaction, deposit, withdrawal)
        if 'transaction' in typ or 'transfer' in typ or 'deposit' in typ or 'withdrawal' in typ: continue

        is_payout_type = 'payout' in typ
        keywords = ['funding', 'payment', 'interest']
        has_keyword = any(k in info_str for k in keywords) or \
                      any(k in desc for k in keywords) or \
                      any(k in typ for k in keywords)

        if is_payout_type or has_keyword:
            data.append({
                'timestamp': entry['timestamp'],
                'date': datetime.fromtimestamp(entry['timestamp']/1000).date(),
                'datetime': datetime.fromtimestamp(entry['timestamp']/1000),
                'amount': amount,
                'description': entry.get('description', str(entry.get('info', 'Unknown'))),
                'type': entry.get('type', 'unknown')
            })
            
    return pd.DataFrame(data)

# ================= 3. 側邊欄：設定 =================
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
    debug_mode = st.checkbox("🐞 啟用偵錯模式 (Debug)", help="若收益顯示為 0，請勾選此項查看原始數據")
    
    if st.button("🔄 刷新數據", type="primary", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()

# ================= 4. 主程式 =================
st.title("💰 V14 資產監控看板")

if not st.session_state.api_key:
    st.warning("請先在左側輸入 API Key")
    st.stop()

exchange = init_exchange(st.session_state.api_key, st.session_state.api_secret)

# 獲取數據
with st.spinner("正在結算收益數據..."):
    account_bal, raw_ledgers, loans, offers = fetch_account_data(exchange, 'USD')
    df_earnings = process_earnings(raw_ledgers)

# --- 計算核心指標 ---
total_assets = float(account_bal['total']) if account_bal else 0.0
free_assets = float(account_bal['free']) if account_bal else 0.0
locked_assets = total_assets - free_assets
utilization_rate = (locked_assets / total_assets * 100) if total_assets > 0 else 0.0

# 收益與 APY 計算 (全歷史)
total_interest_income = 0.0
last_30d_income = 0.0
calculated_apy = 0.0

if not df_earnings.empty:
    total_interest_income = df_earnings['amount'].sum()
    
    # 30天累計
    cutoff_30d = pd.Timestamp.now().date() - timedelta(days=30)
    df_earnings['date'] = pd.to_datetime(df_earnings['date']).dt.date
    df_30d = df_earnings[df_earnings['date'] >= cutoff_30d]
    last_30d_income = df_30d['amount'].sum()
    
    # 全歷史 APY 計算 (含頭含尾 +1天)
    first_date = df_earnings['date'].min()
    today_date = pd.Timestamp.now().date()
    days_diff = (today_date - first_date).days + 1
    
    if days_diff < 1: days_diff = 1 
    
    if total_assets > 0:
        daily_avg_income_all_time = total_interest_income / days_diff
        calculated_apy = (daily_avg_income_all_time / total_assets) * 365 * 100

# ================= 5. 顯示層 (UI) =================

# --- 第一層：5 大核心指標 ---
col1, col2, col3, col4, col5 = st.columns(5)

col1.metric("總資產 (Total)", f"${total_assets:,.2f}")
col2.metric("資金利用率", f"{utilization_rate:.1f}%")
col3.metric("30天累計收益", f"${last_30d_income:,.2f}")
col4.metric("總利息收入 (歷史)", f"${total_interest_income:,.2f}")
col5.metric("全歷史 APY", f"{calculated_apy:.2f}%", help=f"算法：(總收益 / {days_diff if 'days_diff' in locals() else 1}天 / 總資產) * 365")

st.markdown("---")

# --- 第二層：收益量化圖表 ---
st.subheader("📊 每日利息收入")

if not df_earnings.empty:
    range_option = st.radio(
        "選擇時間範圍", 
        ["7天", "30天", "1年", "全部歷史"], 
        index=1, 
        horizontal=True,
        key="chart_range_radio"
    )
    
    end_date = pd.Timestamp.now().date()
    start_date = df_earnings['date'].min()

    if range_option == "7天":
        start_date = end_date - timedelta(days=7)
    elif range_option == "30天":
        start_date = end_date - timedelta(days=30)
    elif range_option == "1年":
        start_date = end_date - timedelta(days=365)
    
    if start_date > end_date: start_date = end_date

    full_date_idx = pd.date_range(start=start_date, end=end_date).date
    df_full_dates = pd.DataFrame(full_date_idx, columns=['date'])
    mask = (df_earnings['date'] >= start_date) & (df_earnings['date'] <= end_date)
    df_filtered = df_earnings.loc[mask]
    df_grouped = df_filtered.groupby('date')['amount'].sum().reset_index()
    df_chart = pd.merge(df_full_dates, df_grouped, on='date', how='left').fillna(0)

    if not df_chart.empty:
        total_in_range = df_chart['amount'].sum()
        fig = px.bar(
            df_chart, 
            x='date', 
            y='amount',
            title=f"區間收益 ({range_option}): ${total_in_range:.2f}",
            labels={'date': '日期', 'amount': '收益 (USD)'},
            color_discrete_sequence=[COLOR_BUY]
        )
        fig.update_layout(
            plot_bgcolor='rgba(0,0,0,0)', 
            paper_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=True, gridcolor='#333'),
            bargap=0.1
        )
        st.plotly_chart(fig, use_container_width=True, key=f"earnings_chart_{range_option}")
    else:
        st.info(f"{range_option} 區間內無收益數據")
else:
    st.info("尚無收益紀錄，或者 API 尚未回傳數據。")

# --- 第三層：資產詳細清單 (放貸與掛單) ---
st.markdown("---")
st.subheader("📋 資產詳細清單")
t1, t2 = st.tabs(["正在放貸 (Active Loans)", "掛單中 (Orders)"])

with t1:
    if loans:
        loan_data = []
        for l in loans:
            if isinstance(l, list) and len(l) >= 13:
                # l[3] 是 MTS_CREATE (建立時間毫秒)
                created_ts = float(l[3]) 
                period = int(l[12])
                
                # 正確邏輯：到期日 = 建立日期 + 天數
                created_dt = datetime.fromtimestamp(created_ts/1000)
                due_dt = created_dt + timedelta(days=period)
                
                # 計算剩餘天數
                now = datetime.now()
                remaining_delta = due_dt - now
                remaining_days_val = max(0.0, remaining_delta.total_seconds() / 86400)
                
                loan_data.append({
                    "開單日期": created_dt.strftime('%m-%d %H:%M'),
                    "金額 (USD)": abs(float(l[5])),
                    "APY": to_apy(float(l[11])),
                    "天數": period,
                    "剩餘天數": f"{remaining_days_val:.1f} 天",
                    "到期時間": due_dt.strftime('%m-%d %H:%M')
                })
        df_loans = pd.DataFrame(loan_data).sort_values("APY", ascending=False)
        st.dataframe(df_loans, use_container_width=True, 
                     column_config={"APY": st.column_config.NumberColumn(format="%.2f%%"), "金額 (USD)": st.column_config.NumberColumn(format="$%.2f")})
    else:
        st.info("目前沒有放貸中的資金")

with t2:
    if offers:
        offer_data = []
        for o in offers:
             if isinstance(o, list) and len(o) >= 16:
                rate_raw = float(o[14])
                is_frr = rate_raw == 0 # 0 代表 FRR
                apy_display = "FRR" if is_frr else f"{to_apy(rate_raw):.2f}%"
                
                offer_data.append({
                    "金額 (USD)": float(o[4]),
                    "類型": "FRR" if is_frr else "Limit",
                    "APY": apy_display,
                    "天數": int(o[15]),
                    "建立時間": datetime.fromtimestamp(int(o[2])/1000).strftime('%m-%d %H:%M')
                })
        df_offers = pd.DataFrame(offer_data)
        st.dataframe(df_offers, use_container_width=True,
                     column_config={"金額 (USD)": st.column_config.NumberColumn(format="$%.2f")})
    else:
        st.info("目前沒有掛單")

# --- 偵錯模式 ---
if debug_mode:
    st.markdown("---")
    st.subheader("🐞 原始數據偵錯")
    st.markdown("如果下方表格顯示錯誤，代表某些欄位在原始資料中不存在。")
    
    if raw_ledgers:
        raw_df = pd.DataFrame(raw_ledgers)
        if 'timestamp' in raw_df.columns:
            raw_df['datetime'] = pd.to_datetime(raw_df['timestamp'], unit='ms')
            
        st.write("▼ API 回傳的原始帳本數據 (前 20 筆):")
        
        possible_cols = ['datetime', 'amount', 'currency', 'type', 'description', 'balance', 'info']
        existing_cols = [c for c in possible_cols if c in raw_df.columns]
        
        if existing_cols:
            st.dataframe(raw_df[existing_cols].head(20), use_container_width=True)
        else:
            st.warning("找不到預期的欄位，顯示全部原始欄位：")
            st.dataframe(raw_df.head(20), use_container_width=True)
            
        st.write("▼ 經過程式篩選後的收益數據 (前 20 筆):")
        st.dataframe(df_earnings.head(20), use_container_width=True)
    else:
        st.warning("API 回傳的原始帳本列表為空 (Empty List)")
