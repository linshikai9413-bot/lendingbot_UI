# app.py - V18 智能門檻修復版 (解決無描述問題 + Secrets 強力搜尋)
import streamlit as st
import ccxt
import pandas as pd
from datetime import datetime, timedelta
import traceback

# ================== 頁面設定 ==================
st.set_page_config(page_title="Bitfinex 資產監控 V18", page_icon="💰", layout="centered")

THEME_BG = "#0E1117"
TEXT_MAIN = "#E6E6E6"

st.markdown(f"""
    <style>
    .stApp {{ background-color: {THEME_BG}; color: {TEXT_MAIN}; }}
    div[data-testid="stMetricValue"] {{ font-size: 2rem !important; }}
    </style>
""", unsafe_allow_html=True)

# ================== 工具函式 ==================
def safe_dt(ts):
    try:
        if ts is None: return datetime.now()
        ts = int(ts)
        if ts > 1e12: return datetime.fromtimestamp(ts / 1000)
        return datetime.fromtimestamp(ts)
    except: return datetime.now()

def pretty_err(e):
    return ''.join(traceback.format_exception_only(type(e), e)).strip()

# ================== Secrets 強力載入邏輯 ==================
def load_api_secrets():
    """全面掃描 Secrets 尋找可能的 Key"""
    
    # 1. 如果 Session 已經有值，直接用
    if st.session_state.get("api_key") and st.session_state.get("api_secret"):
        return

    found_key = ""
    found_secret = ""

    # 2. 定義所有可能的命名規則 (優先級由高到低)
    # 格式: (字典鍵名, 子鍵名) 或 (單一層鍵名, None)
    candidates = [
        # 巢狀格式 [bitfinex]
        ("bitfinex", "api_key"), ("bitfinex", "key"), ("bitfinex", "apiKey"),
        # 平鋪格式 (帶前綴)
        ("bitfinex_api_key", None), ("BITFINEX_API_KEY", None),
        # 平鋪格式 (通用) -> 這是最常見的漏網之魚
        ("api_key", None), ("apikey", None), ("API_KEY", None),
        ("key", None)
    ]

    # 3. 開始掃描
    # 先找 Key
    for parent, child in candidates:
        if child: # 巢狀
            block = st.secrets.get(parent)
            if isinstance(block, dict):
                val = block.get(child) or block.get(st.secrets.get(child, "")) 
                if val: found_key = val; break
        else: # 平鋪
            val = st.secrets.get(parent)
            if val: found_key = val; break
            
    # 再找 Secret (邏輯同上，對應 Secret 的命名)
    secret_candidates = [
        ("bitfinex", "api_secret"), ("bitfinex", "secret"), ("bitfinex", "apiSecret"),
        ("bitfinex_api_secret", None), ("BITFINEX_API_SECRET", None),
        ("api_secret", None), ("apisecret", None), ("API_SECRET", None),
        ("secret", None)
    ]
    
    for parent, child in secret_candidates:
        if child:
            block = st.secrets.get(parent)
            if isinstance(block, dict):
                val = block.get(child)
                if val: found_secret = val; break
        else:
            val = st.secrets.get(parent)
            if val: found_secret = val; break

    # 4. 存入 Session
    if found_key and found_secret:
        st.session_state.api_key = found_key
        st.session_state.api_secret = found_secret
        # 標記載入成功
        st.session_state.secrets_loaded = True
    else:
        st.session_state.secrets_loaded = False

# 執行載入
load_api_secrets()

