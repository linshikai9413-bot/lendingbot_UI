import streamlit as st
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta
import plotly.express as px

# ================= 1. 核心設定 =================
st.set_page_config(page_title="V14 資產監控", page_icon="💰", layout="wide")

THEME_BG, THEME_CARD, COLOR_BUY = "#0E1117", "#1C2128", "#00C896"
st.markdown(f"""
    <style>
    .stApp {{ background-color: {THEME_BG}; color: #E6E6E6; }}
    div[data-testid="stMetric"] {{ background-color: {THEME_CARD}; border-left: 4px solid {COLOR_BUY}; padding: 15px; border-radius: 8px; }}
    div[data-testid="stDataFrame"] {{ border: 1px solid #333; border-radius: 8px; }}
    </style>
""", unsafe_allow_html=True)

# ================= 2. 工具函式 =================
def ts_to_date(ts): return datetime.fromtimestamp(float(ts)/1000)
def to_apy(rate): return float(rate) * 365 * 100

@st.cache_resource
def init_exchange(api_key, api_secret):
    exchange = ccxt.bitfinex({
        'apiKey': api_key, 'secret': api_secret,
        'enableRateLimit': True,
        'nonce': lambda: int(time.time() * 1000000), 
    })
    
    try: exchange.load_markets()
    except: pass # 忽略載入錯誤，改用手動注入

    # 強制注入 USD 與 fUSD 定義 (與 bot.py 邏輯一致)
    f_sym = 'fUSD'
    if exchange.currencies is None: exchange.currencies = {}
    if not hasattr(exchange, 'currencies_by_id') or exchange.currencies_by_id is None: exchange.currencies_by_id = {}
    
    exchange.currencies['USD'] = {'id': 'USD', 'code': 'USD', 'uppercaseId': 'USD', 'precision': 2}
    exchange.currencies_by_id['USD'] = exchange.currencies['USD']
    
    if exchange.markets is None: exchange.markets = {}
    if exchange.markets_by_id is None: exchange.markets_by_id = {} 
    
    market_def = {'id': f_sym, 'symbol': f_sym, 'base': 'USD', 'quote': 'USD', 'type': 'funding', 'precision': {'amount': 8, 'price': 8}}
    exchange.markets[f_sym] = market_def
    exchange.markets_by_id[f_sym] = market_def
    
    return exchange

def fetch_data(exchange):
    """
    [修正] 使用與 bot.py 完全相同的抓取邏輯
    """
    try:
        # 1. 餘額
        bal = exchange.fetch_balance({'type': 'funding'})
        
        # 2. 帳本 (收益計算用)
        since = exchange.milliseconds() - (365 * 86400 * 1000)
        ledgers = exchange.fetch_ledger('USD', since=since, limit=2500)
        
        # 3. 放貸與掛單 (關鍵修正：使用 params 傳遞 symbol，而非 _symbol 方法)
        # 這是 bot.py 能成功抓到的關鍵寫法
        credits = exchange.private_post_auth_r_funding_credits(params={'symbol': 'fUSD'})
        offers = exchange.private_post_auth_r_funding_offers(params={'symbol': 'fUSD'})
        
        # 4. 最近成交
        trades = exchange.private_post_auth_r_funding_trades_symbol_hist({'symbol': 'fUSD', 'limit': 50})
        
        return bal, ledgers, credits, offers, trades
    except Exception as e:
        st.error(f"API Error: {e}")
        return None, [], [], [], []

def process_earnings(ledgers):
    """處理收益數據"""
    data = []
    if not ledgers: return pd.DataFrame()
    
    for e in ledgers:
        amt = float(e.get('amount', 0))
        if amt <= 0: continue
        
        typ = str(e.get('type', '')).lower()
        desc = str(e.get('description', '')).lower()
        
        if any(x in typ for x in ['trans', 'depo', 'with']): continue
        
        if 'payout' in typ or 'funding' in desc:
            data.append({'date': ts_to_date(e['timestamp']).date(), 'amount': amt})
            
    return pd.DataFrame(data)

# ================= 3. 主程式 =================
st.title("💰 V14 資產監控")

with st.sidebar:
    st.header("⚙️ 設定")
    if "api_key" not in st.session_state: st.session_state.api_key = ""
    if "api_secret" not in st.session_state: st.session_state.api_secret = ""
    
    if "bitfinex" in st.secrets:
        st.session_state.api_key = st.secrets["bitfinex"]["api_key"]
        st.session_state.api_secret = st.secrets["bitfinex"]["api_secret"]
        st.success("🔒 API Key Loaded")
    else:
        st.session_state.api_key = st.text_input("API Key", type="password")
        st.session_state.api_secret = st.text_input("API Secret", type="password")
        
    if st.button("🔄 刷新", type="primary"):
        st.cache_resource.clear()
        st.rerun()

if not st.session_state.api_key:
    st.warning("請輸入 API Key"); st.stop()

exchange = init_exchange(st.session_state.api_key, st.session_state.api_secret)

with st.spinner("載入數據..."):
    bal_data, raw_ledgers, loans, offers, trades = fetch_data(exchange)
    df_earn = process_earnings(raw_ledgers)

# --- 計算 ---
usd = bal_data.get('USD', {'total': 0, 'free': 0}) if bal_data else {'total': 0, 'free': 0}
total_asset = float(usd['total'])
utilization = ((total_asset - float(usd['free'])) / total_asset * 100) if total_asset > 0 else 0

total_inc = df_earn['amount'].sum() if not df_earn.empty else 0
d30 = pd.Timestamp.now().date() - timedelta(days=30)
inc_30d = df_earn[df_earn['date'] >= d30]['amount'].sum() if not df_earn.empty else 0

