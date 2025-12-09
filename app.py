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

def fetch_account_data(exchange, currency='USD'):
    """獲取帳戶餘額、掛單、放貸中"""
    try:
        # 1. Balance
        balance = exchange.fetch_balance({'type': 'funding'})
        usd_bal = balance.get(currency, {'total': 0.0, 'free': 0.0, 'used': 0.0})
        
        # 2. Earnings History (抓取過去 1 年，增加 limit 防止漏單)
        # Bitfinex API limit 預設較小，我們設大一點，並抓取較長的時間
        since_1y = exchange.milliseconds() - (365 * 24 * 60 * 60 * 1000)
        
        # 嘗試抓取更多筆數以確保計算準確
        ledgers = exchange.fetch_ledger(currency, since=since_1y, limit=2500)
        
        return usd_bal, ledgers
    except Exception as e:
        st.error(f"數據獲取失敗: {e}")
        return None, []

def process_earnings(ledgers_data):
    """處理收益數據，排除雜訊"""
    if not ledgers_data:
        return pd.DataFrame()

    data = []
    for entry in ledgers_data:
        # 嚴格篩選收益：
        # 1. 金額必須大於 0
        # 2. 類別或描述必須包含 'funding' 或 'payment' (Bitfinex 通常是 Margin Funding Payment)
        # 3. 排除 transfer (轉帳)
        amount = float(entry['amount'])
        desc = str(entry.get('description', '')).lower()
        typ = str(entry.get('type', '')).lower()
        
        is_funding_income = (
            amount > 0 and 
            ('funding' in typ or 'payment' in typ or 'funding' in desc) and
            ('transfer' not in typ)
        )

        if is_funding_income:
            data.append({
                'timestamp': entry['timestamp'],
                'date': datetime.fromtimestamp(entry['timestamp']/1000).date(),
                'datetime': datetime.fromtimestamp(entry['timestamp']/1000),
                'amount': amount,
                'description': entry.get('description', entry.get('type', 'Unknown'))
            })
            
    return pd.DataFrame(data)

# ================= 3. 側邊欄：設定 =================
with st.sidebar:
    st.header("⚙️ 設定")
    
    if "api_key" not in st.session_state:
        st.session_state.api_key = ""
        st.session_state.api_secret = ""

    # 優先讀取 secrets，否則手動輸入
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
    account_bal, raw_ledgers = fetch_account_data(exchange, 'USD')
    df_earnings = process_earnings(raw_ledgers)

# --- 計算核心指標 ---
total_assets = float(account_bal['total']) if account_bal else 0.0
free_assets = float(account_bal['free']) if account_bal else 0.0
locked_assets = total_assets - free_assets
utilization_rate = (locked_assets / total_assets * 100) if total_assets > 0 else 0.0

# 收益計算
total_interest_income = 0.0
last_30d_income = 0.0
calculated_apy = 0.0

if not df_earnings.empty:
    # 總利息收入
    total_interest_income = df_earnings['amount'].sum()
    
    # 30天累計收益
    cutoff_30d = pd.Timestamp.now().date() - timedelta(days=30)
    # 確保 date 欄位是 datetime.date 類型
    df_earnings['date'] = pd.to_datetime(df_earnings['date']).dt.date
    
    df_30d = df_earnings[df_earnings['date'] >= cutoff_30d]
    last_30d_income = df_30d['amount'].sum()
    
    # 真實 APY 反推： (30天總收益 / 30天 / 總本金) * 365 * 100
    if total_assets > 0:
        daily_avg_income = last_30d_income / 30
        calculated_apy = (daily_avg_income / total_assets) * 365 * 100

# ================= 5. 顯示層 (UI) =================

# --- 第一層：5 大核心指標 ---
col1, col2, col3, col4, col5 = st.columns(5)

col1.metric("總資產 (Total)", f"${total_assets:,.2f}")
col2.metric("資金利用率", f"{utilization_rate:.1f}%")
col3.metric("30天累計收益", f"${last_30d_income:,.2f}")
col4.metric("總利息收入 (歷史)", f"${total_interest_income:,.2f}")
col5.metric("目前績效 APY", f"{calculated_apy:.2f}%", help="公式：(近30日均收 / 總資產) * 365")

st.markdown("---")

# --- 第二層：收益量化圖表 ---
st.subheader("📊 每日利息收入")

if not df_earnings.empty:
    # 日期範圍選擇器
    range_option = st.pills("選擇時間範圍", ["7天", "30天", "1年", "全部歷史"], default="30天")
    
    # 根據選擇過濾數據
    end_date = pd.Timestamp.now().date()
    if range_option == "7天":
        start_date = end_date - timedelta(days=7)
    elif range_option == "30天":
        start_date = end_date - timedelta(days=30)
    elif range_option == "1年":
        start_date = end_date - timedelta(days=365)
    else:
        start_date = df_earnings['date'].min()

    # 過濾並分組
    mask = (df_earnings['date'] >= start_date) & (df_earnings['date'] <= end_date)
    df_chart = df_earnings.loc[mask].groupby('date')['amount'].sum().reset_index()
    
    # 繪圖
    if not df_chart.empty:
        fig = px.bar(
            df_chart, 
            x='date', 
            y='amount',
            title=f"區間收益 ({range_option}): ${df_chart['amount'].sum():.2f}",
            labels={'date': '日期', 'amount': '收益 (USD)'},
            color_discrete_sequence=[COLOR_BUY]
        )
        fig.update_layout(
            plot_bgcolor='rgba(0,0,0,0)', 
            paper_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=True, gridcolor='#333')
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("該區間無收益數據")
else:
    st.info("尚無收益紀錄，或者 API 尚未回傳數據。")

# --- 第三層：偵錯模式 (排除 0.00 問題) ---
if debug_mode:
    st.markdown("---")
    st.subheader("🐞 原始數據偵錯")
    st.markdown("""
    **如何排查問題：**
    1. 如果下方表格是空的，代表 `fetch_ledger` 沒有抓到資料 (可能時間範圍太短)。
    2. 如果有資料但 `amount` 都是負數或 0，代表篩選邏輯過濾掉了真正的收益。
    3. 請查看 `description` 或 `type` 欄位，確認利息收入的關鍵字是什麼。
    """)
    
    # 顯示原始回傳的前 20 筆 (未過濾)
    if raw_ledgers:
        raw_df = pd.DataFrame(raw_ledgers)
        # 簡單處理一下時間方便閱讀
        if 'timestamp' in raw_df.columns:
            raw_df['datetime'] = pd.to_datetime(raw_df['timestamp'], unit='ms')
            
        st.write("▼ API 回傳的原始帳本數據 (前 20 筆):")
        st.dataframe(raw_df.head(20), use_container_width=True)
        
        st.write("▼ 經過程式篩選後的收益數據 (前 20 筆):")
        st.dataframe(df_earnings.head(20), use_container_width=True)
    else:
        st.warning("API 回傳的原始帳本列表為空 (Empty List)")