# ================== Exchange 初始化 ==================
@st.cache_resource
def init_exchange(api_key, api_secret):
    ex = ccxt.bitfinex({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

# ================== 核心：收益計算 (智能門檻法) ==================
def calculate_earnings_smart(ledgers, total_assets_ref):
    """
    使用「資產比例」來判斷收益。
    如果單筆入帳 > 總資產的 0.5%，視為本金轉入，予以排除。
    """
    total_earn = 0.0
    last_30d_earn = 0.0
    first_date = datetime.now()
    has_data = False
    
    cutoff_30d = datetime.now() - timedelta(days=30)
    diagnosis_log = []

    if not ledgers:
        return 0.0, 0.0, 0.0, []

    # 設定門檻：如果單筆金額超過總資產的 0.5% (相當於日息 0.5%，年化 180%)
    # 這幾乎不可能是正常放貸利息，一定是本金變動
    # 如果總資產為 0 (剛開始)，則設定一個保守值 (例如 10 USD)
    threshold = (total_assets_ref * 0.005) if total_assets_ref > 0 else 10.0

    for r in ledgers:
        try:
            amt = float(r.get("amount", 0))
            if amt == 0: continue
            
            ts = r.get("timestamp") or r.get("mts")
            dt = safe_dt(ts)
            raw_desc = r.get("description", "") or "No Description"
            
            # --- 判斷邏輯 ---
            is_income = True
            reason = "✅ 收益"

            if amt < 0:
                is_income = False
                reason = "支出"
            
            # 智能門檻過濾
            elif amt > threshold:
                is_income = False
                reason = f"🔴 排除: 金額過大 (>{threshold:.2f}) 視為本金"
            
            # 輔助：如果真的有 Description 包含 transfer，也排除
            elif "transfer" in raw_desc.lower() or "deposit" in raw_desc.lower():
                is_income = False
                reason = "🔴 排除: 關鍵字"

            # 記錄診斷
            if amt > 0:
                diagnosis_log.append({
                    "時間": dt.strftime("%m-%d %H:%M"),
                    "金額": amt,
                    "描述": raw_desc,
                    "判定": reason
                })

            if is_income:
                total_earn += amt
                if dt >= cutoff_30d:
                    last_30d_earn += amt
                
                if not has_data or dt < first_date:
                    first_date = dt
                    has_data = True

        except: continue
        
    days_diff = (datetime.now() - first_date).days + 1 if has_data else 1
    return total_earn, last_30d_earn, days_diff, diagnosis_log

# ================== 側邊欄 ==================
with st.sidebar:
    st.header("⚙️ 設定")
    
    # 狀態顯示
    if st.session_state.get("secrets_loaded"):
        st.success("✅ Secrets 已自動載入")
    else:
        st.warning("⚠️ 未偵測到 Secrets")
    
    # 手動覆蓋區
    k = st.text_input("API Key", value=st.session_state.get("api_key",""), type="password")
    s = st.text_input("API Secret", value=st.session_state.get("api_secret",""), type="password")
    
    if k: st.session_state.api_key = k
    if s: st.session_state.api_secret = s
    
    if st.button("🔄 刷新資料", type="primary"):
        st.cache_resource.clear()
        st.rerun()

    # Secrets 除錯工具 (幫助你確認 Key 到底叫什麼)
    with st.expander("Secrets 診斷 (看不到Key值)"):
        st.write("已讀取到的 Keys:", list(st.secrets.keys()) if hasattr(st.secrets, 'keys') else "None")

# ================== 主程式 ==================
if not st.session_state.get("api_key"):
    st.info("請在 .streamlit/secrets.toml 設定 API Key，或在左側輸入。")
    st.stop()

with st.spinner("連線 Bitfinex 並分析帳本中..."):
    try:
        ex = init_exchange(st.session_state.api_key, st.session_state.api_secret)
        balances = ex.fetch_balance()
        since = ex.milliseconds() - 90 * 24 * 60 * 60 * 1000
        ledgers = ex.fetch_ledger("USD", since=since, limit=1000)
    except Exception as e:
        st.error(f"連線錯誤: {pretty_err(e)}")
        st.stop()

# --- 1. 計算總資產 (Funding Wallet) ---
total_assets = 0.0
free_assets = 0.0
if "info" in balances and isinstance(balances["info"], list):
    for wallet in balances["info"]:
        if len(wallet) > 4 and wallet[0] == "funding" and wallet[1] == "USD":
            total_assets = float(wallet[2]) if wallet[2] else 0.0
            free_assets = float(wallet[4]) if wallet[4] else 0.0
            break
if total_assets == 0:
    usd = balances.get("USD", {})
    total_assets = float(usd.get("total", 0))

# --- 2. 計算收益 (傳入總資產做為門檻參考) ---
total_income, last_30d_income, days_run, diag_log = calculate_earnings_smart(ledgers, total_assets)

# --- 3. 計算指標 ---
utilization = ((total_assets - free_assets) / total_assets * 100) if total_assets > 0 else 0.0
apy = 0.0
if total_assets > 0 and days_run > 0:
    apy = (total_income / days_run / total_assets * 365 * 100)

# ================== UI 顯示 ==================
st.title("💰 Bitfinex 資產監控 V18")

st.markdown("---")
c1, c2 = st.columns(2)
c1.metric("總資產 (Funding)", f"${total_assets:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")

st.markdown("---")
c3, c4, c5 = st.columns(3)
c3.metric("30天收益 (估)", f"${last_30d_income:,.2f}")
c4.metric("總收益 (90天內)", f"${total_income:,.2f}")
c5.metric("年化報酬率 APY", f"{apy:.2f}%")

# ================== 診斷區塊 ==================
st.markdown("---")
st.subheader("🔍 智能過濾診斷")
st.caption(f"過濾門檻：單筆金額 > ${ (total_assets * 0.005):.2f} (資產的 0.5%) 即視為本金排除。")

with st.expander("查看交易判定結果", expanded=True):
    if diag_log:
        df_diag = pd.DataFrame(diag_log)
        def color_verdict(val):
            return f'color: {"#FF4B4B" if "排除" in val else "#00C896"}'
        st.dataframe(df_diag.style.applymap(color_verdict, subset=['判定']), use_container_width=True)
    else:
        st.write("無交易紀錄")
