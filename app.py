import streamlit as st
import gspread
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import time
from datetime import datetime, timedelta

# --- 0. 基礎設定 ---
PORTFOLIO_SHEET_TITLE = 'Streamlit TW Stock_Pei' 
st.set_page_config(page_title="台股戰情指揮中心 V14.2", layout="wide", page_icon="📈")

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

# --- 1. 核心數據處理 ---

def get_gsheet_client():
    credentials = st.secrets["gcp_service_account"]
    return gspread.service_account_from_dict(credentials)

@st.cache_data(ttl=3600)
def get_market_data_finmind():
    """修正版：解決重複 Index 導致的數據解析異常"""
    fm_url = "https://api.finmindtrade.com/api/v4/data"
    api_token = st.secrets.get("finmind_token", "")
    
    try:
        # 1. 獲取個股基本資訊
        info_res = requests.get(fm_url, params={"dataset": "TaiwanStockInfo"}).json()
        if 'data' not in info_res: return {}
        info_df = pd.DataFrame(info_res['data'])
        info_df = info_df[info_df['stock_id'].str.len() == 4]
        # 確保代碼唯一
        info_df = info_df.drop_duplicates(subset=['stock_id'])
        
        # 2. 獲取本益比/淨值比 (尋找最近有資料的日期)
        per_df = pd.DataFrame()
        for i in range(1, 10): 
            search_date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            per_params = {"dataset": "TaiwanStockPER", "start_date": search_date, "end_date": search_date, "token": api_token}
            per_res = requests.get(fm_url, params=per_params).json()
            if 'data' in per_res and len(per_res['data']) > 0:
                per_df = pd.DataFrame(per_res['data'])
                # 重要：同一天內若有重複，取最後一筆
                per_df = per_df.drop_duplicates(subset=['stock_id'], keep='last')
                break
        
        # 3. 合併與清理
        if per_df.empty:
            merged = info_df[['stock_id', 'stock_name', 'industry_category']].copy()
            merged['PE'] = 999.0; merged['PBR'] = 999.0; merged['dividend_yield'] = 0.0
        else:
            merged = pd.merge(info_df[['stock_id', 'stock_name', 'industry_category']], 
                              per_df[['stock_id', 'PE', 'PBR', 'dividend_yield']], 
                              on='stock_id', how='left')
        
        merged.columns = ['代碼', '名稱', '產業', 'PE', 'PB', '殖利率']
        merged['PE'] = pd.to_numeric(merged['PE'], errors='coerce').fillna(999.0)
        merged['PB'] = pd.to_numeric(merged['PB'], errors='coerce').fillna(999.0)
        
        # 再次確保最終代碼不重複
        merged = merged.drop_duplicates(subset=['代碼'])
        return merged.set_index('代碼').to_dict('index')
        
    except Exception as e:
        st.error(f"⚠️ 數據解析失敗：{e}")
        return {}

MARKET_MAP = get_market_data_finmind()
STOCK_OPTIONS = sorted([f"{k} {v['名稱']} ({v['產業']})" for k, v in MARKET_MAP.items()])

@st.cache_data(ttl=600)
def fetch_finmind_history(symbol):
    try:
        url = "https://api.finmindtrade.com/api/v4/data"
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        res = requests.get(url, params={"dataset": "TaiwanStockPrice", "data_id": symbol, "start_date": start_date}).json()
        if not res.get('data'): return None
        
        df = pd.DataFrame(res['data']).rename(columns={'date': 'Date', 'open': 'Open', 'max': 'High', 'min': 'Low', 'close': 'Close'})
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        
        # 技術指標
        df['SMA20'] = df['Close'].rolling(20).mean()
        df['Lower'] = df['SMA20'] - (df['Close'].rolling(20).std() * 2)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df['RSI'] = 100 - (100 / (1 + (gain/(loss+1e-9))))
        
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['Hist'] = df['MACD'] - df['Signal']
        return df
    except: return None

def get_strategy_suggestion(df):
    if df is None or df.empty or len(df) < 30: 
        return ("數據中...", "#9e9e9e", "<span>載入中...</span>")
    last = df.iloc[-1]
    if last['RSI'] < 30: return ("超賣區", "#d32f2f", "🔥 RSI低檔，不建議追殺")
    if last['Close'] < last['Lower'] * 1.01: return ("抄底中", "#2e7d32", "🚀 觸及布林下軌")
    if last['RSI'] > 70: return ("超買區", "#ef6c00", "⚠️ 漲幅過大")
    return ("整理中", "#757575", "☕ 市場盤整中")

