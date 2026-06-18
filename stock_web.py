import json
import os
import io
import time
import warnings
import urllib3
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta

warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.json")
PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "portfolio.json")
ALERTS_FILE    = os.path.join(os.path.dirname(__file__), "alerts.json")

PEERS = {
    "AAPL": ["MSFT", "GOOGL", "META", "AMZN"],
    "MSFT": ["AAPL", "GOOGL", "AMZN", "META"],
    "GOOGL": ["META", "MSFT", "AAPL", "AMZN"],
    "NVDA": ["AMD", "INTC", "QCOM", "TSM"],
    "TSLA": ["GM", "F", "RIVN", "NIO"],
    "AMZN": ["MSFT", "GOOGL", "BABA", "JD"],
    "META": ["GOOGL", "SNAP", "PINS", "TWTR"],
    "AMD":  ["NVDA", "INTC", "QCOM", "TSM"],
}

INDICES = {
    "道琼斯": "^DJI",
    "纳斯达克": "^IXIC",
    "标普500": "^GSPC",
    "恐慌指数": "^VIX",
}

# ── 页面配置 ──────────────────────────────────
st.set_page_config(page_title="美股看板", page_icon="📈", layout="wide")
st.markdown("""
<style>
footer { visibility: hidden; }
@media (max-width: 768px) {
    .block-container { padding: 1rem 0.5rem !important; }
    h1 { font-size: 1.4rem !important; }
}
</style>
""", unsafe_allow_html=True)

# ── API Key ───────────────────────────────────
try:
    API_KEY = st.secrets["FMP_API_KEY"]
except Exception:
    API_KEY = ""

FMP = "https://financialmodelingprep.com/api"

# ── 工具函数 ──────────────────────────────────

def fmt(n):
    if n is None or (isinstance(n, float) and pd.isna(n)): return "N/A"
    if abs(n) >= 1e12: return f"{n/1e12:.2f}T"
    if abs(n) >= 1e9:  return f"{n/1e9:.2f}B"
    if abs(n) >= 1e6:  return f"{n/1e6:.2f}M"
    return f"{n:,.0f}"

def fmt_pct(v, already_pct=False):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "N/A"
    return f"{v:.2f}%" if already_pct else f"{v*100:.2f}%"

def is_market_open():
    et = timezone(timedelta(hours=-4))
    now = datetime.now(et)
    if now.weekday() >= 5: return False
    return now.replace(hour=9,minute=30,second=0) <= now <= now.replace(hour=16,minute=0,second=0)

def fmp_get(endpoint, params=None):
    p = {"apikey": API_KEY}
    if params: p.update(params)
    r = requests.get(f"{FMP}{endpoint}", params=p, timeout=10)
    return r.json() if r.ok else {}

def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f: return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f: json.dump(data, f)

