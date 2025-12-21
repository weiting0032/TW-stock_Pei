import streamlit as st
import gspread
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import time
import random
from datetime import datetime, timedelta

# --- 0. 基礎設定 ---
PORTFOLIO_SHEET_TITLE = 'Streamlit TW Stock_Pei' 
st.set_page_config(page_title="台股戰情指揮中心 V14.0 (FinMind 核心版)", layout="wide", page_icon="📈")

st.markdown("""
    <style>
    .stock-card { border: 1px solid #eee; padding: 18px; border-radius: 12px; background-color: white; box-shadow: 2px 2px 8px rgba(0,0,0,0.05); margin-bottom: 15px; }
    .metric-container { display: flex; justify-content: space-around; background-color: #ffffff; padding: 25px; border-radius: 15px; margin-bottom: 25px; border: 1px solid #e0e0e0; box-shadow: 0 4px 6px rgba(0,0,0,0.03); }
    .metric-item { text-align: center; border-right: 1px solid #eee; flex: 1; }
    .metric-item:last-child { border-right: none; }
    .metric-label { font-size: 0.95em; color: #666; margin-bottom: 8px; font-weight: 500; }
    .metric-value { font-size: 2em; font-weight: 800; color: #1a2a6c; }
    .profit-up { color: #eb093b; font-weight: bold; }
    .profit-down { color: #00a651; font-weight: bold; }
    .group-tag { background-color: #f0f2f6; color: #555; padding: 2px 8px; border-radius: 5px; font-size: 0.8em; }
    .function-title { background-color: #1a2a6c; color: white; padding: 10px 20px; border-radius: 5px; margin-bottom: 20px; font-weight: bold; }
    .strategy-tag { font-size: 0.85em; padding: 2px 8px; border-radius: 4px; color: white; font-weight: bold; margin-top: 5px; display: inline-block; }
    </style>
""", unsafe_allow_html=True)

# --- 1. 核心數據處理 (FinMind 核心) ---

def get_gsheet_client():
    credentials = st.secrets["gcp_service_account"]
    return gspread.service_account_from_dict(credentials)

@st.cache_data(ttl=3600)
def get_market_data_finmind():
    """從 FinMind 獲取全市場清單與財務指標 (PE/PB)"""
    fm_url = "https://api.finmindtrade.com/api/v4/data"
    try:
        # 1. 獲取基本資訊 (名稱、產業)
        info_res = requests.get(fm_url, params={"dataset": "TaiwanStockInfo"}, timeout=15)
        info_df = pd.DataFrame(info_res.json()['data'])
        info_df = info_df[info_df['stock_id'].str.len() == 4] # 過濾權證/認購證
        
        # 2. 獲取本益比/淨值比 (抓取最近一天的全市場數據)
        # 注意：不帶 data_id 會抓取當天所有股票
        per_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d') # 取前幾天確保有資料
        per_params = {"dataset": "TaiwanStockPER", "start_date": per_date}
        per_res = requests.get(fm_url, params=per_params, timeout=15)
        per_df = pd.DataFrame(per_res.json()['data'])
        
        # 取每一家公司最新的一筆資料
        per_df = per_df.sort_values('date').groupby('stock_id').last().reset_index()
        
        # 3. 合併資料
        merged = pd.merge(info_df[['stock_id', 'stock_name', 'industry_category']], 
                          per_df[['stock_id', 'PE', 'PBR', 'dividend_yield']], 
                          on='stock_id', how='left')
        
        # 4. 獲取最新收盤價 (作為參考現價)
        # 這裡為了效能，若不做個別抓取，現價會顯示為 0 或需從 PER 換算，PER 數據通常包含股價
        # 改名與結構對齊
        merged.columns = ['代碼', '名稱', '產業', 'PE', 'PB', '殖利率']
        merged['現價'] = 0.0 # 預設值，後續個股診斷會更新精確值
        merged['PE'] = pd.to_numeric(merged['PE'], errors='coerce').fillna(999.0)
        merged['PB'] = pd.to_numeric(merged['PB'], errors='coerce').fillna(999.0)
        
        return merged.set_index('代碼').to_dict('index')
    except Exception as e:
        st.error(f"FinMind 財務數據獲取失敗: {e}")
        return {}

MARKET_MAP = get_market_data_finmind()
STOCK_OPTIONS = [f"{k} {v['名稱']} ({v['產業']})" for k, v in MARKET_MAP.items()]

@st.cache_data(ttl=300)
def load_portfolio():
    try:
        gc = get_gsheet_client()
        sh = gc.open(PORTFOLIO_SHEET_TITLE)
        df = pd.DataFrame(sh.sheet1.get_all_records())
        df['Symbol'] = df['Symbol'].astype(str).str.zfill(4)
        return df
    except:
        return pd.DataFrame(columns=['Symbol', 'Name', 'Cost', 'Shares', 'Note'])

