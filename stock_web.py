import json
import os
import warnings
import urllib3
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import datetime

warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.json")

# ── 页面配置 ──────────────────────────────────
st.set_page_config(
    page_title="美股看板",
    page_icon="📈",
    layout="wide",
)

st.markdown("""
<style>
.metric-card {
    background: #1e2130;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 8px;
}
.up   { color: #26a69a; font-weight: bold; }
.down { color: #ef5350; font-weight: bold; }
.tag  { font-size: 12px; color: #888; }
</style>
""", unsafe_allow_html=True)

# ── 工具函数 ──────────────────────────────────

def fmt(n):
    if n is None or (isinstance(n, float) and pd.isna(n)):
        return "N/A"
    if abs(n) >= 1e12: return f"{n/1e12:.2f}T"
    if abs(n) >= 1e9:  return f"{n/1e9:.2f}B"
    if abs(n) >= 1e6:  return f"{n/1e6:.2f}M"
    return f"{n:,.0f}"

def fmt_pct(v, scale=1):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "N/A"
    return f"{v * scale:.2f}%"

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE) as f:
            return json.load(f)
    return []

def save_watchlist(lst):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(lst, f)

@st.cache_data(ttl=300, show_spinner=False)
def fetch_info(symbol):
    t = yf.Ticker(symbol)
    return t.info

@st.cache_data(ttl=300, show_spinner=False)
def fetch_history(symbol, period):
    return yf.Ticker(symbol).history(period=period)

@st.cache_data(ttl=300, show_spinner=False)
def fetch_financials(symbol):
    t = yf.Ticker(symbol)
    return t.quarterly_income_stmt, t.quarterly_balance_sheet

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
            link  = (item.findtext("link") or "").strip()
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
        news = yf.Ticker(symbol).news or []
        return [{"title": n.get("content", {}).get("title", ""), "link": "", "pub": ""} for n in news[:8]]

# ── 侧边栏 ────────────────────────────────────

with st.sidebar:
    st.title("📈 美股看板")

    # 搜索框
    query = st.text_input("输入股票代码", placeholder="如 AAPL、TSLA、NVDA").strip().upper()

    st.divider()

    # 自选股管理
    st.subheader("⭐ 自选股")
    wl = load_watchlist()

    new_sym = st.text_input("添加自选股", placeholder="输入代码回车").strip().upper()
    if new_sym and new_sym not in wl:
        wl.append(new_sym)
        save_watchlist(wl)
        st.rerun()

    if wl:
        for sym in wl:
            col1, col2 = st.columns([4, 1])
            with col1:
                if st.button(sym, key=f"wl_{sym}", use_container_width=True):
                    query = sym
            with col2:
                if st.button("✕", key=f"rm_{sym}"):
                    wl.remove(sym)
                    save_watchlist(wl)
                    st.rerun()
    else:
        st.caption("暂无自选股")

    st.divider()
    period_map = {"1个月": "1mo", "3个月": "3mo", "6个月": "6mo", "1年": "1y", "2年": "2y"}
    period_label = st.radio("K线周期", list(period_map.keys()), index=3)
    period = period_map[period_label]

# ── 主内容区 ──────────────────────────────────

if not query:
    # 欢迎页：自选股概览
    st.title("美股信息看板")
    if not wl:
        st.info("在左侧添加自选股，或在搜索框输入股票代码开始查询。")
    else:
        st.subheader("自选股概览")
        cols = st.columns(min(len(wl), 4))
        for i, sym in enumerate(wl):
            with cols[i % 4]:
                try:
                    info = fetch_info(sym)
                    price = info.get("currentPrice") or info.get("regularMarketPrice")
                    prev  = info.get("previousClose")
                    chg_pct = ((price - prev) / prev * 100) if price and prev else None
                    sign  = "+" if (chg_pct or 0) >= 0 else ""
                    color = "up" if (chg_pct or 0) >= 0 else "down"
                    arrow = "▲" if (chg_pct or 0) > 0 else "▼"
                    st.metric(
                        label=f"{sym}",
                        value=f"${price:.2f}" if price else "N/A",
                        delta=f"{sign}{chg_pct:.2f}%" if chg_pct else None,
                    )
                except Exception:
                    st.metric(sym, "获取失败")
    st.stop()

# ── 股票详情页 ────────────────────────────────

with st.spinner(f"正在获取 {query} 数据..."):
    info = fetch_info(query)

if not info or not info.get("quoteType"):
    st.error(f"未找到股票代码：{query}")
    st.stop()

