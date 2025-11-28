import streamlit as st
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go

# ================= 1. 頁面設定與舒適風格 CSS =================
st.set_page_config(
    page_title="Bitfinex 收益監控",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 舒適配色變數 (Midnight Ocean Theme)
THEME_BG = "#0E1117"        # 深邃藍黑背景
THEME_CARD = "#1E232F"      # 柔和的卡片背景
THEME_TEXT = "#E0E0E0"      # 舒適的灰白文字
ACCENT_COLOR = "#4F8BF9"    # 寧靜藍 (主要按鈕/強調)
ACCENT_GREEN = "#00C896"    # 柔和綠 (收益)
ACCENT_YELLOW = "#FFD166"   # 柔和黃 (掛單)

st.markdown(f"""
    <style>
    /* 全局樣式優化 */
    .stApp {{
        background-color: {THEME_BG};
        color: {THEME_TEXT};
    }}
    
    /* 指標卡片 (Metric) 優化 */
    div[data-testid="stMetric"] {{
        background-color: {THEME_CARD};
        border: 1px solid #2B3240;
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }}
    
    div[data-testid="stMetric"] label {{
        font-size: 0.9rem;
        color: #94A3B8; /* 次要文字顏色 */
    }}
    
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {{
        color: {THEME_TEXT};
        font-weight: 600;
    }}

    /* 表格樣式優化 */
    div[data-testid="stDataFrame"] {{
        background-color: {THEME_CARD};
        padding: 10px;
        border-radius: 12px;
    }}

    /* 按鈕樣式 */
    div.stButton > button {{
        background-color: {ACCENT_COLOR};
        color: white;
        border-radius: 8px;
        border: none;
        padding: 0.5rem 1rem;
        transition: opacity 0.3s;
    }}
    div.stButton > button:hover {{
        opacity: 0.9;
        background-color: {ACCENT_COLOR};
        border: none;
        color: white;
    }}
    
    /* 移除頂部過多的空白 */
    .block-container {{
        padding-top: 2rem;
    }}
    </style>
    """, unsafe_allow_html=True)

# ================= 2. 工具函式 =================
@st.cache_resource
def init_exchange(api_key, api_secret):
    return ccxt.bitfinex({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'nonce': lambda: int(time.time() * 1000000), 
    })

def to_apy(daily_rate):
    """將日利率轉換為 APY (%)"""
    try: return float(daily_rate) * 365 * 100
    except: return 0.0

def fetch_data(exchange, currency):
    """
    獲取所有必要的數據
    """
    try:
        # 1. 餘額
        balance = exchange.fetch_balance({'type': 'funding'})
        
        # 2. 進行中的放貸 (使用 private method)
        # 注意: 這裡仍使用 private API，因為 CCXT 的統一介面在不同版本可能有異
        try: 
            active_credits = exchange.private_post_auth_r_funding_credits(params={'symbol': f'f{currency}'})
        except: 
            active_credits = []
        
        # 3. 掛單
        all_orders = exchange.fetch_open_orders()
        open_offers = [o for o in all_orders if o['symbol'] == f'f{currency}']
        
        # 4. 市場行情 (FRR)
        raw_ticker = exchange.public_get_ticker_symbol({'symbol': f'f{currency}'})
        ticker_data = {'frr': float(raw_ticker[0]), 'bid': float(raw_ticker[1])}
        
        # 5. 歷史帳本 (30天)
        since_time = exchange.milliseconds() - (30 * 24 * 60 * 60 * 1000)
        ledgers = exchange.fetch_ledger(currency, since=since_time, limit=1000) 
        
        return balance, active_credits, open_offers, ticker_data, ledgers, None
    except Exception as e:
        return None, None, None, None, None, str(e)

# ================= 3. 側邊欄與設定 =================
with st.sidebar:
    st.header("⚙️ 設定控制台")
    
    # --- API Key 管理 (Session State 優化) ---
    if "api_key" not in st.session_state:
        st.session_state.api_key = ""
    if "api_secret" not in st.session_state:
        st.session_state.api_secret = ""

    # 優先從 secrets 讀取
    if "bitfinex" in st.secrets:
        st.session_state.api_key = st.secrets["bitfinex"]["api_key"]
        st.session_state.api_secret = st.secrets["bitfinex"]["api_secret"]
        st.success("🔒 金鑰已安全載入")
    else:
        st.info("請輸入 API 金鑰 (不會儲存於伺服器)")
        st.session_state.api_key = st.text_input("API Key", value=st.session_state.api_key, type="password")
        st.session_state.api_secret = st.text_input("API Secret", value=st.session_state.api_secret, type="password")

    target_currency = st.selectbox("選擇監控幣種", ["USD", "USDT"], index=0)
    
    st.markdown("---")
    
    if st.button('🔄 更新數據', use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # 簡單的狀態顯示，不使用閃爍動畫
    st.markdown("---")
    st.caption(f"最後更新: {datetime.now().strftime('%H:%M:%S')}")

# ================= 4. 主程式邏輯 =================
st.title(f"🌊 Bitfinex 收益監控 | {target_currency}")

if not st.session_state.api_key or not st.session_state.api_secret:
    st.info("👈 請在左側側邊欄輸入 API Key 以開始使用。")
    st.stop()

try:
    exchange = init_exchange(st.session_state.api_key, st.session_state.api_secret)
except Exception as e:
    st.error(f"連線初始化失敗: {e}")
    st.stop()

# 獲取數據
with st.spinner('☁️ 正在同步帳戶數據...'):
    balance_data, credits_data, offers_data, ticker_data, ledger_data, err_msg = fetch_data(exchange, target_currency)

if err_msg:
    st.error(f"數據獲取異常: {err_msg}")
    st.stop()

# 處理數據
if balance_data:
    usd_bal = balance_data.get(target_currency, {'total': 0, 'free': 0, 'used': 0})
    total_assets = float(usd_bal['total'])
    free_assets = float(usd_bal['free'])
    used_assets = float(usd_bal['used'])
    
    # --- 計算放貸數據 ---
    weighted_rate = 0
    total_loaned = 0
    loans_list = []
    
    if credits_data:
        for loan in credits_data:
            try:
                # 這裡是一個潛在風險點，增加型別檢查
                if isinstance(loan, list) and len(loan) >= 13:
                    amt = abs(float(loan[5]))
                    rate = float(loan[11])
                    period = int(loan[12])
                    
                    total_loaned += amt
                    weighted_rate += (amt * rate)
                    
                    loans_list.append({
                        "amount": amt,
                        "apy_raw": to_apy(rate), # 存數值
                        "period": period,
                        "est_income": amt * rate
                    })
            except Exception as e:
                print(f"Parsing error: {e}") # 僅在後台打印，不影響前端
                pass
            
    avg_apy = (to_apy(weighted_rate / total_loaned)) if total_loaned > 0 else 0.0
    est_daily_income = weighted_rate
    
    # --- 計算歷史收益 ---
    earnings_df = pd.DataFrame()
    total_earnings_30d = 0
    
    if ledger_data:
        earnings_list = []
        valid_types = ['swap', 'interest', 'funding', 'payout', 'margin funding']
        invalid_types = ['deposit', 'transfer', 'trade', 'exchange'] # 過濾掉非收益項目
        
        for entry in ledger_data:
            try:
                amt = float(entry['amount'])
                etype = str(entry.get('type', '')).lower()
                
                # 簡單過濾邏輯：金額大於0 且 類型包含關鍵字
                if amt > 0 and any(k in etype for k in valid_types) and not any(k in etype for k in invalid_types):
                    # 轉換時間戳
                    date_obj = datetime.fromtimestamp(entry['timestamp']/1000).date()
                    earnings_list.append({'Date': date_obj, 'Amount': amt})
            except:
                continue
        
        if earnings_list:
            earnings_df = pd.DataFrame(earnings_list)
            total_earnings_30d = earnings_df['Amount'].sum()

    # ================= 5. 儀表板顯示 (舒適版) =================
    
    # 第一排：核心指標
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    
    kpi1.metric("總資產 (Total)", f"${total_assets:,.2f}", 
                help="帳戶內該幣種的總餘額")
    
    utilization_rate = (used_assets / total_assets * 100) if total_assets > 0 else 0
    kpi2.metric("資金利用率", f"{utilization_rate:.1f}%", 
                delta=f"閒置: ${free_assets:,.2f}", delta_color="off") # off 代表灰色，不顯示紅綠
    
    market_frr_apy = to_apy(ticker_data['frr'])
    # 若我們的利率高於市場 FRR，顯示綠色
    diff_apy = avg_apy - market_frr_apy
    kpi3.metric("平均年化 (APY)", f"{avg_apy:.2f}%", 
                delta=f"{diff_apy:+.2f}% vs FRR")
    
    kpi4.metric("30天累計收益", f"${total_earnings_30d:.2f}", 
                delta=f"預估日收: ${est_daily_income:.2f}")

    st.markdown("###") # 間距

    # 第二排：圖表區
    col_main, col_side = st.columns([0.65, 0.35], gap="large")
    
    with col_main:
        st.subheader("📊 每日收益趨勢")
        if not earnings_df.empty:
            # 整理數據：按日期加總
            chart_df = earnings_df.groupby('Date')['Amount'].sum().reset_index()
            # 補齊最近30天，確保圖表連續
            all_dates = pd.date_range(end=datetime.now().date(), periods=30, freq='D').date
            all_dates_df = pd.DataFrame({'Date': all_dates})
            chart_df = pd.merge(all_dates_df, chart_df, on='Date', how='left').fillna(0)
            
            # 使用更柔和的 Area Chart
            fig = px.area(chart_df, x='Date', y='Amount',
                          template="plotly_dark",
                          color_discrete_sequence=[ACCENT_GREEN])
            
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor='#333'),
                height=300
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("尚無足夠的收益數據來繪製圖表。")

    with col_side:
        st.subheader("🍰 資產狀態分佈")
        pie_data = pd.DataFrame([
            {'Type': '放貸中', 'Value': total_loaned, 'Color': ACCENT_COLOR},
            {'Type': '掛單中', 'Value': max(0.0, total_assets - free_assets - total_loaned), 'Color': ACCENT_YELLOW},
            {'Type': '閒置', 'Value': free_assets, 'Color': '#EF5350'}
        ]).query("Value > 0")
        
        if not pie_data.empty:
            fig_pie = go.Figure(data=[go.Pie(
                labels=pie_data['Type'], 
                values=pie_data['Value'],
                hole=.7, # 甜甜圈圖
                marker=dict(colors=pie_data['Color'])
            )])
            
            fig_pie.update_layout(
                showlegend=True,
                paper_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
                margin=dict(l=0, r=0, t=20, b=50),
                height=300
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.write("無資產數據")

    st.markdown("###") 

    # 第三排：詳細列表 (使用 Tabs 整理)
    st.subheader("📝 詳細明細")
    tab1, tab2 = st.tabs(["正在進行的放貸", "目前的掛單"])
    
    with tab1:
        if loans_list:
            df_loans = pd.DataFrame(loans_list)
            df_loans = df_loans.sort_values(by="apy_raw", ascending=False)
            
            st.dataframe(
                df_loans,
                use_container_width=True,
                column_order=("amount", "apy_raw", "period", "est_income"),
                column_config={
                    "amount": st.column_config.NumberColumn("本金", format="$%.2f"),
                    "apy_raw": st.column_config.ProgressColumn(
                        "年化利率 (APY)", 
                        format="%.2f%%", 
                        min_value=0, 
                        max_value=100, # 調整上限到 100 比較合理
                    ),
                    "period": st.column_config.NumberColumn("週期", format="%d 天"),
                    "est_income": st.column_config.NumberColumn("預估日收", format="$%.4f")
                },
                hide_index=True
            )
        else:
            st.caption("目前沒有正在進行的放貸。")
            
    with tab2:
        if offers_data:
            offers_clean = [{
                "amount": o['amount'], 
                "apy": to_apy(o['price']), 
                "period": o['info'].get('period', 2),
                "created": datetime.fromtimestamp(o['timestamp']/1000).strftime('%Y-%m-%d %H:%M')
            } for o in offers_data]
            
            st.dataframe(
                pd.DataFrame(offers_clean),
                use_container_width=True,
                column_config={
                    "amount": st.column_config.NumberColumn("掛單數量", format="$%.2f"),
                    "apy": st.column_config.NumberColumn("掛單年化", format="%.2f%%"),
                    "period": st.column_config.NumberColumn("天數", format="%d 天"),
                    "created": "建立時間"
                },
                hide_index=True
            )
        else:
            st.caption("目前沒有掛單。")

else:
    # 這裡處理如果 API 連線成功但沒有該幣種餘額的情況
    st.warning(f"無法獲取 {target_currency} 數據，請確認您的 API 權限或帳戶餘額。")