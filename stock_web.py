import json
import os
import io
import time
import warnings
import urllib3
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
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
/* 移动端优化 */
@media (max-width: 768px) {
    .block-container { padding: 1rem 0.5rem !important; }
    h1 { font-size: 1.4rem !important; }
    h2 { font-size: 1.1rem !important; }
    [data-testid="stSidebar"] { min-width: 200px !important; }
}
/* 评分卡片 */
.score-card {
    border-radius: 10px;
    padding: 12px 16px;
    margin: 4px 0;
    font-size: 14px;
}
</style>
""", unsafe_allow_html=True)

# ── 工具函数 ──────────────────────────────────

def fmt(n):
    if n is None or (isinstance(n, float) and pd.isna(n)): return "N/A"
    if abs(n) >= 1e12: return f"{n/1e12:.2f}T"
    if abs(n) >= 1e9:  return f"{n/1e9:.2f}B"
    if abs(n) >= 1e6:  return f"{n/1e6:.2f}M"
    return f"{n:,.0f}"

def fmt_pct(v, scale=1):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "N/A"
    return f"{v*scale:.2f}%"

def is_market_open():
    et = timezone(timedelta(hours=-4))
    now = datetime.now(et)
    if now.weekday() >= 5: return False
    open_t  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_t <= now <= close_t

def make_session():
    try:
        from curl_cffi import requests as cr
        return cr.Session(impersonate="chrome")
    except ImportError:
        return None

def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

def to_excel(dfs: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet, df in dfs.items():
            df.to_excel(writer, sheet_name=sheet[:31])
    return buf.getvalue()

# ── 数据获取 ──────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def fetch_info(symbol):
    return yf.Ticker(symbol, session=make_session()).info

@st.cache_data(ttl=60, show_spinner=False)
def fetch_history(symbol, period):
    return yf.Ticker(symbol, session=make_session()).history(period=period)

@st.cache_data(ttl=300, show_spinner=False)
def fetch_financials(symbol):
    t = yf.Ticker(symbol, session=make_session())
    return t.quarterly_income_stmt, t.quarterly_balance_sheet

@st.cache_data(ttl=300, show_spinner=False)
def fetch_annual_financials(symbol):
    t = yf.Ticker(symbol, session=make_session())
    return t.income_stmt, t.balance_sheet, t.cashflow

@st.cache_data(ttl=300, show_spinner=False)
def fetch_calendar(symbol):
    try:
        return yf.Ticker(symbol, session=make_session()).calendar
    except Exception:
        return None

@st.cache_data(ttl=300, show_spinner=False)
def fetch_options_dates(symbol):
    try:
        return yf.Ticker(symbol, session=make_session()).options
    except Exception:
        return []

@st.cache_data(ttl=300, show_spinner=False)
def fetch_options_chain(symbol, date):
    try:
        t = yf.Ticker(symbol, session=make_session())
        chain = t.option_chain(date)
        return chain.calls, chain.puts
    except Exception:
        return None, None

@st.cache_data(ttl=300, show_spinner=False)
def fetch_insider(symbol):
    try:
        return yf.Ticker(symbol, session=make_session()).insider_transactions
    except Exception:
        return None

@st.cache_data(ttl=300, show_spinner=False)
def fetch_institutions(symbol):
    try:
        t = yf.Ticker(symbol, session=make_session())
        return t.institutional_holders, t.mutualfund_holders
    except Exception:
        return None, None

@st.cache_data(ttl=300, show_spinner=False)
def fetch_dividends(symbol):
    try:
        return yf.Ticker(symbol, session=make_session()).dividends
    except Exception:
        return None

@st.cache_data(ttl=600, show_spinner=False)
def fetch_news(symbol):
    try:
        import requests, xml.etree.ElementTree as ET
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"}, verify=False)
        tree = ET.fromstring(r.content)
        items = []
        for item in tree.findall(".//item")[:8]:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            if pub:
                try:
                    dt  = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
                    pub = dt.strftime("%m/%d %H:%M")
                except Exception:
                    pub = pub[:10]
            if title:
                items.append({"title": title, "link": link, "pub": pub})
        return items
    except Exception:
        return []

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
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_boll(series, period=20, std=2):
    mid  = series.rolling(period).mean()
    band = series.rolling(period).std()
    return mid, mid + std*band, mid - std*band

# ── 评分系统 ──────────────────────────────────

def valuation_score(info):
    score, details = 0, []
    pe = info.get("trailingPE")
    if pe:
        if pe < 15:   score += 2; details.append(("PE < 15，估值偏低", "+2"))
        elif pe < 25: score += 1; details.append(("PE 15~25，估值合理", "+1"))
        else:         details.append((f"PE {pe:.1f}，估值偏高", "0"))

    pb = info.get("priceToBook")
    if pb:
        if pb < 1:    score += 2; details.append(("PB < 1，低于净资产", "+2"))
        elif pb < 3:  score += 1; details.append(("PB 1~3，合理", "+1"))
        else:         details.append((f"PB {pb:.1f}，溢价较高", "0"))

    roe = info.get("returnOnEquity")
    if roe:
        if roe > 0.2:  score += 2; details.append(("ROE > 20%，盈利能力强", "+2"))
        elif roe > 0.1: score += 1; details.append(("ROE 10~20%，一般", "+1"))
        else:           details.append(("ROE < 10%，盈利能力弱", "0"))

    margin = info.get("profitMargins")
    if margin:
        if margin > 0.2:  score += 2; details.append(("净利率 > 20%", "+2"))
        elif margin > 0.1: score += 1; details.append(("净利率 10~20%", "+1"))
        else:              details.append(("净利率 < 10%", "0"))

    fpe = info.get("forwardPE")
    if fpe and pe and fpe < pe:
        score += 1; details.append(("前瞻PE < 当前PE，盈利预期增长", "+1"))

    total = min(score, 10)
    return total, details

def health_score(info):
    score, details = 0, []

    de = info.get("debtToEquity")
    if de is not None:
        if de < 50:    score += 2; details.append(("负债率 < 50%，财务稳健", "+2"))
        elif de < 100: score += 1; details.append(("负债率 50~100%，一般", "+1"))
        else:          details.append((f"负债率 {de:.0f}%，杠杆较高", "0"))

    cr = info.get("currentRatio")
    if cr:
        if cr > 2:    score += 2; details.append(("流动比率 > 2，短期偿债能力强", "+2"))
        elif cr > 1:  score += 1; details.append(("流动比率 1~2，可接受", "+1"))
        else:         details.append(("流动比率 < 1，短期流动性风险", "0"))

    fcf = info.get("freeCashflow")
    if fcf:
        if fcf > 0:   score += 2; details.append(("自由现金流为正", "+2"))
        else:         details.append(("自由现金流为负", "0"))

    rg = info.get("revenueGrowth")
    if rg:
        if rg > 0.15:  score += 2; details.append(("营收增速 > 15%", "+2"))
        elif rg > 0.05: score += 1; details.append(("营收增速 5~15%", "+1"))
        else:           details.append(("营收增速 < 5%", "0"))

    total = min(score, 10)
    return total, details

def score_color(s):
    if s >= 7: return "#26a69a"
    if s >= 4: return "#f39c12"
    return "#ef5350"

# ── 侧边栏 ────────────────────────────────────

with st.sidebar:
    st.title("📈 美股看板")
    query = st.text_input("搜索股票代码", placeholder="如 AAPL TSLA NVDA").strip().upper()

    market_open = is_market_open()
    if market_open:
        st.success("🟢 美股盘中 · 60秒自动刷新")
    else:
        st.info("🔴 美股休市")

    st.divider()
    st.subheader("⭐ 自选股")
    wl = load_json(WATCHLIST_FILE, [])
    new_sym = st.text_input("添加自选股", placeholder="输入代码回车").strip().upper()
    if new_sym and new_sym not in wl:
        wl.append(new_sym)
        save_json(WATCHLIST_FILE, wl)
        st.rerun()

    if wl:
        for sym in wl:
            c1, c2 = st.columns([4, 1])
            with c1:
                if st.button(sym, key=f"wl_{sym}", use_container_width=True):
                    query = sym
            with c2:
                if st.button("✕", key=f"rm_{sym}"):
                    wl.remove(sym)
                    save_json(WATCHLIST_FILE, wl)
                    st.rerun()
    else:
        st.caption("暂无自选股")

    st.divider()
    period_map   = {"1个月":"1mo","3个月":"3mo","6个月":"6mo","1年":"1y","2年":"2y"}
    period_label = st.radio("K线周期", list(period_map.keys()), index=3)
    period       = period_map[period_label]

# ── 自动刷新 ──────────────────────────────────

if market_open:
    if "last_refresh" not in st.session_state:
        st.session_state.last_refresh = time.time()
    elapsed = time.time() - st.session_state.last_refresh
    if elapsed >= 60:
        st.session_state.last_refresh = time.time()
        st.cache_data.clear()
        st.rerun()
    st.sidebar.caption(f"下次刷新：{max(0, int(60-elapsed))} 秒后")

# ── 大盘概览 ──────────────────────────────────

st.subheader("大盘概览")
idx_cols = st.columns(4)
for i, (name, sym) in enumerate(INDICES.items()):
    with idx_cols[i]:
        try:
            info  = fetch_info(sym)
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            prev  = info.get("previousClose")
            chg   = ((price-prev)/prev*100) if price and prev else None
            sign  = "+" if (chg or 0) >= 0 else ""
            st.metric(name, f"{price:,.2f}" if price else "N/A",
                      f"{sign}{chg:.2f}%" if chg else None)
        except Exception:
            st.metric(name, "N/A")

st.divider()

# ── 涨跌提醒检查 ──────────────────────────────

alerts = load_json(ALERTS_FILE, {})
for sym, rule in alerts.items():
    try:
        info  = fetch_info(sym)
        price = info.get("currentPrice") or info.get("regularMarketPrice")
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
                    info  = fetch_info(sym)
                    price = info.get("currentPrice") or info.get("regularMarketPrice")
                    prev  = info.get("previousClose")
                    chg   = ((price-prev)/prev*100) if price and prev else None
                    sign  = "+" if (chg or 0) >= 0 else ""
                    st.metric(sym, f"${price:.2f}" if price else "N/A",
                              f"{sign}{chg:.2f}%" if chg else None)
                except Exception:
                    st.metric(sym, "获取失败")

        # 相关性热力图（自选股）
        if len(wl) >= 2:
            st.subheader("自选股价格相关性")
            try:
                closes = {}
                for sym in wl:
                    h = fetch_history(sym, "1y")
                    if not h.empty:
                        closes[sym] = h["Close"]
                if closes:
                    df_close = pd.DataFrame(closes).dropna()
                    corr = df_close.corr()
                    fig_corr = px.imshow(corr, text_auto=".2f", color_continuous_scale="RdYlGn",
                                         zmin=-1, zmax=1, title="1年价格相关性（1=完全同向，-1=完全反向）")
                    fig_corr.update_layout(height=350, margin=dict(l=0,r=0,t=40,b=0),
                                            paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_corr, use_container_width=True)
            except Exception:
                pass
    else:
        st.info("在左侧添加自选股，或搜索股票代码开始查询。")
    st.stop()

# ── 股票详情 ──────────────────────────────────

with st.spinner(f"正在获取 {query} 数据..."):
    info = fetch_info(query)

if not info or not info.get("quoteType"):
    st.error(f"未找到股票代码：{query}")
    st.stop()

price   = info.get("currentPrice") or info.get("regularMarketPrice")
prev    = info.get("previousClose")
change  = (price - prev) if price and prev else None
chg_pct = (change / prev * 100) if change and prev else None
sign    = "+" if (chg_pct or 0) >= 0 else ""

st.title(f"{info.get('shortName', query)}  `{query}`")
st.caption(f"{info.get('sector','')}  ·  {info.get('industry','')}")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("当前价格", f"${price:.2f}" if price else "N/A",
          f"{sign}{chg_pct:.2f}%" if chg_pct else None)
c2.metric("今日区间", f"${info.get('dayLow','?')} ~ ${info.get('dayHigh','?')}")
c3.metric("52周区间", f"${info.get('fiftyTwoWeekLow','?')} ~ ${info.get('fiftyTwoWeekHigh','?')}")
c4.metric("成交量",   fmt(info.get("volume")))
c5.metric("市值",     fmt(info.get("marketCap")))

st.divider()

tabs = st.tabs([
    "📊 K线走势", "📐 技术指标", "📋 基本面", "⭐ 评分",
    "🏭 同行对比", "📊 相关性", "💰 财报", "📅 财报日历",
    "🎯 期权", "🏦 机构持仓", "👔 内部人交易", "💵 股息历史",
    "📉 做空数据", "💼 投资组合", "🔔 价格提醒", "📰 新闻"
])
(tab_chart, tab_tech, tab_fund, tab_score,
 tab_peer, tab_corr, tab_fin, tab_cal,
 tab_opt, tab_inst, tab_insider, tab_div,
 tab_short, tab_port, tab_alert, tab_news) = tabs

# ── K线走势 ───────────────────────────────────
with tab_chart:
    hist = fetch_history(query, period)
    if not hist.empty:
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
                  for c, o in zip(hist["Close"], hist["Open"])]
        fig2 = go.Figure(go.Bar(x=hist.index, y=hist["Volume"], marker_color=colors))
        fig2.update_layout(height=120, margin=dict(l=0,r=0,t=0,b=0),
                           paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)

        # 导出
        st.download_button("⬇️ 导出K线数据 Excel",
                           data=to_excel({"K线数据": hist.reset_index()}),
                           file_name=f"{query}_kline.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ── 技术指标 ──────────────────────────────────
with tab_tech:
    hist = fetch_history(query, period)
    if not hist.empty:
        close = hist["Close"]
        mid, upper, lower = calc_boll(close)
        macd, signal_line, histo = calc_macd(close)
        rsi = calc_rsi(close)

        fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                            row_heights=[0.5, 0.25, 0.25],
                            vertical_spacing=0.05,
                            subplot_titles=("布林带", "MACD", "RSI"))
        fig.add_trace(go.Candlestick(x=hist.index, open=hist["Open"], high=hist["High"],
                                      low=hist["Low"], close=close,
                                      increasing_line_color="#26a69a",
                                      decreasing_line_color="#ef5350", name="K线"), row=1, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=upper, name="上轨",
                                  line=dict(color="#aaa", width=1, dash="dot")), row=1, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=mid, name="中轨",
                                  line=dict(color="#888", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=lower, name="下轨",
                                  line=dict(color="#aaa", width=1, dash="dot"),
                                  fill="tonexty", fillcolor="rgba(150,150,150,0.05)"), row=1, col=1)
        colors_h = ["#26a69a" if v >= 0 else "#ef5350" for v in histo]
        fig.add_trace(go.Bar(x=hist.index, y=histo, marker_color=colors_h, name="柱状"), row=2, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=macd, name="MACD",
                                  line=dict(color="#f39c12", width=1)), row=2, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=signal_line, name="Signal",
                                  line=dict(color="#3498db", width=1)), row=2, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=rsi, name="RSI",
                                  line=dict(color="#9b59b6", width=1.5)), row=3, col=1)
        fig.add_hline(y=70, line_dash="dot", line_color="#ef5350", row=3, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="#26a69a", row=3, col=1)
        fig.update_layout(height=700, xaxis_rangeslider_visible=False,
                          margin=dict(l=0,r=0,t=30,b=0),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

        c1, c2, c3 = st.columns(3)
        rsi_val = rsi.iloc[-1]
        rsi_sig = "超买" if rsi_val > 70 else ("超卖" if rsi_val < 30 else "中性")
        c1.metric("RSI(14)", f"{rsi_val:.1f}", rsi_sig)
        c2.metric("MACD", f"{macd.iloc[-1]:.3f}")
        boll_pos = (close.iloc[-1]-lower.iloc[-1])/(upper.iloc[-1]-lower.iloc[-1])*100
        c3.metric("布林带位置", f"{boll_pos:.0f}%")

# ── 基本面 ────────────────────────────────────
with tab_fund:
    cl, cr = st.columns(2)
    with cl:
        st.subheader("估值指标")
        pe  = info.get("trailingPE")
        fpe = info.get("forwardPE")
        pb  = info.get("priceToBook")
        ps  = info.get("priceToSalesTrailing12Months")
        st.table(pd.DataFrame({
            "市盈率 PE（TTM）": f"{pe:.2f}" if pe else "N/A",
            "前瞻市盈率":       f"{fpe:.2f}" if fpe else "N/A",
            "市净率 PB":       f"{pb:.2f}" if pb else "N/A",
            "市销率 PS":       f"{ps:.2f}" if ps else "N/A",
            "每股收益 EPS":     f"${info.get('trailingEps'):.2f}" if info.get("trailingEps") else "N/A",
            "Beta":           f"{info.get('beta'):.2f}" if info.get("beta") else "N/A",
        }.items(), columns=["指标","数值"]).set_index("指标"))

        st.subheader("分析师评级")
        rec    = info.get("recommendationKey","N/A").replace("_"," ").title()
        tm     = info.get("targetMeanPrice")
        tl     = info.get("targetLowPrice")
        th     = info.get("targetHighPrice")
        n_anal = info.get("numberOfAnalystOpinions")
        upside = ((tm-price)/price*100) if tm and price else None
        st.metric("综合评级", rec, f"{n_anal} 位分析师" if n_anal else None)
        if tm:
            a1, a2, a3 = st.columns(3)
            a1.metric("目标价（均）", f"${tm:.2f}")
            a2.metric("目标价（低）", f"${tl:.2f}" if tl else "N/A")
            a3.metric("目标价（高）", f"${th:.2f}" if th else "N/A")
        if upside is not None:
            s = "+" if upside >= 0 else ""
            st.metric("潜在涨幅", f"{s}{upside:.1f}%")

    with cr:
        st.subheader("盈利能力")
        st.table(pd.DataFrame({
            "毛利率":    fmt_pct(info.get("grossMargins"), 100),
            "营业利润率": fmt_pct(info.get("operatingMargins"), 100),
            "净利率":    fmt_pct(info.get("profitMargins"), 100),
            "ROE":     fmt_pct(info.get("returnOnEquity"), 100),
            "ROA":     fmt_pct(info.get("returnOnAssets"), 100),
        }.items(), columns=["指标","数值"]).set_index("指标"))

        st.subheader("股息 & 其他")
        div = info.get("trailingAnnualDividendYield") or info.get("dividendYield")
        if div and div > 1: div = div / 100
        st.table(pd.DataFrame({
            "股息率":   fmt_pct(div, 100) if div else "N/A",
            "每股股息":  f"${info.get('dividendRate'):.2f}" if info.get("dividendRate") else "N/A",
            "负债权益比": f"{info.get('debtToEquity'):.1f}%" if info.get("debtToEquity") else "N/A",
            "流动比率":  f"{info.get('currentRatio'):.2f}" if info.get("currentRatio") else "N/A",
            "均量(3M)": fmt(info.get("averageVolume")),
        }.items(), columns=["指标","数值"]).set_index("指标"))

# ── 评分系统 ──────────────────────────────────
with tab_score:
    sc1, sc2 = st.columns(2)

    with sc1:
        st.subheader("估值评分")
        vs, v_details = valuation_score(info)
        color_v = score_color(vs)
        st.markdown(f"<h1 style='color:{color_v};text-align:center'>{vs} / 10</h1>",
                    unsafe_allow_html=True)
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=vs,
            gauge={"axis":{"range":[0,10]},
                   "bar":{"color":color_v},
                   "steps":[{"range":[0,4],"color":"#fde8e8"},
                             {"range":[4,7],"color":"#fef9e7"},
                             {"range":[7,10],"color":"#e8f8f5"}]},
        ))
        fig_gauge.update_layout(height=200, margin=dict(l=20,r=20,t=10,b=10),
                                 paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_gauge, use_container_width=True)
        for desc, pts in v_details:
            icon = "✅" if pts.startswith("+") and pts != "+0" else "➖"
            st.write(f"{icon} {desc}  `{pts}`")

    with sc2:
        st.subheader("财务健康评分")
        hs, h_details = health_score(info)
        color_h = score_color(hs)
        st.markdown(f"<h1 style='color:{color_h};text-align:center'>{hs} / 10</h1>",
                    unsafe_allow_html=True)
        fig_gauge2 = go.Figure(go.Indicator(
            mode="gauge+number",
            value=hs,
            gauge={"axis":{"range":[0,10]},
                   "bar":{"color":color_h},
                   "steps":[{"range":[0,4],"color":"#fde8e8"},
                             {"range":[4,7],"color":"#fef9e7"},
                             {"range":[7,10],"color":"#e8f8f5"}]},
        ))
        fig_gauge2.update_layout(height=200, margin=dict(l=20,r=20,t=10,b=10),
                                  paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_gauge2, use_container_width=True)
        for desc, pts in h_details:
            icon = "✅" if pts.startswith("+") and pts != "+0" else "➖"
            st.write(f"{icon} {desc}  `{pts}`")

# ── 同行对比 ──────────────────────────────────
with tab_peer:
    peers = PEERS.get(query, [])
    if not peers:
        st.info("暂无预设同行，手动输入：")
        custom = st.text_input("输入对比股票（空格分隔）", placeholder="AAPL MSFT GOOGL")
        peers = [s.strip().upper() for s in custom.split() if s.strip()] if custom else []

    if peers:
        symbols = [query] + peers
        rows = []
        for sym in symbols:
            try:
                i = fetch_info(sym)
                p = i.get("currentPrice") or i.get("regularMarketPrice")
                pv = i.get("previousClose")
                chg = ((p-pv)/pv*100) if p and pv else None
                rows.append({
                    "股票": sym,
                    "价格": f"${p:.2f}" if p else "N/A",
                    "涨跌幅": f"{'+'if(chg or 0)>=0 else ''}{chg:.2f}%" if chg else "N/A",
                    "市值": fmt(i.get("marketCap")),
                    "PE":  f"{i.get('trailingPE'):.1f}" if i.get("trailingPE") else "N/A",
                    "前瞻PE": f"{i.get('forwardPE'):.1f}" if i.get("forwardPE") else "N/A",
                    "净利率": fmt_pct(i.get("profitMargins"), 100),
                    "ROE":  fmt_pct(i.get("returnOnEquity"), 100),
                })
            except Exception:
                rows.append({"股票": sym, "价格": "获取失败"})

        df_peer = pd.DataFrame(rows).set_index("股票")
        st.dataframe(df_peer, use_container_width=True)
        st.download_button("⬇️ 导出同行对比 Excel",
                           data=to_excel({"同行对比": df_peer.reset_index()}),
                           file_name=f"{query}_peers.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        market_caps = {}
        for r in rows:
            sym = r["股票"]
            try:
                mc = fetch_info(sym).get("marketCap")
                if mc: market_caps[sym] = mc / 1e9
            except Exception:
                pass
        if market_caps:
            fig_peer = go.Figure(go.Bar(
                x=list(market_caps.keys()), y=list(market_caps.values()),
                marker_color=["#3498db" if k==query else "#95a5a6" for k in market_caps],
                text=[f"{v:.0f}B" for v in market_caps.values()], textposition="outside"))
            fig_peer.update_layout(title="市值对比（十亿美元）", height=300,
                                    margin=dict(l=0,r=0,t=40,b=0),
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_peer, use_container_width=True)

# ── 相关性分析 ────────────────────────────────
with tab_corr:
    st.subheader("多股票价格相关性")
    default_syms = " ".join(PEERS.get(query, [query]))
    corr_input = st.text_input("输入股票代码（空格分隔，含当前股票）",
                                value=f"{query} {default_syms}")
    corr_syms = list(dict.fromkeys([s.upper() for s in corr_input.split() if s.strip()]))[:8]

    if len(corr_syms) >= 2:
        with st.spinner("加载历史数据..."):
            closes = {}
            for sym in corr_syms:
                try:
                    h = fetch_history(sym, "1y")
                    if not h.empty:
                        closes[sym] = h["Close"]
                except Exception:
                    pass
        if closes:
            df_close = pd.DataFrame(closes).dropna()
            corr = df_close.corr()
            fig_corr = px.imshow(corr, text_auto=".2f",
                                  color_continuous_scale="RdYlGn", zmin=-1, zmax=1,
                                  title="1年价格相关性")
            fig_corr.update_layout(height=400, margin=dict(l=0,r=0,t=40,b=0),
                                    paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_corr, use_container_width=True)
            st.caption("1=完全同向  0=无关  -1=完全反向。相关性低的股票组合有助于分散风险。")

            # 归一化走势对比
            df_norm = df_close / df_close.iloc[0] * 100
            fig_norm = go.Figure()
            for col in df_norm.columns:
                fig_norm.add_trace(go.Scatter(x=df_norm.index, y=df_norm[col], name=col))
            fig_norm.update_layout(title="归一化价格走势对比（基准=100）", height=350,
                                    margin=dict(l=0,r=0,t=40,b=0),
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_norm, use_container_width=True)

# ── 财报 ─────────────────────────────────────
with tab_fin:
    income, balance = fetch_financials(query)
    ann_income, ann_balance, ann_cashflow = fetch_annual_financials(query)

    # 季度
    if income is not None and not income.empty:
        st.subheader("季度利润表")
        cols = income.columns[:5]
        col_labels = [str(c)[:7] for c in cols]
        rows_i = [("营收","Total Revenue"),("毛利润","Gross Profit"),
                  ("营业利润","Operating Income"),("净利润","Net Income")]
        records = {lbl:[fmt(v) for v in income.loc[key][cols]]
                   for lbl,key in rows_i if key in income.index}
        df_q = pd.DataFrame(records, index=col_labels).T
        st.dataframe(df_q, use_container_width=True)

    # 年度趋势图
    if ann_income is not None and not ann_income.empty:
        st.subheader("年度营收与利润趋势")
        rev_rows = ["Total Revenue","Gross Profit","Net Income"]
        fig_ann = go.Figure()
        colors_ann = ["#3498db","#26a69a","#e74c3c"]
        for row, color in zip(rev_rows, colors_ann):
            if row in ann_income.index:
                data = ann_income.loc[row].iloc[:5][::-1]
                fig_ann.add_trace(go.Scatter(
                    x=[str(d)[:4] for d in data.index],
                    y=data.values/1e9, name=row.replace("Total ",""),
                    mode="lines+markers", line=dict(color=color, width=2)))
        fig_ann.update_layout(height=320, yaxis_title="十亿美元",
                               margin=dict(l=0,r=0,t=10,b=0),
                               paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_ann, use_container_width=True)

    # 年度资产负债
    if ann_balance is not None and not ann_balance.empty:
        st.subheader("年度资产负债表")
        cols_b = ann_balance.columns[:5]
        col_labels_b = [str(c)[:4] for c in cols_b]
        rows_b = [("总资产","Total Assets"),
                  ("总负债","Total Liabilities Net Minority Interest"),
                  ("股东权益","Stockholders Equity"),
                  ("现金及等价物","Cash And Cash Equivalents")]
        records_b = {lbl:[fmt(v) for v in ann_balance.loc[key][cols_b]]
                     for lbl,key in rows_b if key in ann_balance.index}
        df_b = pd.DataFrame(records_b, index=col_labels_b).T
        st.dataframe(df_b, use_container_width=True)

    # 导出
    export_sheets = {}
    if income is not None and not income.empty:
        export_sheets["季度利润表"] = income
    if ann_income is not None and not ann_income.empty:
        export_sheets["年度利润表"] = ann_income
    if ann_balance is not None and not ann_balance.empty:
        export_sheets["年度资产负债"] = ann_balance
    if export_sheets:
        st.download_button("⬇️ 导出财报 Excel",
                           data=to_excel(export_sheets),
                           file_name=f"{query}_financials.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ── 财报日历 ──────────────────────────────────
with tab_cal:
    st.subheader("财报日历")
    cal = fetch_calendar(query)
    if cal is not None:
        try:
            if isinstance(cal, dict):
                earn_date = cal.get("Earnings Date")
                eps_est   = cal.get("EPS Estimate")
                rev_est   = cal.get("Revenue Estimate")
                if earn_date:
                    d = str(earn_date[0])[:10] if hasattr(earn_date,'__iter__') else str(earn_date)[:10]
                    st.metric("下次财报日期", d)
                if eps_est:
                    st.metric("EPS 预期", f"${eps_est:.2f}" if isinstance(eps_est,float) else str(eps_est))
                if rev_est:
                    st.metric("营收预期", fmt(rev_est) if isinstance(rev_est,(int,float)) else str(rev_est))
            else:
                st.dataframe(cal, use_container_width=True)
        except Exception as e:
            st.info(f"财报日历数据解析失败: {e}")
    else:
        st.info("暂无财报日历数据")

    if wl:
        st.subheader("自选股财报日期")
        for sym in wl:
            try:
                c = fetch_calendar(sym)
                if c and isinstance(c, dict):
                    ed = c.get("Earnings Date")
                    if ed:
                        date_str = str(ed[0])[:10] if hasattr(ed,'__iter__') else str(ed)[:10]
                        st.write(f"**{sym}** — {date_str}")
            except Exception:
                pass

# ── 期权 ─────────────────────────────────────
with tab_opt:
    st.subheader("期权数据")
    dates = fetch_options_dates(query)
    if dates:
        selected_date = st.selectbox("到期日", dates[:8])
        calls, puts = fetch_options_chain(query, selected_date)
        if calls is not None:
            col_opt1, col_opt2 = st.columns(2)
            with col_opt1:
                st.write("**看涨期权 (Calls)**")
                df_calls = calls[["strike","lastPrice","bid","ask","volume","openInterest","impliedVolatility"]].head(15).copy()
                df_calls.columns = ["行权价","最新价","买价","卖价","成交量","持仓量","隐波"]
                df_calls["隐波"] = df_calls["隐波"].apply(lambda x: f"{x*100:.1f}%")
                st.dataframe(df_calls, use_container_width=True)
            with col_opt2:
                st.write("**看跌期权 (Puts)**")
                df_puts = puts[["strike","lastPrice","bid","ask","volume","openInterest","impliedVolatility"]].head(15).copy()
                df_puts.columns = ["行权价","最新价","买价","卖价","成交量","持仓量","隐波"]
                df_puts["隐波"] = df_puts["隐波"].apply(lambda x: f"{x*100:.1f}%")
                st.dataframe(df_puts, use_container_width=True)
    else:
        st.info("暂无期权数据")

# ── 机构持仓 ──────────────────────────────────
with tab_inst:
    st.subheader("机构持仓")
    inst, mf = fetch_institutions(query)
    if inst is not None and not inst.empty:
        st.write("**主要机构持仓**")
        st.dataframe(inst.head(15), use_container_width=True)

        # 柱状图
        if "% Out" in inst.columns and "Holder" in inst.columns:
            top = inst.head(10)
            fig_inst = go.Figure(go.Bar(
                x=top["% Out"], y=top["Holder"], orientation="h",
                marker_color="#3498db"))
            fig_inst.update_layout(title="持仓占比前10机构", height=300,
                                    xaxis_title="持股比例",
                                    margin=dict(l=0,r=0,t=40,b=0),
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_inst, use_container_width=True)
    else:
        st.info("暂无机构持仓数据")

    if mf is not None and not mf.empty:
        st.write("**主要基金持仓**")
        st.dataframe(mf.head(10), use_container_width=True)

# ── 内部人交易 ────────────────────────────────
with tab_insider:
    st.subheader("内部人交易记录")
    st.caption("公司高管、董事等内部人士的买卖记录（来自 SEC 申报）")
    insider_df = fetch_insider(query)
    if insider_df is not None and not insider_df.empty:
        st.dataframe(insider_df.head(20), use_container_width=True)

        # 统计买卖方向
        if "Transaction" in insider_df.columns:
            buy_cnt  = insider_df["Transaction"].str.contains("Buy|Purchase", case=False, na=False).sum()
            sell_cnt = insider_df["Transaction"].str.contains("Sell|Sale", case=False, na=False).sum()
            c1, c2 = st.columns(2)
            c1.metric("内部人买入次数", buy_cnt)
            c2.metric("内部人卖出次数", sell_cnt)

        st.download_button("⬇️ 导出内部人交易 Excel",
                           data=to_excel({"内部人交易": insider_df}),
                           file_name=f"{query}_insider.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("暂无内部人交易数据")

# ── 股息历史 ──────────────────────────────────
with tab_div:
    st.subheader("股息历史")
    div_hist = fetch_dividends(query)
    if div_hist is not None and len(div_hist) > 0:
        df_div = div_hist.reset_index()
        df_div.columns = ["日期", "每股股息"]
        df_div["年份"] = df_div["日期"].dt.year

        # 年度股息总额
        ann_div = df_div.groupby("年份")["每股股息"].sum().reset_index()
        ann_div.columns = ["年份", "年度股息"]

        fig_div = go.Figure()
        fig_div.add_trace(go.Bar(x=ann_div["年份"], y=ann_div["年度股息"],
                                  marker_color="#26a69a",
                                  text=[f"${v:.2f}" for v in ann_div["年度股息"]],
                                  textposition="outside"))
        fig_div.update_layout(title="年度每股股息", height=300, yaxis_title="美元",
                               margin=dict(l=0,r=0,t=40,b=0),
                               paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_div, use_container_width=True)
        st.dataframe(df_div.sort_values("日期", ascending=False).head(20),
                     use_container_width=True)
    else:
        st.info("该股票暂无股息记录（可能不派息）")

# ── 做空数据 ──────────────────────────────────
with tab_short:
    st.subheader("做空数据")
    short_ratio  = info.get("shortRatio")
    short_float  = info.get("shortPercentOfFloat")
    shares_short = info.get("sharesShort")
    shares_total = info.get("floatShares")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("空头比例（流通）", fmt_pct(short_float, 100) if short_float else "N/A")
    c2.metric("空头回补天数",    f"{short_ratio:.1f} 天" if short_ratio else "N/A")
    c3.metric("做空股数",        fmt(shares_short))
    c4.metric("流通股数",        fmt(shares_total))

    if short_float:
        pct = short_float * 100
        if pct > 20:
            st.warning(f"⚠️ 空头比例 {pct:.1f}% 较高，存在轧空（Short Squeeze）机会，但风险也较大")
        elif pct > 10:
            st.info(f"ℹ️ 空头比例 {pct:.1f}%，属于中等水平")
        else:
            st.success(f"✅ 空头比例 {pct:.1f}%，做空压力较小")

    if short_ratio:
        if short_ratio > 10:
            st.warning(f"⚠️ 空头回补需 {short_ratio:.1f} 天，回补压力较大，轧空风险高")
        elif short_ratio > 5:
            st.info(f"ℹ️ 空头回补需 {short_ratio:.1f} 天，中等水平")
        else:
            st.success(f"✅ 空头回补仅需 {short_ratio:.1f} 天，做空压力较小")

# ── 投资组合 ──────────────────────────────────
with tab_port:
    st.subheader("我的投资组合")
    portfolio = load_json(PORTFOLIO_FILE, {})

    with st.form("add_position"):
        pc1, pc2, pc3 = st.columns(3)
        p_sym  = pc1.text_input("股票代码").strip().upper()
        p_qty  = pc2.number_input("持仓数量", min_value=0.0, step=1.0)
        p_cost = pc3.number_input("买入均价 ($)", min_value=0.0, step=0.01)
        if st.form_submit_button("添加/更新"):
            if p_sym and p_qty > 0 and p_cost > 0:
                portfolio[p_sym] = {"qty": p_qty, "cost": p_cost}
                save_json(PORTFOLIO_FILE, portfolio)
                st.rerun()

    if portfolio:
        rows_p, total_cost, total_val = [], 0, 0
        for sym, pos in portfolio.items():
            try:
                i = fetch_info(sym)
                p = i.get("currentPrice") or i.get("regularMarketPrice") or pos["cost"]
                cost_total = pos["qty"] * pos["cost"]
                val_total  = pos["qty"] * p
                pnl        = val_total - cost_total
                pnl_pct    = (pnl / cost_total * 100) if cost_total else 0
                total_cost += cost_total
                total_val  += val_total
                rows_p.append({
                    "股票": sym, "数量": pos["qty"],
                    "买入价": f"${pos['cost']:.2f}", "现价": f"${p:.2f}",
                    "持仓市值": f"${val_total:,.2f}",
                    "盈亏": f"{'+'if pnl>=0 else ''}${pnl:,.2f}",
                    "盈亏%": f"{'+'if pnl_pct>=0 else ''}{pnl_pct:.2f}%",
                })
            except Exception:
                rows_p.append({"股票": sym, "数量": pos["qty"],
                                "买入价": f"${pos['cost']:.2f}", "现价": "N/A"})

        total_pnl     = total_val - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
        m1, m2, m3 = st.columns(3)
        m1.metric("总持仓市值", f"${total_val:,.2f}")
        m2.metric("总成本",     f"${total_cost:,.2f}")
        m3.metric("总盈亏", f"{'+'if total_pnl>=0 else ''}${total_pnl:,.2f}",
                  f"{'+'if total_pnl_pct>=0 else ''}{total_pnl_pct:.2f}%")

        df_port = pd.DataFrame(rows_p).set_index("股票")
        st.dataframe(df_port, use_container_width=True)

        st.download_button("⬇️ 导出持仓 Excel",
                           data=to_excel({"投资组合": df_port.reset_index()}),
                           file_name="portfolio.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        pie_data = {r["股票"]: float(r["持仓市值"].replace("$","").replace(",",""))
                    for r in rows_p if "持仓市值" in r}
        if pie_data:
            fig_pie = go.Figure(go.Pie(labels=list(pie_data.keys()),
                                        values=list(pie_data.values()), hole=0.4))
            fig_pie.update_layout(height=300, margin=dict(l=0,r=0,t=10,b=0),
                                   paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_pie, use_container_width=True)

        del_sym = st.selectbox("删除持仓", [""] + list(portfolio.keys()))
        if del_sym and st.button("确认删除"):
            del portfolio[del_sym]
            save_json(PORTFOLIO_FILE, portfolio)
            st.rerun()
    else:
        st.info("还没有持仓，在上方表单添加。")

# ── 价格提醒 ──────────────────────────────────
with tab_alert:
    st.subheader("价格提醒")
    st.caption("当股价达到目标价或跌破止损价时，页面顶部会显示提醒。")

    with st.form("add_alert"):
        al1, al2, al3 = st.columns(3)
        a_sym  = al1.text_input("股票代码", value=query).strip().upper()
        a_high = al2.number_input("目标价 ($)（可留0）", min_value=0.0, step=0.01)
        a_low  = al3.number_input("止损价 ($)（可留0）", min_value=0.0, step=0.01)
        if st.form_submit_button("设置提醒"):
            if a_sym:
                alerts[a_sym] = {
                    "high": a_high if a_high > 0 else None,
                    "low":  a_low  if a_low  > 0 else None,
                }
                save_json(ALERTS_FILE, alerts)
                st.success(f"已设置 {a_sym} 的价格提醒")

    if alerts:
        st.write("**当前提醒设置**")
        alert_rows = [{"股票": s,
                        "目标价": f"${r['high']}" if r.get("high") else "—",
                        "止损价": f"${r['low']}"  if r.get("low")  else "—"}
                      for s, r in alerts.items()]
        st.dataframe(pd.DataFrame(alert_rows).set_index("股票"), use_container_width=True)

        del_alert = st.selectbox("删除提醒", [""] + list(alerts.keys()))
        if del_alert and st.button("确认删除提醒"):
            del alerts[del_alert]
            save_json(ALERTS_FILE, alerts)
            st.rerun()

# ── 新闻 ─────────────────────────────────────
with tab_news:
    st.subheader(f"{query} 最新新闻")
    with st.spinner("加载新闻..."):
        news_items = fetch_news(query)
    if news_items:
        for item in news_items:
            if item["link"]:
                st.markdown(f"**[{item['title']}]({item['link']})**  "
                            f"<span style='color:#888;font-size:12px'>{item['pub']}</span>",
                            unsafe_allow_html=True)
            else:
                st.markdown(f"**{item['title']}**  "
                            f"<span style='color:#888;font-size:12px'>{item['pub']}</span>",
                            unsafe_allow_html=True)
            st.divider()
    else:
        st.info("暂无新闻")
