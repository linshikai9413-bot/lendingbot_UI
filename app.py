# app.py - V17 收益修復版 (排除法邏輯 + 帳本診斷)
import streamlit as st
import ccxt
import pandas as pd
from datetime import datetime, timedelta
import traceback

# ================== 頁面設定 ==================
st.set_page_config(page_title="Bitfinex 資產監控 (V17)", page_icon="💰", layout="centered")

THEME_BG = "#0E1117"
TEXT_MAIN = "#E6E6E6"
TEXT_SUCCESS = "#00C896"
TEXT_WARNING = "#FFD700"

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

# ================== Secrets 自動載入邏輯 ==================
def load_api_secrets():
    """嘗試從 st.secrets 載入 API Key，並存入 session_state"""
    status_msg = ""
    
    # 1. 檢查 Session 是否已有值
    if st.session_state.get("api_key") and st.session_state.get("api_secret"):
        return "✅ 使用中 (Session)"

    # 2. 嘗試讀取 Secrets
    api_key = ""
    api_secret = ""
    
    # 支援 [bitfinex] 區塊 (建議)
    bf_block = st.secrets.get("bitfinex") if isinstance(st.secrets, dict) else None
    if bf_block:
        api_key = bf_block.get("api_key") or bf_block.get("key")
        api_secret = bf_block.get("api_secret") or bf_block.get("secret")
    
    # 支援平鋪寫法 (Fallback)
    if not api_key:
        api_key = st.secrets.get("bitfinex_api_key") or st.secrets.get("BITFINEX_API_KEY")
    if not api_secret:
        api_secret = st.secrets.get("bitfinex_api_secret") or st.secrets.get("BITFINEX_API_SECRET")

    # 3. 載入 Session
    if api_key and api_secret:
        st.session_state.api_key = api_key
        st.session_state.api_secret = api_secret
        return "✅ 已從 Secrets 自動載入"
    else:
        return "⚠️ 未偵測到 Secrets，請手動輸入"

# 執行載入
secrets_status = load_api_secrets()

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

# ================== 核心：收益計算 (排除法) ==================
def calculate_earnings_diagnose(ledgers):
    """
    計算收益，同時回傳診斷日誌，讓使用者知道每一筆是被算進去還是被排除
    """
    total_earn = 0.0
    last_30d_earn = 0.0
    first_date = datetime.now()
    has_data = False
    
    cutoff_30d = datetime.now() - timedelta(days=30)
    
    # 診斷日誌 (只存最近 20 筆非零交易)
    diagnosis_log = []

    if not ledgers:
        return 0.0, 0.0, 0.0, []

    # 關鍵字：如果描述包含這些，視為本金變動，予以排除
    # 注意：轉換成小寫比對
    EXCLUDE_KEYWORDS = [
        "transfer", "deposit", "withdrawal", "exchange", 
        "claim", "settlement", "trading fee", "affiliate"
    ]

    for r in ledgers:
        try:
            amt = float(r.get("amount", 0))
            if amt == 0: continue
            
            ts = r.get("timestamp") or r.get("mts")
            dt = safe_dt(ts)
            
            # 取得描述
            raw_desc = r.get("description", "") or r.get("info", {}).get("description", "") or "No Description"
            desc_lower = raw_desc.lower()
            
            # --- 判斷邏輯 ---
            is_income = False
            reason = ""

            if amt < 0:
                is_income = False
                reason = "支出 (負數)"
            else:
                # 預設為收入，除非撞到排除關鍵字
                is_income = True
                for kw in EXCLUDE_KEYWORDS:
                    if kw in desc_lower:
                        is_income = False
                        reason = f"排除關鍵字: {kw}"
                        break
                if is_income:
                    reason = "✅ 判定為收益"

            # 記錄診斷 (只記正數或有意義的交易)
            if amt > 0:
                diagnosis_log.append({
                    "時間": dt.strftime("%Y-%m-%d %H:%M"),
                    "金額": amt,
                    "描述": raw_desc,
                    "判定": "🟢 納入計算" if is_income else f"🔴 排除 ({reason})"
                })

            # 加總
            if is_income:
                total_earn += amt
                if dt >= cutoff_30d:
                    last_30d_earn += amt
                
                if not has_data or dt < first_date:
                    first_date = dt
                    has_data = True

        except Exception as e:
            continue
        
    days_diff = (datetime.now() - first_date).days + 1 if has_data else 1
    return total_earn, last_30d_earn, days_diff, diagnosis_log