def to_excel(dfs: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet, df in dfs.items():
            df.to_excel(writer, sheet_name=sheet[:31])
    return buf.getvalue()

# ── 数据获取 ──────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def fetch_quote(symbol):
    data = fmp_get(f"/v3/quote/{symbol}")
    return data[0] if data and isinstance(data, list) else {}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_profile(symbol):
    data = fmp_get(f"/v3/profile/{symbol}")
    return data[0] if data and isinstance(data, list) else {}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_ratios(symbol):
    data = fmp_get(f"/v3/ratios-ttm/{symbol}")
    return data[0] if data and isinstance(data, list) else {}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_history(symbol, days=365):
    data = fmp_get(f"/v3/historical-price-full/{symbol}", {"timeseries": days})
    if not data or "historical" not in data: return pd.DataFrame()
    df = pd.DataFrame(data["historical"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    df.columns = [c.capitalize() for c in df.columns]
    return df

@st.cache_data(ttl=300, show_spinner=False)
def fetch_income(symbol):
    data = fmp_get(f"/v3/income-statement/{symbol}", {"limit": 8})
    return pd.DataFrame(data) if data else pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def fetch_balance(symbol):
    data = fmp_get(f"/v3/balance-sheet-statement/{symbol}", {"limit": 8})
    return pd.DataFrame(data) if data else pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def fetch_cashflow(symbol):
    data = fmp_get(f"/v3/cash-flow-statement/{symbol}", {"limit": 8})
    return pd.DataFrame(data) if data else pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def fetch_analyst(symbol):
    data = fmp_get(f"/v3/analyst-stock-recommendations/{symbol}", {"limit": 10})
    return pd.DataFrame(data) if data else pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def fetch_insider(symbol):
    data = fmp_get(f"/v4/insider-trading", {"symbol": symbol, "limit": 20})
    return pd.DataFrame(data) if data else pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def fetch_institutions(symbol):
    data = fmp_get(f"/v3/institutional-holder/{symbol}")
    return pd.DataFrame(data) if data else pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def fetch_dividends(symbol):
    data = fmp_get(f"/v3/historical-price-full/stock_dividend/{symbol}")
    if not data or "historical" not in data: return pd.DataFrame()
    return pd.DataFrame(data["historical"])

@st.cache_data(ttl=600, show_spinner=False)
def fetch_news(symbol):
    data = fmp_get(f"/v3/stock_news", {"tickers": symbol, "limit": 8})
    return data if isinstance(data, list) else []

@st.cache_data(ttl=300, show_spinner=False)
def fetch_short(symbol):
    # FMP free doesn't have short data, use profile fields
    profile = fetch_profile(symbol)
    return profile

# ── 技术指标 ──────────────────────────────────

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast    = series.ewm(span=fast).mean()
    ema_slow    = series.ewm(span=slow).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    return macd_line, signal_line, macd_line - signal_line

def calc_boll(series, period=20, std=2):
    mid  = series.rolling(period).mean()
    band = series.rolling(period).std()
    return mid, mid + std*band, mid - std*band

# ── 评分 ─────────────────────────────────────

def valuation_score(q, r):
    score, details = 0, []
    pe = q.get("pe")
    if pe and pe > 0:
        if pe < 15:   score+=2; details.append(("PE < 15，估值偏低", "+2"))
        elif pe < 25: score+=1; details.append((f"PE {pe:.1f}，估值合理", "+1"))
        else:         details.append((f"PE {pe:.1f}，估值偏高", "0"))
    pb = r.get("priceToBookRatioTTM")
    if pb and pb > 0:
        if pb < 1:   score+=2; details.append(("PB < 1，低于净资产", "+2"))
        elif pb < 3: score+=1; details.append(("PB 1~3，合理", "+1"))
        else:        details.append((f"PB {pb:.1f}，溢价较高", "0"))
    roe = r.get("returnOnEquityTTM")
    if roe:
        if roe > 0.2:  score+=2; details.append(("ROE > 20%，盈利能力强", "+2"))
        elif roe > 0.1: score+=1; details.append(("ROE 10~20%", "+1"))
        else:           details.append(("ROE < 10%，盈利能力弱", "0"))
    npm = r.get("netProfitMarginTTM")
    if npm:
        if npm > 0.2:  score+=2; details.append(("净利率 > 20%", "+2"))
        elif npm > 0.1: score+=1; details.append(("净利率 10~20%", "+1"))
        else:           details.append(("净利率 < 10%", "0"))
    return min(score, 10), details

def health_score(r):
    score, details = 0, []
    de = r.get("debtEquityRatioTTM")
    if de is not None:
        if de < 0.5:  score+=2; details.append(("负债权益比 < 0.5，财务稳健", "+2"))
        elif de < 1:  score+=1; details.append(("负债权益比 0.5~1，一般", "+1"))
        else:         details.append((f"负债权益比 {de:.1f}，杠杆较高", "0"))
    cr = r.get("currentRatioTTM")
    if cr:
        if cr > 2:   score+=2; details.append(("流动比率 > 2，短期偿债能力强", "+2"))
        elif cr > 1: score+=1; details.append(("流动比率 1~2，可接受", "+1"))
        else:        details.append(("流动比率 < 1，短期流动性风险", "0"))
    fcf = r.get("freeCashFlowPerShareTTM")
    if fcf:
        if fcf > 0: score+=2; details.append(("自由现金流为正", "+2"))
        else:       details.append(("自由现金流为负", "0"))
    rg = r.get("revenueGrowthTTM") or r.get("revenuePerShareTTM")
    if rg and rg > 0:
        score+=1; details.append(("营收数据正常", "+1"))
    return min(score, 10), details

def score_color(s):
    if s >= 7: return "#26a69a"
    if s >= 4: return "#f39c12"
    return "#ef5350"

# ── 侧边栏 ────────────────────────────────────

with st.sidebar:
    st.title("📈 美股看板")
    query = st.text_input("搜索股票代码", placeholder="如 AAPL TSLA NVDA").strip().upper()
    market_open = is_market_open()
    if market_open: st.success("🟢 美股盘中 · 60秒自动刷新")
    else:           st.info("🔴 美股休市")

    st.divider()
    st.subheader("⭐ 自选股")
    wl = load_json(WATCHLIST_FILE, [])
    new_sym = st.text_input("添加自选股", placeholder="输入代码回车").strip().upper()
    if new_sym and new_sym not in wl:
        wl.append(new_sym); save_json(WATCHLIST_FILE, wl); st.rerun()

    for sym in wl:
        c1, c2 = st.columns([4,1])
        with c1:
            if st.button(sym, key=f"wl_{sym}", use_container_width=True): query = sym
        with c2:
            if st.button("✕", key=f"rm_{sym}"):
                wl.remove(sym); save_json(WATCHLIST_FILE, wl); st.rerun()
    if not wl: st.caption("暂无自选股")

    st.divider()
    period_map   = {"1个月":30,"3个月":90,"6个月":180,"1年":365,"2年":730}
    period_label = st.radio("K线周期", list(period_map.keys()), index=3)
    period_days  = period_map[period_label]

# ── 自动刷新 ──────────────────────────────────
if market_open:
    if "last_refresh" not in st.session_state:
        st.session_state.last_refresh = time.time()
    elapsed = time.time() - st.session_state.last_refresh
    if elapsed >= 60:
        st.session_state.last_refresh = time.time()
        st.cache_data.clear(); st.rerun()
    st.sidebar.caption(f"下次刷新：{max(0,int(60-elapsed))} 秒后")

# ── 大盘概览 ──────────────────────────────────
st.subheader("大盘概览")
idx_syms = {"道琼斯":"%5EDJI","纳斯达克":"%5EIXIC","标普500":"%5EGSPC","恐慌指数":"%5EVIX"}
idx_cols = st.columns(4)
for i, (name, sym) in enumerate(idx_syms.items()):
    with idx_cols[i]:
        try:
            q = fetch_quote(sym)
            price = q.get("price")
            chg   = q.get("changesPercentage")
            sign  = "+" if (chg or 0) >= 0 else ""
            st.metric(name, f"{price:,.2f}" if price else "N/A",
                      f"{sign}{chg:.2f}%" if chg else None)
        except Exception:
            st.metric(name, "N/A")

st.divider()

# ── 价格提醒检查 ──────────────────────────────
alerts = load_json(ALERTS_FILE, {})
for sym, rule in alerts.items():
    try:
        q = fetch_quote(sym)
        price = q.get("price")
        if price:
            if rule.get("high") and price >= rule["high"]:
                st.warning(f"🔔 {sym} 已达目标价 ${rule['high']} (现价 ${price:.2f})")
            if rule.get("low") and price <= rule["low"]:
                st.warning(f"⚠️ {sym} 已跌破止损价 ${rule['low']} (现价 ${price:.2f})")
    except Exception:
        pass

# ── 无查询：自选股概览 ────────────────────────
if not query:
    if wl:
        st.subheader("自选股概览")
        cols = st.columns(min(len(wl), 4))
        for i, sym in enumerate(wl):
            with cols[i % 4]:
                try:
                    q = fetch_quote(sym)
                    price = q.get("price")
                    chg   = q.get("changesPercentage")
                    sign  = "+" if (chg or 0) >= 0 else ""
                    st.metric(sym, f"${price:.2f}" if price else "N/A",
                              f"{sign}{chg:.2f}%" if chg else None)
                except Exception:
                    st.metric(sym, "获取失败")

        if len(wl) >= 2:
            st.subheader("自选股价格相关性")
            try:
                closes = {}
                for sym in wl:
                    h = fetch_history(sym, 365)
                    if not h.empty and "Close" in h.columns:
                        closes[sym] = h["Close"]
                if closes:
                    df_c = pd.DataFrame(closes).dropna()
                    corr = df_c.corr()
                    fig_corr = px.imshow(corr, text_auto=".2f",
                                          color_continuous_scale="RdYlGn", zmin=-1, zmax=1)
                    fig_corr.update_layout(height=350, margin=dict(l=0,r=0,t=10,b=0),
                                            paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_corr, use_container_width=True)
            except Exception:
                pass
    else:
        st.info("在左侧添加自选股，或搜索股票代码开始查询。")
    st.stop()

# ── 股票详情 ──────────────────────────────────
with st.spinner(f"正在获取 {query} 数据..."):
    quote   = fetch_quote(query)
    profile = fetch_profile(query)
    ratios  = fetch_ratios(query)

if not quote or not quote.get("symbol"):
    st.error(f"未找到股票代码：{query}")
    st.stop()

price   = quote.get("price")
prev    = quote.get("previousClose")
chg_pct = quote.get("changesPercentage")
sign    = "+" if (chg_pct or 0) >= 0 else ""

st.title(f"{quote.get('name', query)}  `{query}`")
st.caption(f"{profile.get('sector','')}  ·  {profile.get('industry','')}")

c1,c2,c3,c4,c5 = st.columns(5)
c1.metric("当前价格", f"${price:.2f}" if price else "N/A",
          f"{sign}{chg_pct:.2f}%" if chg_pct else None)
c2.metric("今日区间",  f"${quote.get('dayLow','?')} ~ ${quote.get('dayHigh','?')}")
c3.metric("52周区间", f"${quote.get('yearLow','?')} ~ ${quote.get('yearHigh','?')}")
c4.metric("成交量",   fmt(quote.get("volume")))
c5.metric("市值",     fmt(quote.get("marketCap")))

st.divider()

tabs = st.tabs([
    "📊 K线走势","📐 技术指标","📋 基本面","⭐ 评分",
    "🏭 同行对比","📊 相关性","💰 财报","👔 内部人交易",
    "🏦 机构持仓","💵 股息历史","💼 投资组合","🔔 价格提醒","📰 新闻"
])
(tab_chart,tab_tech,tab_fund,tab_score,
 tab_peer,tab_corr,tab_fin,tab_insider,
 tab_inst,tab_div,tab_port,tab_alert,tab_news) = tabs

# ── K线走势 ───────────────────────────────────
with tab_chart:
    hist = fetch_history(query, period_days)
    if not hist.empty and "Close" in hist.columns:
        hist["MA20"] = hist["Close"].rolling(20).mean()
        hist["MA60"] = hist["Close"].rolling(60).mean()
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=hist.index, open=hist["Open"], high=hist["High"],
            low=hist["Low"], close=hist["Close"],
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350", name="K线"))
        fig.add_trace(go.Scatter(x=hist.index, y=hist["MA20"], name="MA20",
                                  line=dict(color="#f39c12", width=1)))
        fig.add_trace(go.Scatter(x=hist.index, y=hist["MA60"], name="MA60",
                                  line=dict(color="#3498db", width=1)))
        fig.update_layout(xaxis_rangeslider_visible=False, height=420,
                          margin=dict(l=0,r=0,t=10,b=0),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          legend=dict(orientation="h", y=1.05))
        st.plotly_chart(fig, use_container_width=True)

        colors = ["#26a69a" if c >= o else "#ef5350"
                  for c,o in zip(hist["Close"], hist["Open"])]
        fig2 = go.Figure(go.Bar(x=hist.index, y=hist["Volume"], marker_color=colors))
        fig2.update_layout(height=120, margin=dict(l=0,r=0,t=0,b=0),
                           paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)

        st.download_button("⬇️ 导出K线数据 Excel",
                           data=to_excel({"K线数据": hist.reset_index()}),
                           file_name=f"{query}_kline.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ── 技术指标 ──────────────────────────────────
with tab_tech:
    hist = fetch_history(query, period_days)
    if not hist.empty and "Close" in hist.columns:
        close = hist["Close"]
        mid, upper, lower = calc_boll(close)
        macd, signal_line, histo = calc_macd(close)
        rsi = calc_rsi(close)

        fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                            row_heights=[0.5,0.25,0.25], vertical_spacing=0.05,
                            subplot_titles=("布林带","MACD","RSI"))
        fig.add_trace(go.Candlestick(x=hist.index, open=hist["Open"], high=hist["High"],
                                      low=hist["Low"], close=close,
                                      increasing_line_color="#26a69a",
                                      decreasing_line_color="#ef5350", name="K线"), row=1,col=1)
        for y,name,color,dash in [(upper,"上轨","#aaa","dot"),(mid,"中轨","#888","solid"),(lower,"下轨","#aaa","dot")]:
            fig.add_trace(go.Scatter(x=hist.index, y=y, name=name,
                                      line=dict(color=color,width=1,dash=dash)), row=1,col=1)
        colors_h = ["#26a69a" if v>=0 else "#ef5350" for v in histo]
        fig.add_trace(go.Bar(x=hist.index, y=histo, marker_color=colors_h), row=2,col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=macd, name="MACD",
                                  line=dict(color="#f39c12",width=1)), row=2,col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=signal_line, name="Signal",
                                  line=dict(color="#3498db",width=1)), row=2,col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=rsi, name="RSI",
                                  line=dict(color="#9b59b6",width=1.5)), row=3,col=1)
        fig.add_hline(y=70, line_dash="dot", line_color="#ef5350", row=3,col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="#26a69a", row=3,col=1)
        fig.update_layout(height=700, xaxis_rangeslider_visible=False,
                          margin=dict(l=0,r=0,t=30,b=0),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

        c1,c2,c3 = st.columns(3)
        rsi_val = rsi.iloc[-1]
        c1.metric("RSI(14)", f"{rsi_val:.1f}",
                  "超买" if rsi_val>70 else ("超卖" if rsi_val<30 else "中性"))
        c2.metric("MACD", f"{macd.iloc[-1]:.3f}")
        if upper.iloc[-1] != lower.iloc[-1]:
            bpos = (close.iloc[-1]-lower.iloc[-1])/(upper.iloc[-1]-lower.iloc[-1])*100
            c3.metric("布林带位置", f"{bpos:.0f}%")

# ── 基本面 ────────────────────────────────────
with tab_fund:
    cl, cr = st.columns(2)
    with cl:
        st.subheader("估值指标")
        pe  = quote.get("pe")
        eps = quote.get("eps")
        st.table(pd.DataFrame({
            "市盈率 PE":   f"{pe:.2f}"  if pe  else "N/A",
            "每股收益 EPS": f"${eps:.2f}" if eps else "N/A",
            "市净率 PB":   f"{ratios.get('priceToBookRatioTTM'):.2f}" if ratios.get("priceToBookRatioTTM") else "N/A",
            "市销率 PS":   f"{ratios.get('priceToSalesRatioTTM'):.2f}" if ratios.get("priceToSalesRatioTTM") else "N/A",
            "Beta":       f"{profile.get('beta'):.2f}" if profile.get("beta") else "N/A",
            "52周涨跌幅":  f"{quote.get('yearChange')*100:.1f}%" if quote.get("yearChange") else "N/A",
        }.items(), columns=["指标","数值"]).set_index("指标"))

        st.subheader("分析师评级")
        analyst_df = fetch_analyst(query)
        if not analyst_df.empty:
            latest = analyst_df.iloc[0]
            st.metric("最新评级", latest.get("analystRatingsStrongBuy","") and "强烈买入" or latest.get("analystRatingsbuy","") and "买入" or "持有")
            tp = quote.get("priceAvg50")
            if tp: st.metric("50日均价", f"${tp:.2f}")

    with cr:
        st.subheader("盈利能力")
        st.table(pd.DataFrame({
            "毛利率":    fmt_pct(ratios.get("grossProfitMarginTTM")),
            "营业利润率": fmt_pct(ratios.get("operatingProfitMarginTTM")),
            "净利率":    fmt_pct(ratios.get("netProfitMarginTTM")),
            "ROE":     fmt_pct(ratios.get("returnOnEquityTTM")),
            "ROA":     fmt_pct(ratios.get("returnOnAssetsTTM")),
        }.items(), columns=["指标","数值"]).set_index("指标"))

        st.subheader("股息 & 财务")
        dy = profile.get("lastDiv")
        st.table(pd.DataFrame({
            "最新股息":   f"${dy:.2f}" if dy else "N/A",
            "负债权益比": f"{ratios.get('debtEquityRatioTTM'):.2f}" if ratios.get("debtEquityRatioTTM") else "N/A",
            "流动比率":  f"{ratios.get('currentRatioTTM'):.2f}" if ratios.get("currentRatioTTM") else "N/A",
            "均量(50日)": fmt(quote.get("avgVolume")),
        }.items(), columns=["指标","数值"]).set_index("指标"))

# ── 评分 ─────────────────────────────────────
with tab_score:
    sc1, sc2 = st.columns(2)
    with sc1:
        st.subheader("估值评分")
        vs, v_det = valuation_score(quote, ratios)
        color_v = score_color(vs)
        fig_g = go.Figure(go.Indicator(mode="gauge+number", value=vs,
            gauge={"axis":{"range":[0,10]},"bar":{"color":color_v},
                   "steps":[{"range":[0,4],"color":"#fde8e8"},
                             {"range":[4,7],"color":"#fef9e7"},
                             {"range":[7,10],"color":"#e8f8f5"}]}))
        fig_g.update_layout(height=200, margin=dict(l=20,r=20,t=10,b=10),
                             paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_g, use_container_width=True)
        for desc, pts in v_det:
            st.write(f"{'✅' if pts!='0' else '➖'} {desc}  `{pts}`")

    with sc2:
        st.subheader("财务健康评分")
        hs, h_det = health_score(ratios)
        color_h = score_color(hs)
        fig_g2 = go.Figure(go.Indicator(mode="gauge+number", value=hs,
            gauge={"axis":{"range":[0,10]},"bar":{"color":color_h},
                   "steps":[{"range":[0,4],"color":"#fde8e8"},
                             {"range":[4,7],"color":"#fef9e7"},
                             {"range":[7,10],"color":"#e8f8f5"}]}))
        fig_g2.update_layout(height=200, margin=dict(l=20,r=20,t=10,b=10),
                              paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_g2, use_container_width=True)
        for desc, pts in h_det:
            st.write(f"{'✅' if pts!='0' else '➖'} {desc}  `{pts}`")

# ── 同行对比 ──────────────────────────────────
with tab_peer:
    peers = PEERS.get(query, [])
    if not peers:
        custom = st.text_input("输入对比股票（空格分隔）", placeholder="AAPL MSFT GOOGL")
        peers = [s.upper() for s in custom.split() if s.strip()] if custom else []

    if peers:
        symbols = [query] + peers
        rows = []
        for sym in symbols:
            try:
                q = fetch_quote(sym)
                r = fetch_ratios(sym)
                chg = q.get("changesPercentage")
                rows.append({
                    "股票": sym,
                    "价格": f"${q.get('price'):.2f}" if q.get("price") else "N/A",
                    "涨跌幅": f"{'+'if(chg or 0)>=0 else ''}{chg:.2f}%" if chg else "N/A",
                    "市值": fmt(q.get("marketCap")),
                    "PE":  f"{q.get('pe'):.1f}" if q.get("pe") else "N/A",
                    "净利率": fmt_pct(r.get("netProfitMarginTTM")),
                    "ROE":  fmt_pct(r.get("returnOnEquityTTM")),
                })
            except Exception:
                rows.append({"股票": sym, "价格": "获取失败"})

        df_peer = pd.DataFrame(rows).set_index("股票")
        st.dataframe(df_peer, use_container_width=True)
        st.download_button("⬇️ 导出同行对比 Excel",
                           data=to_excel({"同行对比": df_peer.reset_index()}),
                           file_name=f"{query}_peers.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        mcs = {r["股票"]: fetch_quote(r["股票"]).get("marketCap",0)/1e9
               for r in rows if fetch_quote(r["股票"]).get("marketCap")}
        if mcs:
            fig_peer = go.Figure(go.Bar(
                x=list(mcs.keys()), y=list(mcs.values()),
                marker_color=["#3498db" if k==query else "#95a5a6" for k in mcs],
                text=[f"{v:.0f}B" for v in mcs.values()], textposition="outside"))
            fig_peer.update_layout(title="市值对比（十亿美元）", height=300,
                                    margin=dict(l=0,r=0,t=40,b=0),
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_peer, use_container_width=True)

# ── 相关性 ────────────────────────────────────
with tab_corr:
    st.subheader("多股票价格相关性")
    default_syms = " ".join(PEERS.get(query, [query]))
    corr_input = st.text_input("输入股票代码（空格分隔）", value=f"{query} {default_syms}")
    corr_syms = list(dict.fromkeys([s.upper() for s in corr_input.split() if s]))[:8]

    if len(corr_syms) >= 2:
        closes = {}
        for sym in corr_syms:
            h = fetch_history(sym, 365)
            if not h.empty and "Close" in h.columns:
                closes[sym] = h["Close"]
        if closes:
            df_c = pd.DataFrame(closes).dropna()
            corr = df_c.corr()
            fig_corr = px.imshow(corr, text_auto=".2f",
                                  color_continuous_scale="RdYlGn", zmin=-1, zmax=1,
                                  title="1年价格相关性")
            fig_corr.update_layout(height=400, margin=dict(l=0,r=0,t=40,b=0),
                                    paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_corr, use_container_width=True)
            st.caption("1=完全同向  0=无关  -1=完全反向")

            df_norm = df_c / df_c.iloc[0] * 100
            fig_norm = go.Figure()
            for col in df_norm.columns:
                fig_norm.add_trace(go.Scatter(x=df_norm.index, y=df_norm[col], name=col))
            fig_norm.update_layout(title="归一化走势对比（基准=100）", height=350,
                                    margin=dict(l=0,r=0,t=40,b=0),
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_norm, use_container_width=True)

# ── 财报 ─────────────────────────────────────
with tab_fin:
    income_df = fetch_income(query)
    balance_df = fetch_balance(query)

    if not income_df.empty:
        st.subheader("年度利润表")
        inc_cols = ["date","revenue","grossProfit","operatingIncome","netIncome"]
        inc_labels = {"date":"日期","revenue":"营收","grossProfit":"毛利润",
                      "operatingIncome":"营业利润","netIncome":"净利润"}
        df_show = income_df[[c for c in inc_cols if c in income_df.columns]].head(5).copy()
        df_show.rename(columns=inc_labels, inplace=True)
        for col in ["营收","毛利润","营业利润","净利润"]:
            if col in df_show.columns:
                df_show[col] = df_show[col].apply(fmt)
        st.dataframe(df_show.set_index("日期"), use_container_width=True)

        # 趋势图
        plot_df = income_df[["date","revenue","netIncome"]].head(8)[::-1]
        fig_inc = go.Figure()
        fig_inc.add_trace(go.Bar(x=plot_df["date"].astype(str).str[:4],
                                  y=plot_df["revenue"]/1e9, name="营收(B)", marker_color="#3498db"))
        fig_inc.add_trace(go.Bar(x=plot_df["date"].astype(str).str[:4],
                                  y=plot_df["netIncome"]/1e9, name="净利润(B)", marker_color="#26a69a"))
        fig_inc.update_layout(barmode="group", height=300, yaxis_title="十亿美元",
                               margin=dict(l=0,r=0,t=10,b=0),
                               paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_inc, use_container_width=True)

        st.download_button("⬇️ 导出财报 Excel",
                           data=to_excel({"利润表": income_df, "资产负债": balance_df}),
                           file_name=f"{query}_financials.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    if not balance_df.empty:
        st.subheader("年度资产负债表")
        bal_cols = ["date","totalAssets","totalLiabilities","totalStockholdersEquity","cashAndCashEquivalents"]
        bal_labels = {"date":"日期","totalAssets":"总资产","totalLiabilities":"总负债",
                      "totalStockholdersEquity":"股东权益","cashAndCashEquivalents":"现金"}
        df_bal = balance_df[[c for c in bal_cols if c in balance_df.columns]].head(5).copy()
        df_bal.rename(columns=bal_labels, inplace=True)
        for col in ["总资产","总负债","股东权益","现金"]:
            if col in df_bal.columns:
                df_bal[col] = df_bal[col].apply(fmt)
        st.dataframe(df_bal.set_index("日期"), use_container_width=True)

# ── 内部人交易 ────────────────────────────────
with tab_insider:
    st.subheader("内部人交易记录")
    insider_df = fetch_insider(query)
    if not insider_df.empty:
        show_cols = [c for c in ["reportingName","transactionType","securitiesTransacted",
                                  "price","transactionDate"] if c in insider_df.columns]
        labels = {"reportingName":"姓名","transactionType":"类型",
                  "securitiesTransacted":"股数","price":"价格","transactionDate":"日期"}
        df_show = insider_df[show_cols].head(20).rename(columns=labels)
        st.dataframe(df_show, use_container_width=True)

        if "类型" in df_show.columns:
            buy  = df_show["类型"].str.contains("Buy|P-Purchase", case=False, na=False).sum()
            sell = df_show["类型"].str.contains("Sell|S-Sale", case=False, na=False).sum()
            c1, c2 = st.columns(2)
            c1.metric("内部人买入", buy)
            c2.metric("内部人卖出", sell)

        st.download_button("⬇️ 导出内部人交易 Excel",
                           data=to_excel({"内部人交易": insider_df}),
                           file_name=f"{query}_insider.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("暂无内部人交易数据")

# ── 机构持仓 ──────────────────────────────────
with tab_inst:
    st.subheader("机构持仓")
    inst_df = fetch_institutions(query)
    if not inst_df.empty:
        st.dataframe(inst_df.head(15), use_container_width=True)
        if "weight" in inst_df.columns and "holder" in inst_df.columns:
            top = inst_df.head(10)
            fig_inst = go.Figure(go.Bar(
                x=top["weight"], y=top["holder"], orientation="h",
                marker_color="#3498db"))
            fig_inst.update_layout(title="持仓占比前10机构", height=300,
                                    margin=dict(l=0,r=0,t=40,b=0),
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_inst, use_container_width=True)
    else:
        st.info("暂无机构持仓数据")

# ── 股息历史 ──────────────────────────────────
with tab_div:
    st.subheader("股息历史")
    div_df = fetch_dividends(query)
    if not div_df.empty:
        div_df["date"] = pd.to_datetime(div_df["date"])
        div_df["year"] = div_df["date"].dt.year
        ann_div = div_df.groupby("year")["dividend"].sum().reset_index()
        fig_div = go.Figure(go.Bar(
            x=ann_div["year"], y=ann_div["dividend"],
            marker_color="#26a69a",
            text=[f"${v:.2f}" for v in ann_div["dividend"]], textposition="outside"))
        fig_div.update_layout(title="年度每股股息", height=300,
                               margin=dict(l=0,r=0,t=40,b=0),
                               paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_div, use_container_width=True)
        st.dataframe(div_df[["date","dividend"]].head(20), use_container_width=True)
    else:
        st.info("该股票暂无股息记录")

# ── 投资组合 ──────────────────────────────────
with tab_port:
    st.subheader("我的投资组合")
    portfolio = load_json(PORTFOLIO_FILE, {})

    with st.form("add_position"):
        pc1,pc2,pc3 = st.columns(3)
        p_sym  = pc1.text_input("股票代码").strip().upper()
        p_qty  = pc2.number_input("持仓数量", min_value=0.0, step=1.0)
        p_cost = pc3.number_input("买入均价 ($)", min_value=0.0, step=0.01)
        if st.form_submit_button("添加/更新"):
            if p_sym and p_qty > 0 and p_cost > 0:
                portfolio[p_sym] = {"qty": p_qty, "cost": p_cost}
                save_json(PORTFOLIO_FILE, portfolio); st.rerun()

    if portfolio:
        rows_p, total_cost, total_val = [], 0, 0
        for sym, pos in portfolio.items():
            try:
                q = fetch_quote(sym)
                p = q.get("price") or pos["cost"]
                ct = pos["qty"] * pos["cost"]
                vt = pos["qty"] * p
                pnl = vt - ct
                pnl_pct = (pnl/ct*100) if ct else 0
                total_cost += ct; total_val += vt
                rows_p.append({
                    "股票": sym, "数量": pos["qty"],
                    "买入价": f"${pos['cost']:.2f}", "现价": f"${p:.2f}",
                    "持仓市值": f"${vt:,.2f}",
                    "盈亏": f"{'+'if pnl>=0 else ''}${pnl:,.2f}",
                    "盈亏%": f"{'+'if pnl_pct>=0 else ''}{pnl_pct:.2f}%",
                })
            except Exception:
                rows_p.append({"股票": sym, "买入价": f"${pos['cost']:.2f}", "现价": "N/A"})

        total_pnl = total_val - total_cost
        tpp = (total_pnl/total_cost*100) if total_cost else 0
        m1,m2,m3 = st.columns(3)
        m1.metric("总持仓市值", f"${total_val:,.2f}")
        m2.metric("总成本", f"${total_cost:,.2f}")
        m3.metric("总盈亏", f"{'+'if total_pnl>=0 else ''}${total_pnl:,.2f}",
                  f"{'+'if tpp>=0 else ''}{tpp:.2f}%")

        df_port = pd.DataFrame(rows_p).set_index("股票")
        st.dataframe(df_port, use_container_width=True)
        st.download_button("⬇️ 导出持仓 Excel",
                           data=to_excel({"投资组合": df_port.reset_index()}),
                           file_name="portfolio.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        pie_data = {}
        for r in rows_p:
            if "持仓市值" in r:
                try: pie_data[r["股票"]] = float(r["持仓市值"].replace("$","").replace(",",""))
                except: pass
        if pie_data:
            fig_pie = go.Figure(go.Pie(labels=list(pie_data.keys()),
                                        values=list(pie_data.values()), hole=0.4))
            fig_pie.update_layout(height=300, margin=dict(l=0,r=0,t=10,b=0),
                                   paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_pie, use_container_width=True)

        del_sym = st.selectbox("删除持仓", [""] + list(portfolio.keys()))
        if del_sym and st.button("确认删除"):
            del portfolio[del_sym]; save_json(PORTFOLIO_FILE, portfolio); st.rerun()
    else:
        st.info("还没有持仓，在上方表单添加。")

# ── 价格提醒 ──────────────────────────────────
with tab_alert:
    st.subheader("价格提醒")
    with st.form("add_alert"):
        al1,al2,al3 = st.columns(3)
        a_sym  = al1.text_input("股票代码", value=query).strip().upper()
        a_high = al2.number_input("目标价 ($)（可留0）", min_value=0.0, step=0.01)
        a_low  = al3.number_input("止损价 ($)（可留0）", min_value=0.0, step=0.01)
        if st.form_submit_button("设置提醒"):
            if a_sym:
                alerts[a_sym] = {"high": a_high if a_high>0 else None,
                                  "low":  a_low  if a_low>0  else None}
                save_json(ALERTS_FILE, alerts)
                st.success(f"已设置 {a_sym} 的价格提醒")

    if alerts:
        st.dataframe(pd.DataFrame([
            {"股票":s, "目标价":f"${r['high']}" if r.get("high") else "—",
             "止损价":f"${r['low']}" if r.get("low") else "—"}
            for s,r in alerts.items()]).set_index("股票"), use_container_width=True)
        del_alert = st.selectbox("删除提醒", [""] + list(alerts.keys()))
        if del_alert and st.button("确认删除提醒"):
            del alerts[del_alert]; save_json(ALERTS_FILE, alerts); st.rerun()

# ── 新闻 ─────────────────────────────────────
with tab_news:
    st.subheader(f"{query} 最新新闻")
    news_items = fetch_news(query)
    if news_items:
        for item in news_items:
            title = item.get("title","")
            url   = item.get("url","")
            pub   = item.get("publishedDate","")[:10]
            if url:
                st.markdown(f"**[{title}]({url})**  "
                            f"<span style='color:#888;font-size:12px'>{pub}</span>",
                            unsafe_allow_html=True)
            else:
                st.markdown(f"**{title}**  "
                            f"<span style='color:#888;font-size:12px'>{pub}</span>",
                            unsafe_allow_html=True)
            st.divider()
    else:
        st.info("暂无新闻")