def get_strategy_suggestion(df):
    if df is None or df.empty or len(df) < 26: 
        return ("資料不足", "#9e9e9e", "<span>等待數據中...</span>", "")
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 指標條件
    is_panic = last['RSI'] < 25
    is_oversold = last['RSI'] < 35 and last['Close'] < last['Lower'] * 1.02
    macd_up = last['Hist'] > prev['Hist'] and last['Hist'] < 0
    
    if is_panic: return ("極度恐慌", "#d32f2f", "⚠️ 市場過度悲觀，分批布局", f"RSI: {last['RSI']:.1f}")
    if is_oversold and macd_up: return ("黃金買訊", "#2e7d32", "🔥 RSI低檔 + MACD 轉折", "技術面買訊")
    if last['RSI'] > 75: return ("高檔過熱", "#ef6c00", "⛔ 建議減碼，不宜追高", f"RSI: {last['RSI']:.1f}")
    if last['Close'] > last['SMA20'] and last['Hist'] > 0: return ("多頭續抱", "#1976d2", "📈 強勢動能持續", "波段持有")
    return ("觀望整理", "#757575", "☕ 趨勢不明，靜待轉折", f"RSI: {last['RSI']:.1f}")

@st.cache_data(ttl=600)
def fetch_finmind_history(symbol):
    try:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {"dataset": "TaiwanStockPrice", "data_id": symbol, "start_date": start_date, "end_date": end_date}
        res = requests.get(url, params=params).json()
        if not res['data']: return None
        df = pd.DataFrame(res['data'])
        df = df.rename(columns={'date': 'Date', 'open': 'Open', 'max': 'High', 'min': 'Low', 'close': 'Close'})
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        # 計算技術指標
        df['SMA20'] = df['Close'].rolling(20).mean()
        df['SMA60'] = df['Close'].rolling(60).mean()
        df['Lower'] = df['SMA20'] - (df['Close'].rolling(20).std() * 2)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df['RSI'] = 100 - (100 / (1 + gain/loss))
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['Hist'] = df['MACD'] - df['Signal']
        return df
    except: return None

# --- 2. 介面邏輯 (側邊導覽) ---
with st.sidebar:
    st.title("🛡️ FinMind 數據戰情室")
    if 'menu' not in st.session_state: st.session_state.menu = "portfolio"
    if st.button("🚀 庫存個股監控", use_container_width=True): st.session_state.menu = "portfolio"
    if st.button("💰 低基期快篩", use_container_width=True): st.session_state.menu = "screening"
    if st.button("🔍 免庫存診斷", use_container_width=True): st.session_state.menu = "diagnosis"
    if st.button("📝 庫存清單管理", use_container_width=True): st.session_state.menu = "management"

if 'df_portfolio' not in st.session_state:
    st.session_state.df_portfolio = load_portfolio()

# --- 各功能區塊 ---

if st.session_state.menu == "portfolio":
    st.markdown('<div class="function-title">功能：🚀 庫存動態監控</div>', unsafe_allow_html=True)
    portfolio = st.session_state.df_portfolio
    if not portfolio.empty:
        total_mv, total_cost = 0.0, 0.0
        details = []
        for _, r in portfolio.iterrows():
            h_df = fetch_finmind_history(r['Symbol'])
            curr_p = h_df['Close'].iloc[-1] if h_df is not None else 0
            m_info = MARKET_MAP.get(r['Symbol'], {'名稱': '未知', '產業': '未知', 'PE': 0, 'PB': 0})
            total_mv += curr_p * r['Shares']
            total_cost += r['Cost'] * r['Shares']
            strat = get_strategy_suggestion(h_df)
            details.append({'r': r, 'm': m_info, 'cp': curr_p, 'strat': strat, 'df': h_df})

        diff = total_mv - total_cost
        st.markdown(f"""
            <div class="metric-container">
                <div class="metric-item"><div class="metric-label">總資產市值</div><div class="metric-value">${total_mv:,.0f}</div></div>
                <div class="metric-item"><div class="metric-label">未實現損益</div>
                    <div class="metric-value {'profit-up' if diff>=0 else 'profit-down'}">{'+' if diff>=0 else ''}${diff:,.0f}</div>
                </div>
                <div class="metric-item"><div class="metric-label">成本</div><div class="metric-value">${total_cost:,.0f}</div></div>
            </div>
        """, unsafe_allow_html=True)

        cols = st.columns(3)
        for i, item in enumerate(details):
            r, m, cp, strat, h_df = item['r'], item['m'], item['cp'], item['strat'], item['df']
            p_pct = (cp - r['Cost']) / r['Cost'] * 100 if r['Cost'] > 0 else 0
            with cols[i % 3]:
                st.markdown(f"""
                <div class="stock-card">
                    <b>{r['Name']} ({r['Symbol']})</b> <span class="group-tag">{m['產業']}</span>
                    <div style="margin:10px 0;"><span style="font-size:1.6em;font-weight:bold;">${cp:.2f}</span>
                    <span class="{'profit-up' if p_pct>=0 else 'profit-down'}">{'+' if p_pct>=0 else ''}{p_pct:.2f}%</span></div>
                    <div style="font-size:0.85em; color:#666;">PE: {m['PE']} | PB: {m['PB']}</div>
                    <div class="strategy-tag" style="background-color:{strat[1]};">策略: {strat[0]}</div>
                </div>
                """, unsafe_allow_html=True)
                if st.button(f"技術圖表 {r['Symbol']}", key=f"p_{r['Symbol']}"):
                    st.session_state.current_plot = (h_df, r['Name'])