price    = info.get("currentPrice") or info.get("regularMarketPrice")
prev     = info.get("previousClose")
change   = (price - prev) if price and prev else None
chg_pct  = (change / prev * 100) if change and prev else None
sign     = "+" if (chg_pct or 0) >= 0 else ""

# 标题行
st.title(f"{info.get('shortName', query)}  `{query}`")
st.caption(f"{info.get('sector', '')}  ·  {info.get('industry', '')}")

# 价格指标行
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("当前价格", f"${price:.2f}" if price else "N/A",
          f"{sign}{chg_pct:.2f}%" if chg_pct else None)
c2.metric("今日区间", f"${info.get('dayLow','?')} ~ ${info.get('dayHigh','?')}", delta_color="off")
c3.metric("52周区间", f"${info.get('fiftyTwoWeekLow','?')} ~ ${info.get('fiftyTwoWeekHigh','?')}", delta_color="off")
c4.metric("成交量", fmt(info.get("volume")))
c5.metric("市值", fmt(info.get("marketCap")))

st.divider()

# ── K线图 ─────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📊 K线走势", "📋 基本面", "💰 财报", "📰 新闻"])

with tab1:
    hist = fetch_history(query, period)
    if not hist.empty:
        fig = go.Figure(data=[go.Candlestick(
            x=hist.index,
            open=hist["Open"], high=hist["High"],
            low=hist["Low"],   close=hist["Close"],
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            name="K线",
        )])
        # 均线
        hist["MA20"] = hist["Close"].rolling(20).mean()
        hist["MA60"] = hist["Close"].rolling(60).mean()
        fig.add_trace(go.Scatter(x=hist.index, y=hist["MA20"], name="MA20",
                                  line=dict(color="#f39c12", width=1)))
        fig.add_trace(go.Scatter(x=hist.index, y=hist["MA60"], name="MA60",
                                  line=dict(color="#3498db", width=1)))
        fig.update_layout(
            xaxis_rangeslider_visible=False,
            height=420,
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=1.05),
        )
        st.plotly_chart(fig, use_container_width=True)

        # 成交量柱状图
        colors = ["#26a69a" if c >= o else "#ef5350"
                  for c, o in zip(hist["Close"], hist["Open"])]
        fig2 = go.Figure(go.Bar(x=hist.index, y=hist["Volume"], marker_color=colors))
        fig2.update_layout(height=120, margin=dict(l=0, r=0, t=0, b=0),
                           paper_bgcolor="rgba(0,0,0,0)",
                           plot_bgcolor="rgba(0,0,0,0)",
                           showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.warning("历史数据获取失败")

# ── 基本面 ────────────────────────────────────
with tab2:
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("估值指标")
        pe  = info.get("trailingPE")
        fpe = info.get("forwardPE")
        pb  = info.get("priceToBook")
        ps  = info.get("priceToSalesTrailing12Months")
        data_val = {
            "市盈率 PE（TTM）": f"{pe:.2f}" if pe else "N/A",
            "前瞻市盈率":       f"{fpe:.2f}" if fpe else "N/A",
            "市净率 PB":       f"{pb:.2f}" if pb else "N/A",
            "市销率 PS":       f"{ps:.2f}" if ps else "N/A",
            "每股收益 EPS":     f"${info.get('trailingEps'):.2f}" if info.get("trailingEps") else "N/A",
            "Beta":           f"{info.get('beta'):.2f}" if info.get("beta") else "N/A",
        }
        st.table(pd.DataFrame.from_dict(data_val, orient="index", columns=["数值"]))

        st.subheader("分析师评级")
        rec = info.get("recommendationKey", "N/A").replace("_", " ").title()
        target_mean = info.get("targetMeanPrice")
        target_low  = info.get("targetLowPrice")
        target_high = info.get("targetHighPrice")
        n_analyst   = info.get("numberOfAnalystOpinions")
        upside = ((target_mean - price) / price * 100) if target_mean and price else None

        st.metric("综合评级", rec, f"{n_analyst} 位分析师" if n_analyst else None)
        if target_mean:
            c1b, c2b, c3b = st.columns(3)
            c1b.metric("目标价（均）", f"${target_mean:.2f}")
            c2b.metric("目标价（低）", f"${target_low:.2f}" if target_low else "N/A")
            c3b.metric("目标价（高）", f"${target_high:.2f}" if target_high else "N/A")
        if upside is not None:
            sign_u = "+" if upside >= 0 else ""
            st.metric("潜在涨幅", f"{sign_u}{upside:.1f}%")

    with col_r:
        st.subheader("盈利能力")
        margin  = info.get("profitMargins")
        roe     = info.get("returnOnEquity")
        roa     = info.get("returnOnAssets")
        gross_m = info.get("grossMargins")
        op_m    = info.get("operatingMargins")
        data_profit = {
            "毛利率":   fmt_pct(gross_m, 100),
            "营业利润率": fmt_pct(op_m, 100),
            "净利率":   fmt_pct(margin, 100),
            "ROE":    fmt_pct(roe, 100),
            "ROA":    fmt_pct(roa, 100),
        }
        st.table(pd.DataFrame.from_dict(data_profit, orient="index", columns=["数值"]))

        st.subheader("股息 & 其他")
        div = info.get("trailingAnnualDividendYield") or info.get("dividendYield")
        if div and div > 1:
            div = div / 100
        data_div = {
            "股息率":   fmt_pct(div, 100) if div else "N/A",
            "每股股息":  f"${info.get('dividendRate'):.2f}" if info.get("dividendRate") else "N/A",
            "52周高":   f"${info.get('fiftyTwoWeekHigh','N/A')}",
            "52周低":   f"${info.get('fiftyTwoWeekLow','N/A')}",
            "均量(3M)": fmt(info.get("averageVolume")),
        }
        st.table(pd.DataFrame.from_dict(data_div, orient="index", columns=["数值"]))

# ── 财报 ─────────────────────────────────────
with tab3:
    income, balance = fetch_financials(query)

    if income is not None and not income.empty:
        st.subheader("季度利润表")
        rows_i = ["Total Revenue", "Gross Profit", "Operating Income", "Net Income"]
        labels_i = ["营收", "毛利润", "营业利润", "净利润"]
        cols = income.columns[:5]
        col_labels = [str(c)[:7] for c in cols]
        records = {}
        for label, key in zip(labels_i, rows_i):
            if key in income.index:
                records[label] = [fmt(v) for v in income.loc[key][cols]]
        df_i = pd.DataFrame(records, index=col_labels).T
        st.dataframe(df_i, use_container_width=True)

        # 营收趋势图
        if "Total Revenue" in income.index:
            rev = income.loc["Total Revenue"].iloc[:8][::-1]
            net = income.loc["Net Income"].iloc[:8][::-1] if "Net Income" in income.index else None
            fig3 = go.Figure()
            fig3.add_trace(go.Bar(x=[str(d)[:7] for d in rev.index], y=rev.values/1e9,
                                   name="营收(B)", marker_color="#3498db"))
            if net is not None:
                fig3.add_trace(go.Bar(x=[str(d)[:7] for d in net.index], y=net.values/1e9,
                                       name="净利润(B)", marker_color="#26a69a"))
            fig3.update_layout(barmode="group", height=280,
                                margin=dict(l=0, r=0, t=10, b=0),
                                paper_bgcolor="rgba(0,0,0,0)",
                                plot_bgcolor="rgba(0,0,0,0)",
                                yaxis_title="十亿美元")
            st.plotly_chart(fig3, use_container_width=True)

    if balance is not None and not balance.empty:
        st.subheader("季度资产负债表")
        rows_b = ["Total Assets", "Total Liabilities Net Minority Interest",
                  "Stockholders Equity", "Cash And Cash Equivalents"]
        labels_b = ["总资产", "总负债", "股东权益", "现金及等价物"]
        cols_b = balance.columns[:5]
        col_labels_b = [str(c)[:7] for c in cols_b]
        records_b = {}
        for label, key in zip(labels_b, rows_b):
            if key in balance.index:
                records_b[label] = [fmt(v) for v in balance.loc[key][cols_b]]
        df_b = pd.DataFrame(records_b, index=col_labels_b).T
        st.dataframe(df_b, use_container_width=True)

# ── 新闻 ─────────────────────────────────────
with tab4:
    st.subheader(f"{query} 最新新闻")
    with st.spinner("加载新闻..."):
        news_items = fetch_news(query)
    if news_items:
        for item in news_items:
            title = item["title"]
            link  = item["link"]
            pub   = item["pub"]
            if link:
                st.markdown(f"**[{title}]({link})**  <span style='color:#888;font-size:12px'>{pub}</span>",
                            unsafe_allow_html=True)
            else:
                st.markdown(f"**{title}**  <span style='color:#888;font-size:12px'>{pub}</span>",
                            unsafe_allow_html=True)
            st.divider()
    else:
        st.info("暂无新闻")
