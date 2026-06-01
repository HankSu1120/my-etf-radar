import streamlit as st
import yfinance as yf
import pandas as pd
import ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from supabase import create_client, Client
import datetime

# ==========================================
# 🔑 雲端基地連線設定 (請保持你原本的設定)
# ==========================================
SUPABASE_URL = "https://wgqwszdmvwfanrsghtcn.supabase.co" 
SUPABASE_KEY = "這裡請貼上你那一串超級長的anon_public密鑰"

@st.cache_data(ttl=3600)
def get_etf_data(ticker):
    try:
        etf = yf.Ticker(ticker)
        # 強制索取「還原除權息後的真實歷史收盤價 (auto_adjust=True)」
        df = etf.history(period="5y", auto_adjust=True)
        # 網頁基本設定
st.set_page_config(page_title="高股息 ETF 雲端決策面板", layout="wide")
st.title("📊 高股息 ETF 智慧決策面板 (複利校正完全體)")
st.subheader("基於自訂「除息後低檔撈底 ＆ 4天高檔鈍化抱緊」策略")

# ==========================================
# 🎯 檢查這裡：四大天王精選清單定義（必須放在 get_etf_data 之前！）
# ==========================================
FEATURED_LIST = {
    "00929.TW": "復華台灣科技優息 (月配)",
    "00919.TW": "群益台灣精選高息 (季配)",
    "0056.TW": "元大高股息 (季配)",
    "00878.TW": "國泰永續高股息 (季配)"
}
        # 新股防禦機制：如果 5y 抓下來是空的（例如 00929 在雲端被拒絕）
        if df.empty or len(df) < 22:
            df = etf.history(period="max", auto_adjust=True)
            
        if df.empty or len(df) < 22:
            return None
            
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df_cleaned = pd.DataFrame(index=df.index)
        
        # 💡 注意：因為開啟了 auto_adjust=True，Yahoo 會自動把 Open, High, Low, Close 轉換成還原價
        # 資料庫裡不會再有單獨的 'Adj Close' 欄位，這能保證與 Colab 的數據 100% 同步！
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col in df.columns:
                df_cleaned[col] = df[col].iloc[:, 0] if isinstance(df[col], pd.DataFrame) else df[col]
            else:
                df_cleaned[col] = 0.0
                
        # 另外單獨保留 Dividends 欄位用來計算除息日天數
        if 'Dividends' in df.columns:
            df_cleaned['Dividends'] = df['Dividends'].iloc[:, 0] if isinstance(df['Dividends'], pd.DataFrame) else df['Dividends']
        else:
            df_cleaned['Dividends'] = 0.0
                
        df_cleaned['MA22'] = df_cleaned['Close'].rolling(window=22).mean()
        stoch = ta.momentum.StochasticOscillator(
            high=df_cleaned['High'], low=df_cleaned['Low'], close=df_cleaned['Close'], 
            window=9, smooth_window=3
        )
        df_cleaned['K'] = stoch.stoch()
        df_cleaned['D'] = stoch.stoch_signal()
        return df_cleaned.dropna()
        
    except Exception as e:
        print(f"Error loading {ticker}: {e}")
        return None

# 雷達掃描邏輯
def scan_and_save_signals():
    radar_data = []
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    for ticker, name in FEATURED_LIST.items():
        try:
            response = supabase.table("etf_signals").select("*").eq("ticker", ticker).eq("update_date", today_str).execute()
            if response.data:
                data = response.data[0]
                radar_data.append({"ticker": ticker, "price": float(data['price']), "k": float(data['k_value']), "d": float(data['d_value']), "status": data['signal_status']})
                continue
        except: pass
            
        df_scan = get_etf_data(ticker)
        if df_scan is not None:
            latest = df_scan.iloc[-1]
            prev = df_scan.iloc[-2]
            div_days = 999
            for i in range(len(df_scan)-1, -1, -1):
                if df_scan['Dividends'].iloc[i] > 0:
                    div_days = len(df_scan) - 1 - i
                    break
                    
            status_text = "⚪ 觀望中"
            if div_days <= 20:
                low_z = False
                for j in range(max(0, len(df_scan)-5), len(df_scan)):
                    if df_scan['K'].iloc[j] < 18 and df_scan['D'].iloc[j] < 18:
                        low_z = True
                if low_z and latest['K'] > latest['D'] and prev['K'] <= prev['D']:
                    status_text = "🟢 ⚡ 買入訊號觸發"
                    
            if len(df_scan) >= 5:
                k_w = df_scan['K'].iloc[-5:-1]
                d_w = df_scan['D'].iloc[-5:-1]
                if (k_w > 82).all() and (d_w > 82).all():
                    if latest['K'] < 82: status_text = "🔴 🛑 賣出訊號觸發"
                elif (df_scan['K'].iloc[-4:] > 82).all(): status_text = "🔥 高檔狂飆抱緊"
            
            try:
                insert_data = {"ticker": ticker, "price": float(latest['Close']), "k_value": float(latest['K']), "d_value": float(latest['D']), "signal_status": status_text, "update_date": today_str}
                supabase.table("etf_signals").insert(insert_data).execute()
            except: pass
            radar_data.append({"ticker": ticker, "price": float(latest['Close']), "k": float(latest['K']), "d": float(latest['D']), "status": status_text})
    return radar_data