elif st.session_state.menu == "screening":
    st.markdown('<div class="function-title">功能：💰 低基期潛力標的快篩 (FinMind 財務版)</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([2, 2, 1])
    pe_lim = c1.number_input("PE 本益比上限", value=15.0)
    pb_lim = c2.number_input("PB 淨值比上限", value=1.2)
    
    if c3.button("啟動掃描"):
        with st.spinner('從 FinMind 財務資料庫掃描中...'):
            results = []
            for k, v in MARKET_MAP.items():
                if 0 < v['PE'] <= pe_lim and 0 < v['PB'] <= pb_lim:
                    results.append({'代碼': k, **v})
            st.session_state.scan_results = pd.DataFrame(results)

    if 'scan_results' in st.session_state:
        df_res = st.session_state.scan_results
        if not df_res.empty:
            st.success(f"符合條件標的：{len(df_res)} 筆")
            sc_cols = st.columns(3)
            for i, (idx, row) in enumerate(df_res.head(60).iterrows()):
                with sc_cols[i % 3]:
                    st.markdown(f"""
                    <div class="stock-card">
                        <b>{row['代碼']} {row['名稱']}</b><span class="group-tag">{row['產業']}</span>
                        <div style="margin:10px 0; color:#1a2a6c; font-weight:bold;">PE: {row['PE']} | PB: {row['PB']}</div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"執行技術診斷 {row['代碼']}", key=f"sc_{row['代碼']}"):
                        h_df = fetch_finmind_history(row['代碼'])
                        if h_df is not None:
                            st.session_state.current_plot = (h_df, row['名稱'])
                            st.rerun()

elif st.session_state.menu == "diagnosis":
    st.markdown('<div class="function-title">功能：🔍 全市場技術分析診斷</div>', unsafe_allow_html=True)
    selection = st.selectbox("搜尋標的", options=["請選擇..."] + STOCK_OPTIONS)
    if st.button("執行診斷") and selection != "請選擇...":
        code, name = selection.split(" ")[0], selection.split(" ")[1]
        df = fetch_finmind_history(code)
        if df is not None: st.session_state.current_plot = (df, name)

elif st.session_state.menu == "management":
    st.markdown('<div class="function-title">功能：📝 庫存清單管理</div>', unsafe_allow_html=True)
    edited_df = st.data_editor(st.session_state.df_portfolio, hide_index=True, use_container_width=True)
    if st.button("💾 儲存並同步至 Google Sheets"):
        try:
            gc = get_gsheet_client()
            sh = gc.open(PORTFOLIO_SHEET_TITLE).sheet1
            sh.clear()
            sh.update('A1', [edited_df.columns.tolist()] + edited_df.values.tolist())
            st.session_state.df_portfolio = edited_df
            st.success("✅ 同步完成！")
            st.rerun()
        except Exception as e: st.error(f"寫入失敗: {e}")

# --- 底部圖表 ---
if 'current_plot' in st.session_state:
    st.divider()
    p_df, p_name = st.session_state.current_plot
    st.markdown(f"### 📊 技術分析圖表：{p_name}")
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=p_df.index, open=p_df['Open'], high=p_df['High'], low=p_df['Low'], close=p_df['Close'], name='K線'), row=1, col=1)
    fig.add_trace(go.Scatter(x=p_df.index, y=p_df['SMA20'], line=dict(color='orange'), name='20MA'), row=1, col=1)
    fig.add_trace(go.Scatter(x=p_df.index, y=p_df['RSI'], line=dict(color='purple'), name='RSI'), row=2, col=1)
    fig.update_layout(height=600, xaxis_rangeslider_visible=False, template="plotly_white")
    st.plotly_chart(fig, use_container_width=True)
