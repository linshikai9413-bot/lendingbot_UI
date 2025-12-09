import streamlit as st
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta
import plotly.express as px

# ================= 1. 核心設定 =================
st.set_page_config(page_title="V14 資產監控 (Debug Mode)", page_icon="🐞", layout="wide")

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
    # 強制注入 (雖然在 Raw API 模式下不一定需要，但以防萬一)
    try: exchange.load_markets()
    except: pass
    
    f_sym = 'fUSD'
    if exchange.currencies is None: exchange.currencies = {}
    exchange.currencies['USD'] = {'id': 'USD', 'code': 'USD', 'uppercaseId': 'USD', 'precision': 2}
    
    if exchange.markets is None: exchange.markets = {}
    exchange.markets[f_sym] = {'id': f_sym, 'symbol': f_sym, 'base': 'USD', 'quote': 'USD', 'type': 'funding', 'precision': {'amount': 8, 'price': 8}}
    
    return exchange

def fetch_data_debug(exchange):
    """
    極限偵錯模式：嘗試所有可能的抓取方法
    """
    debug_results = {}
    valid_loans = []
    valid_offers = []
    
    # --- 測試 1: 標準 CCXT 方法 (fetch_funding_credits) ---
    try:
        res = exchange.fetch_funding_credits(symbol='fUSD')
        debug_results['1_fetch_funding_credits(fUSD)'] = f"Success: {len(res)} items"
        if res: valid_loans = res # 如果這個成功，優先使用
    except Exception as e:
        debug_results['1_fetch_funding_credits(fUSD)'] = f"Error: {str(e)}"

    # --- 測試 2: Raw API (無參數) ---
    try:
        res = exchange.private_post_auth_r_funding_credits()
        debug_results['2_private_credits()'] = f"Success: {len(res)} items"
    except Exception as e:
        debug_results['2_private_credits()'] = f"Error: {str(e)}"

    # --- 測試 3: Raw API (params={'symbol': 'fUSD'}) --- [Bot.py 用法]
    try:
        res = exchange.private_post_auth_r_funding_credits(params={'symbol': 'fUSD'})
        debug_results['3_private_credits(params=fUSD)'] = f"Success: {len(res)} items"
        # 如果這是 Raw 格式，我們需要手動轉換才能給 UI 用
        if res and isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
             # 暫存 Raw Data 供下方顯示
             valid_loans = res 
    except Exception as e:
        debug_results['3_private_credits(params=fUSD)'] = f"Error: {str(e)}"

    # --- 測試 4: Raw API (params={'symbol': 'USD'}) --- [嘗試 USD]
    try:
        res = exchange.private_post_auth_r_funding_credits(params={'symbol': 'USD'})
        debug_results['4_private_credits(params=USD)'] = f"Success: {len(res)} items"
    except Exception as e:
        debug_results['4_private_credits(params=USD)'] = f"Error: {str(e)}"

    # --- 測試 5: Raw API (_symbol 方法) ---
    try:
        res = exchange.private_post_auth_r_funding_credits_symbol({'symbol': 'fUSD'})
        debug_results['5_private_credits_symbol(fUSD)'] = f"Success: {len(res)} items"
    except Exception as e:
        debug_results['5_private_credits_symbol(fUSD)'] = f"Error: {str(e)}"

    # 同樣測試 Offers
    try:
        res = exchange.private_post_auth_r_funding_offers(params={'symbol': 'fUSD'})
        valid_offers = res
    except: pass

    # 獲取其他基礎數據
    try:
        bal = exchange.fetch_balance({'type': 'funding'})
        ledgers = exchange.fetch_ledger('USD', limit=1000)
        trades = exchange.private_post_auth_r_funding_trades_symbol_hist({'symbol': 'fUSD', 'limit': 50})
    except Exception as e:
        st.error(f"基礎數據錯誤: {e}")
        return None, [], [], [], [], debug_results

    return bal, ledgers, valid_loans, valid_offers, trades, debug_results

def process_earnings(ledgers):
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
st.title("🐞 V14 資產監控 (極限偵錯版)")

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

with st.spinner("偵錯中..."):
    bal_data, raw_ledgers, loans, offers, trades, debug_log = fetch_data_debug(exchange)
    df_earn = process_earnings(raw_ledgers)

# --- 顯示偵錯結果 ---
st.subheader("🔍 API 抓取測試結果")
st.json(debug_log)

st.markdown("---")

# --- 正常顯示區 (如果有的話) ---
usd = bal_data.get('USD', {'total': 0, 'free': 0}) if bal_data else {'total': 0, 'free': 0}
total_asset = float(usd['total'])
utilization = ((total_asset - float(usd['free'])) / total_asset * 100) if total_asset > 0 else 0
total_inc = df_earn['amount'].sum() if not df_earn.empty else 0

c1, c2, c3 = st.columns(3)
c1.metric("總資產", f"${total_asset:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")
c3.metric("歷史總收益", f"${total_inc:,.2f}")

st.markdown("---")
t1, t2 = st.tabs(["放貸中 (Loans)", "掛單中 (Orders)"])

with t1:
    if loans:
        st.write("Raw Loans Data:", loans) # 直接顯示原始資料
        d = []
        for l in loans:
            # 兼容 Raw List 格式 [ID, SYM, ..., AMT, ..., RATE, PERIOD]
            if isinstance(l, list) and len(l) > 10:
                try:
                    # 嘗試解析 Raw List
                    # 通常: 3=Created, 5=Amount, 11=Rate, 12=Period
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
                        "到期": due.strftime('%m-%d %H:%M')
                    })
                except: pass
            # 兼容 Dict 格式 (如果 fetch_funding_credits 成功)
            elif isinstance(l, dict):
                try:
                    d.append({
                        "開單日期": datetime.fromtimestamp(l['timestamp']/1000).strftime('%m-%d %H:%M'),
                        "金額": l['amount'],
                        "APY": to_apy(l['rate']),
                        "天數": l['period'],
                        "到期": "N/A"
                    })
                except: pass
        
        if d: st.dataframe(pd.DataFrame(d))
    else: st.info("無放貸資料")

with t2:
    if offers:
        st.write("Raw Offers Data:", offers)
    else: st.info("無掛單")