apy_hist = 0
if not df_earn.empty and total_asset > 0:
    days = (pd.Timestamp.now().date() - df_earn['date'].min()).days + 1
    apy_hist = (total_inc / days / total_asset) * 365 * 100

# --- 指標顯示 ---
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("總資產", f"${total_asset:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")
c3.metric("30天收益", f"${inc_30d:,.2f}")
c4.metric("歷史總收益", f"${total_inc:,.2f}")
c5.metric("全歷史 APY", f"{apy_hist:.2f}%")

st.markdown("---")

# --- 圖表 ---
st.subheader("📊 每日績效")
if not df_earn.empty:
    rng = st.radio("範圍", ["7天", "30天", "1年", "全部"], index=1, horizontal=True)
    end_d = pd.Timestamp.now().date()
    start_d = df_earn['date'].min()
    if rng == "7天": start_d = end_d - timedelta(days=7)
    elif rng == "30天": start_d = end_d - timedelta(days=30)
    elif rng == "1年": start_d = end_d - timedelta(days=365)
    
    # 補齊日期
    full_d = pd.DataFrame(pd.date_range(max(start_d, df_earn['date'].min()), end_d).date, columns=['date'])
    mask = (df_earn['date'] >= start_d) & (df_earn['date'] <= end_d)
    chart_data = df_earn.loc[mask].groupby('date')['amount'].sum().reset_index()
    chart_data = pd.merge(full_d, chart_data, on='date', how='left').fillna(0)
    
    # 計算當日 APY
    chart_data['apy'] = (chart_data['amount'] / total_asset * 36500) if total_asset > 0 else 0

    fig = px.bar(chart_data, x='date', y='amount', title=f"區間收益: ${chart_data['amount'].sum():.2f}", color_discrete_sequence=[COLOR_BUY])
    fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor='#333'), height=350)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("無收益資料")

# --- 明細 ---
st.markdown("---")
t1, t2, t3, t4 = st.tabs(["放貸中", "掛單中", "最近成交", "每日收益"])

with t1:
    if loans:
        d = []
        for l in loans:
            # 放寬檢查：只要是 list 且 symbol 是 fUSD 就顯示
            if isinstance(l, list) and len(l) > 10 and 'USD' in str(l[1]):
                try:
                    # 嘗試讀取，失敗則跳過
                    created = ts_to_date(l[3])
                    amt = abs(float(l[5]))
                    rate = float(l[11])
                    period = int(l[12])
                    due = created + timedelta(days=period)
                    d.append({
                        "開單日期": created.strftime('%m-%d %H:%M'),
                        "金額": amt,
                        "APY": to_apy(rate),
                        "天數": period,
                        "剩餘": f"{max(0, (due - datetime.now()).total_seconds()/86400):.1f} 天",
                        "到期": due.strftime('%m-%d %H:%M')
                    })
                except: pass
        if d: st.dataframe(pd.DataFrame(d).sort_values("APY", ascending=False), use_container_width=True, column_config={"APY": st.column_config.NumberColumn(format="%.2f%%"), "金額": st.column_config.NumberColumn(format="$%.2f")})
        else: st.info("無放貸資料 (API 回傳空或格式不符)")
    else: st.info("目前無放貸")

with t2:
    if offers:
        d = []
        for o in offers:
            if isinstance(o, list) and len(o) > 10 and 'USD' in str(o[1]):
                try:
                    created = ts_to_date(o[2])
                    amt = float(o[4])
                    rate = float(o[14])
                    period = int(o[15])
                    d.append({
                        "金額": amt,
                        "類型": "FRR" if rate==0 else "Limit",
                        "APY": "FRR" if rate==0 else f"{to_apy(rate):.2f}%",
                        "天數": period,
                        "建立": created.strftime('%m-%d %H:%M')
                    })
                except: pass
        if d: st.dataframe(pd.DataFrame(d), use_container_width=True, column_config={"金額": st.column_config.NumberColumn(format="$%.2f")})
        else: st.info("無掛單資料")
    else: st.info("無掛單")

with t3:
    if trades and isinstance(trades, list):
        d = []
        for t in sorted(trades, key=lambda x: x[2] if len(x)>2 else 0, reverse=True)[:20]:
            if isinstance(t, list) and len(t) >= 7:
                amt = float(t[4])
                # [關鍵修正] 嚴格過濾：只顯示正數金額 (借出)
                if amt > 0:
                    d.append({
                        "成交時間": ts_to_date(t[2]).strftime('%m-%d %H:%M'),
                        "金額": abs(amt),
                        "APY": to_apy(t[5]),
                        "天數": int(t[6])
                    })
        if d: st.dataframe(pd.DataFrame(d), use_container_width=True, column_config={"APY": st.column_config.NumberColumn(format="%.2f%%"), "金額": st.column_config.NumberColumn(format="$%.2f")})
        else: st.info("無最近借出成交")
    else: st.info("無成交紀錄")

with t4:
    if 'chart_data' in locals() and not chart_data.empty:
        df_show = chart_data.sort_values('date', ascending=False)[['date', 'amount', 'apy']]
        df_show.columns = ['日期', '收益 (USD)', '當日 APY']
        st.dataframe(df_show, use_container_width=True, column_config={"日期": st.column_config.DateColumn(format="YYYY-MM-DD"), "收益 (USD)": st.column_config.NumberColumn(format="$%.2f"), "當日 APY": st.column_config.NumberColumn(format="%.2f%%")})
    else: st.info("無數據")