# 核心回測引擎 (與 Colab 100% 同步的真實複利模型)
def run_backtest_5y_corrected(df_all):
    # 💡 【核心修復二】：回測時只切出最近 5 年 (最大 1200 天)，新股就直接用全部天數
    df = df_all.tail(1200).copy()
    
    position = 0
    buy_price = 0
    trade_log = []
    
    start_balance = 1000000.0
    current_balance = start_balance
    
    earn_pcts = []
    loss_pcts = []
    
    for i in range(5, len(df)):
        current_date = df.index[i].strftime('%Y-%m-%d')
        div_days = 999
        for k in range(i, -1, -1):
            if df['Dividends'].iloc[k] > 0:
                div_days = i - k
                break
        
        if position == 0 and div_days <= 20:
            low_zone = False
            for j in range(i-4, i+1):
                if df['K'].iloc[j] < 18 and df['D'].iloc[j] < 18: low_zone = True
            if low_zone and df['K'].iloc[i] > df['D'].iloc[i] and df['K'].iloc[i-1] <= df['D'].iloc[i-1]:
                position = 1
                buy_price = df['Close'].iloc[i]
                trade_log.append(f"🟢 【買入】日期: {current_date} | 價格: ${buy_price:.2f}")
                
        elif position == 1:
            k_window_4d = df['K'].iloc[i-4:i]
            d_window_4d = df['D'].iloc[i-4:i]
            if (k_window_4d > 82).all() and (d_window_4d > 82).all() and df['K'].iloc[i] < 82:
                position = 0
                sell_price = df['Close'].iloc[i]
                ret = (sell_price - buy_price) / buy_price * 100
                if ret > 0: earn_pcts.append(ret)
                else: loss_pcts.append(ret)
                current_balance = current_balance * (1 + ret / 100)
                trade_log.append(f"🔴 【賣出】日期: {current_date} | 價格: ${sell_price:.2f} | 本趟獲利: {ret:+.2f}%")
                
    if position == 1:
        sell_price = df['Close'].iloc[-1]
        ret = (sell_price - buy_price) / buy_price * 100
        if ret > 0: earn_pcts.append(ret)
        else: loss_pcts.append(ret)
        current_balance = current_balance * (1 + ret / 100)
        trade_log.append(f"🔒 【未平倉結算】價格: ${sell_price:.2f} | 帳面效益: {ret:+.2f}%")
        
    total_trades = len(earn_pcts) + len(loss_pcts)
    strategy_return = ((current_balance - start_balance) / start_balance) * 100
    
    if total_trades > 0:
        win_rate = (len(earn_pcts) / total_trades) * 100
        avg_earn = sum(earn_pcts) / len(earn_pcts) if earn_pcts else 0.0
        avg_loss = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0.0
    else:
        win_rate, avg_earn, avg_loss = 0.0, 0.0, 0.0
        
    return {
        "total_return": strategy_return,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "avg_earn": avg_earn,
        "avg_loss": avg_loss,
        "logs": trade_log,
        "actual_days": len(df_all)
    }

# ==========================================
# 畫面佈局
# ==========================================
current_radar = scan_and_save_signals()

st.markdown("### 📋 今日高股息戰情總覽表")
summary_df = pd.DataFrame(current_radar)
if not summary_df.empty:
    summary_df.columns = ["ETF代號", "今日收盤價", "今日 K 值", "今日 D 值", "策略操作提示"]
    st.dataframe(summary_df.set_index("ETF代號"), use_container_width=True)
st.divider()

st.markdown("### 📡 全自動雲端策略雷達 (Supabase 已同步)")
with st.expander("🔔 展開查看今日所有高股息操作建議", expanded=True):
    buy_column, sell_column, hold_column = st.columns(3)
    with buy_column:
        st.markdown("#### 📥 🟢 買入訊號觸發")
        buys = [item for item in current_radar if "買入" in item['status']]
        if buys:
            for item in buys: st.success(f"⚡ **{item['ticker']}** ({item['price']:.2f}元)\n\n*符合除息低檔黃金交叉*")
        else: st.caption("🟢 今日暫無股票觸發買入點")
    with sell_column:
        st.markdown("#### 📤 🔴 賣出訊號觸發")
        sells = [item for item in current_radar if "賣出" in item['status']]
        if sells:
            for item in sells: st.error(f"🛑 **{item['ticker']}** ({item['price']:.2f}元)\n\n*符合4天高檔鈍化結束*")
        else: st.caption("🔴 今日暫無股票觸發賣出點")
    with hold_column:
        st.markdown("#### 🔥 多頭波段請抱緊")
        holds = [item for item in current_radar if "抱緊" in item['status']]
        if holds:
            for item in holds: st.warning(f"🚀 **{item['ticker']}** ({item['price']:.2f}元)\n\n*正處於高檔狂飆鈍化狀態*")
        else: st.caption("⚪ 今日暫無股票處於飆速鈍化區")