# --- 2. 介面呈現 ---
with st.sidebar:
    st.title("🛡️ 數據中心")
    if 'menu' not in st.session_state: st.session_state.menu = "portfolio"
    if st.button("🚀 庫存監控", use_container_width=True): st.session_state.menu = "portfolio"
    if st.button("💰 低基期快篩", use_container_width=True): st.session_state.menu = "screening"
    if st.button("🔍 個股診斷", use_container_width=True): st.session_state.menu = "diagnosis"
    if st.button("📝 管理清單", use_container_width=True): st.session_state.menu = "management"

if 'df_portfolio' not in st.session_state:
    try:
        gc = get_gsheet_client()
        sh = gc.open(PORTFOLIO_SHEET_TITLE)
        pdf = pd.DataFrame(sh.sheet1.get_all_records())
        pdf['Symbol'] = pdf['Symbol'].astype(str).str.zfill(4)
        st.session_state.df_portfolio = pdf
    except:
        st.session_state.df_portfolio = pd.DataFrame(columns=['Symbol', 'Name', 'Cost', 'Shares', 'Note'])

# --- 各功能邏輯 ---

if st.session_state.menu == "portfolio":
    st.markdown('<div class="function-title">🚀 庫存個股監控</div>', unsafe_allow_html=True)
    portfolio = st.session_state.df_portfolio
    if not portfolio.empty:
        cols = st.columns(3)
        for i, (_, r) in enumerate(portfolio.iterrows()):
            h_df = fetch_finmind_history(r['Symbol'])
            if h_df is not None:
                cp = h_df['Close'].iloc[-1]
                p_pct = (cp - r['Cost']) / r['Cost'] * 100 if r['Cost'] > 0 else 0
                strat = get_strategy_suggestion(h_df)
                with cols[i % 3]:
                    st.markdown(f"""
                    <div class="stock-card">
                        <b>{r['Name']} ({r['Symbol']})</b>
                        <div style="margin:10px 0;"><span style="font-size:1.5em;font-weight:bold;">${cp:.2f}</span> 
                        <span class="{'profit-up' if p_pct>=0 else 'profit-down'}">{p_pct:.2f}%</span></div>
                        <div class="strategy-tag" style="background-color:{strat[1]};">{strat[0]}</div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"技術圖表 {r['Symbol']}", key=f"p_{r['Symbol']}"):
                        st.session_state.current_plot = (h_df, r['Name'])

elif st.session_state.menu == "screening":
    st.markdown('<div class="function-title">💰 低基期潛力標的快篩</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([2, 2, 1])
    pe_lim = c1.number_input("PE 本益比上限", value=15.0)
    pb_lim = c2.number_input("PB 淨值比上限", value=1.2)
    
    if c3.button("啟動掃描"):
        results = [{'代碼': k, **v} for k, v in MARKET_MAP.items() if 0 < v['PE'] <= pe_lim and 0 < v['PB'] <= pb_lim]
        st.session_state.scan_results = pd.DataFrame(results)

    if 'scan_results' in st.session_state:
        df_res = st.session_state.scan_results
        if not df_res.empty:
            st.success(f"符合條件標的：{len(df_res)} 筆")
            sc_cols = st.columns(3)
            for i, (idx, row) in enumerate(df_res.head(30).iterrows()):
                with sc_cols[i % 3]:
                    st.markdown(f"""
                    <div class="stock-card">
                        <b>{row['代碼']} {row['名稱']}</b><br>
                        <small>{row['產業']}</small>
                        <div style="margin:5px 0; color:#1a2a6c;">PE: {row['PE']:.1f} | PB: {row['PB']:.1f}</div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"診斷 {row['代碼']}", key=f"sc_{row['代碼']}"):
                        h_df = fetch_finmind_history(row['代碼'])
                        if h_df is not None:
                            st.session_state.current_plot = (h_df, row['名稱'])
                            st.rerun()

elif st.session_state.menu == "diagnosis":
    st.markdown('<div class="function-title">🔍 全市場技術分析診斷</div>', unsafe_allow_html=True)
    selection = st.selectbox("搜尋標的", options=["請選擇..."] + STOCK_OPTIONS)
    if st.button("執行診斷") and selection != "請選擇...":
        code = selection.split(" ")[0]
        h_df = fetch_finmind_history(code)
        if h_df is not None:
            st.session_state.current_plot = (h_df, selection.split(" ")[1])

elif st.session_state.menu == "management":
    st.markdown('<div class="function-title">📝 庫存清單管理</div>', unsafe_allow_html=True)
    edited_df = st.data_editor(st.session_state.df_portfolio, hide_index=True, use_container_width=True)
    if st.button("💾 儲存變更"):
        try:
            gc = get_gsheet_client()
            sh = gc.open(PORTFOLIO_SHEET_TITLE).sheet1
            sh.clear()
            sh.update('A1', [edited_df.columns.tolist()] + edited_df.values.tolist())
            st.session_state.df_portfolio = edited_df
            st.success("✅ 已同步至雲端")
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