# ================== 側邊欄 ==================
with st.sidebar:
    st.header("⚙️ 設定")
    st.caption(f"API 狀態: {secrets_status}")
    
    # 即便自動載入，也保留輸入框以便手動覆蓋
    k = st.text_input("API Key", value=st.session_state.get("api_key",""), type="password")
    s = st.text_input("API Secret", value=st.session_state.get("api_secret",""), type="password")
    
    # 如果使用者手動輸入，更新 session
    if k and k != st.session_state.get("api_key"): st.session_state.api_key = k
    if s and s != st.session_state.get("api_secret"): st.session_state.api_secret = s
    
    if st.button("🔄 刷新資料", type="primary"):
        st.cache_resource.clear()
        st.rerun()

# ================== 主程式 ==================
if not st.session_state.get("api_key"):
    st.warning("⚠️ 請確認 `.streamlit/secrets.toml` 設定正確，或在側邊欄手動輸入 API Key")
    st.stop()

with st.spinner("連線 Bitfinex 並分析帳本中..."):
    try:
        ex = init_exchange(st.session_state.api_key, st.session_state.api_secret)
        
        # 1. 餘額
        balances = ex.fetch_balance()
        
        # 2. 流水帳 (抓過去 90 天即可，太久會慢且容易混淆)
        since = ex.milliseconds() - 90 * 24 * 60 * 60 * 1000
        ledgers = ex.fetch_ledger("USD", since=since, limit=1000)
        
    except Exception as e:
        st.error(f"連線錯誤: {pretty_err(e)}")
        st.stop()

# --- 數據處理 ---

# 1. 資產 (Funding Wallet 優先)
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

# 2. 指標計算
utilization = ((total_assets - free_assets) / total_assets * 100) if total_assets > 0 else 0.0
total_income, last_30d_income, days_run, diag_log = calculate_earnings_diagnose(ledgers)

# 3. APY
apy = 0.0
if total_assets > 0 and days_run > 0:
    apy = (total_income / days_run / total_assets * 365 * 100)

# ================== UI 顯示 ==================
st.title("💰 Bitfinex 資產監控 V17")
st.caption("已採用「排除法」過濾本金，並自動載入 Secrets")

st.markdown("---")
c1, c2 = st.columns(2)
c1.metric("總資產 (Funding)", f"${total_assets:,.2f}")
c2.metric("資金利用率", f"{utilization:.1f}%")

st.markdown("---")
c3, c4, c5 = st.columns(3)
c3.metric("30天收益 (估)", f"${last_30d_income:,.2f}")
c4.metric("總收益 (90天內)", f"${total_income:,.2f}")
c5.metric("年化報酬率 APY", f"{apy:.2f}%")

# ================== 診斷區塊 (除錯關鍵) ==================
st.markdown("---")
st.subheader("🔍 收益計算診斷")
st.info("如果收益顯示為 0，請展開下方查看每一筆交易是如何被判定的。")

with st.expander("查看最近交易判定結果 (前 20 筆)", expanded=True):
    if diag_log:
        df_diag = pd.DataFrame(diag_log)
        # 讓判定欄位顏色不同
        def color_verdict(val):
            color = '#00C896' if '🟢' in val else '#FF4B4B'
            return f'color: {color}'
        
        st.dataframe(df_diag.style.applymap(color_verdict, subset=['判定']), use_container_width=True)
    else:
        st.write("過去 90 天內無大於 0 的資金變動紀錄。")