st.sidebar.header("🔍 模式與個股搜尋")
mode = st.sidebar.radio("請選擇操作模式", ["精選個股主頁 (按鈕切換)", "自訂搜尋個股分析"])

def render_etf_dashboard(ticker, display_name):
    df = get_etf_data(ticker)
    if df is None:
        st.error(f"❌ 無法取得 {ticker} 的歷史數據。")
        return
        
    latest_day = df.iloc[-1]
    prev_day = df.iloc[-2]
    
    st.markdown(f"## 🏢 {ticker} - {display_name}")
    
    b1, b2 = st.columns([0.3, 0.7])
    with b1:
        run_btn = st.button(f"🏃‍♂️ 一鍵模擬回測 {ticker} 五年績效", key=f"btn_{ticker}")
    
    if run_btn:
        with st.spinner("🚀 複利量化回測引擎運行中..."):
            res = run_backtest_5y_corrected(df)
            
            # 💡 【核心修復三】：只有當「實際天數真的不滿5年（小於1200天）」才噴出警告
            if res['actual_days'] < 1200:
                st.warning(f"⚠️ **【上市未滿五年提示】**：{ticker} 在台股上市至今僅有 `{res['actual_days']}` 個交易日，以下數據為其自上市日至今的實際模擬結果。")
                
            st.markdown("### 📊 策略回測核心報告 (5年完全體)")
            kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
            kpi_col4, kpi_col5 = st.columns(2)
            
            with kpi_col1: st.metric(label="💰 策略總報酬率", value=f"{res['total_return']:+.2f}%")
            with kpi_col2: st.metric(label="📊 總交易次數", value=f"{res['total_trades']} 次")
            with kpi_col3: st.metric(label="🎯 策略總勝率", value=f"{res['win_rate']:.1f}%")
            with kpi_col4: st.success(f"🟢 平均每次賺取：{res['avg_earn']:+.2f}%")
            with kpi_col5: st.error(f"🔴 平均每次虧損：{res['avg_loss']:.2f}%")
                
            with st.expander("📋 檢視詳細歷史進出場歷史明細"):
                for log in res['logs']: st.write(log)
        st.divider()

    m1, m2 = st.columns(2)
    with m1: st.metric(label="今日收盤價", value=f"${latest_day['Close']:.2f}", delta=f"{(latest_day['Close'] - prev_day['Close']):.2f}")
    with m2: st.markdown(f"**📍 今日 KD 狀態:** `K = {latest_day['K']:.1f}` | `D = {latest_day['D']:.1f}`")
    
    df_plot = df.tail(120)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.05)
    fig.add_trace(go.Candlestick(x=df_plot.index, open=df_plot['Open'], high=df_plot['High'], low=df_plot['Low'], close=df_plot['Close'], name="K線"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MA22'], line=dict(color='orange', width=1.5), name="22日均線"), row=1, col=1)
    for date, row in df_plot.iterrows():
        if row['Dividends'] > 0: fig.add_vline(x=date, line_width=1.5, line_dash="dash", line_color="green", row=1, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['K'], line=dict(color='blue', width=1.5), name="K值"), row=2, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['D'], line=dict(color='fuchsia', width=1.5), name="D值"), row=2, col=1)
    fig.add_hline(y=82, line_width=1, line_dash="dot", line_color="red", row=2, col=1)
    fig.add_hline(y=18, line_width=1, line_dash="dot", line_color="green", row=2, col=1)
    fig.update_layout(height=480, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

# 底部渲染
if mode == "精選個股主頁 (按鈕切換)":
    st.info("💡 **【實戰操盤錦囊】** 下方技術線圖中，**開頭綠色垂直虛線**代表【除息日】。下方 KD 圖中，**紅色點虛線**為 82 出場防守線，**綠色點虛線**為 18 撈底警戒線。")
    tab_titles = [f"📈 {ticker}" for ticker in FEATURED_LIST.keys()]
    tabs = st.tabs(tab_titles)
    for i, (ticker, name) in enumerate(FEATURED_LIST.items()):
        with tabs[i]: render_etf_dashboard(ticker, name)
else:
    search_input = st.sidebar.text_input("輸入台股代碼 (例如: 00940)", value="00940")
    search_input_full = search_input.strip() if search_input.endswith(".TW") else f"{search_input.strip()}.TW"
    st.info("💡 **【實戰操盤錦囊】** 下方技術線圖中，**開頭綠色垂直虛線**代表【除息日】。下方 KD 圖中，**紅色點虛線**為 82 出場防守線 Gord，**綠色點虛線**為 18 撈底警戒線。")
    render_etf_dashboard(search_input_full, "自訂搜尋個股分析")
