"""
Indian Market Morning Summary — Telegram Bot
=============================================
Fetches, before market open:
  1. Domestic snapshot (Nifty 50 / Sensex / Bank Nifty previous close)
  2. Global overnight cues (US & Asian indices, crude, USD/INR)
  3. Sector-wise performance (Bank, IT, Auto, Pharma, FMCG, Metal, Energy, Realty)
  4. FII/DII institutional net flows (provisional, ₹ crore)
  5. Full-market top gainers/losers across ALL ~2000 NSE-listed equities
     (via NSE's official daily Bhavcopy file)
  6. Unusual trading-volume spikes (vs the prior session)
  7. New 52-week high/low breakouts (among the day's most liquid stocks)
  8. Nifty 50 top gainers & losers (smaller, faster, always-on baseline)
  9. Market-moving news headlines (RSS, no API key needed)
  10. A curated economic-events watchlist for the week

...and sends it as one formatted message to a Telegram chat.

Run manually:   python bot.py
Run on schedule: see .github/workflows/morning-summary.yml
"""

import os
import sys
import io
import zipfile
import datetime
import html
import requests
import feedparser
import pandas as pd
import yfinance as yf

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# NSE's servers block requests that don't look like a real browser.
NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Indices & macro tickers (all free on Yahoo Finance, no key needed)
GLOBAL_CUES = {
    "Dow Jones": "^DJI",
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Nikkei 225": "^N225",
    "Hang Seng": "^HSI",
    "Brent Crude": "BZ=F",
    "USD/INR": "INR=X",
    "Dollar Index": "DX-Y.NYB",
}

DOMESTIC_INDICES = {
    "Nifty 50": "^NSEI",
    "Sensex": "^BSESN",
    "Bank Nifty": "^NSEBANK",
}

# Major NSE sectoral indices — shows *where* the day's action is concentrated.
SECTOR_INDICES = {
    "Nifty Bank": "^NSEBANK",
    "Nifty IT": "^CNXIT",
    "Nifty Auto": "^CNXAUTO",
    "Nifty Pharma": "^CNXPHARMA",
    "Nifty FMCG": "^CNXFMCG",
    "Nifty Metal": "^CNXMETAL",
    "Nifty Energy": "^CNXENERGY",
    "Nifty Realty": "^CNXREALTY",
}

# Nifty 50 constituents (NSE tickers, .NS suffix for Yahoo Finance)
NIFTY50 = [
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS",
    "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BEL.NS", "BHARTIARTL.NS",
    "CIPLA.NS", "COALINDIA.NS", "DRREDDY.NS", "EICHERMOT.NS", "GRASIM.NS",
    "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS",
    "HINDUNILVR.NS", "ICICIBANK.NS", "INDUSINDBK.NS", "INFY.NS", "ITC.NS",
    "JSWSTEEL.NS", "KOTAKBANK.NS", "LT.NS", "M&M.NS", "MARUTI.NS",
    "NESTLEIND.NS", "NTPC.NS", "ONGC.NS", "POWERGRID.NS", "RELIANCE.NS",
    "SBILIFE.NS", "SBIN.NS", "SHRIRAMFIN.NS", "SUNPHARMA.NS", "TATACONSUM.NS",
    "TMPV.NS", "TATASTEEL.NS", "TCS.NS", "TECHM.NS", "TITAN.NS",
    "TRENT.NS", "ULTRACEMCO.NS", "UPL.NS", "WIPRO.NS", "BPCL.NS",
]

# Indian financial-news RSS feeds (no API key needed)
NEWS_FEEDS = {
    "Economic Times Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
    "Livemint Markets": "https://www.livemint.com/rss/markets",
}

MAX_HEADLINES = 8

# Recurring events worth flagging — extend this list as needed.
# (RBI MPC dates / Union Budget date change yearly; update at the start of each year.)
KEY_EVENTS_2026 = [
    ("2026-02-01", "Union Budget"),
    ("2026-02-06", "RBI MPC decision"),
    ("2026-04-08", "RBI MPC decision"),
    ("2026-06-05", "RBI MPC decision"),
    ("2026-08-06", "RBI MPC decision"),
    ("2026-10-01", "RBI MPC decision"),
    ("2026-12-04", "RBI MPC decision"),
]


# --------------------------------------------------------------------------
# Data fetchers (each wrapped in try/except so one failure doesn't kill the run)
# --------------------------------------------------------------------------

def fetch_quote_changes(tickers: dict) -> dict:
    """Return {label: (last_close, pct_change)} using last two daily closes."""
    results = {}
    symbols = list(tickers.values())
    try:
        data = yf.download(symbols, period="5d", interval="1d",
                            group_by="ticker", progress=False, threads=True)
    except Exception as e:
        print(f"[warn] batch download failed: {e}", file=sys.stderr)
        return results

    for label, sym in tickers.items():
        try:
            closes = data[sym]["Close"].dropna()
            if len(closes) < 2:
                continue
            last, prev = closes.iloc[-1], closes.iloc[-2]
            pct = (last - prev) / prev * 100
            results[label] = (last, pct)
        except Exception as e:
            print(f"[warn] could not process {label} ({sym}): {e}", file=sys.stderr)
    return results


def fetch_nifty_movers(top_n: int = 5):
    """Return (gainers, losers) lists of (ticker, pct_change) from the last session."""
    try:
        data = yf.download(NIFTY50, period="5d", interval="1d",
                            group_by="ticker", progress=False, threads=True)
    except Exception as e:
        print(f"[warn] nifty movers download failed: {e}", file=sys.stderr)
        return [], []

    changes = []
    for sym in NIFTY50:
        try:
            closes = data[sym]["Close"].dropna()
            if len(closes) < 2:
                continue
            last, prev = closes.iloc[-1], closes.iloc[-2]
            pct = (last - prev) / prev * 100
            changes.append((sym.replace(".NS", ""), pct))
        except Exception:
            continue

    changes.sort(key=lambda x: x[1], reverse=True)
    gainers = changes[:top_n]
    losers = changes[-top_n:][::-1] if len(changes) >= top_n else []
    return gainers, losers


def fetch_news(max_items: int = MAX_HEADLINES):
    """Pull latest headlines from Indian market RSS feeds."""
    items = []
    for source, url in NEWS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                items.append({
                    "source": source,
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", ""),
                })
        except Exception as e:
            print(f"[warn] feed failed ({source}): {e}", file=sys.stderr)

    # de-duplicate by title, preserve order
    seen, deduped = set(), []
    for it in items:
        key = it["title"].lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(it)
    return deduped[:max_items]


def upcoming_events(days_ahead: int = 7):
    today = datetime.date.today()
    upcoming = []
    for date_str, label in KEY_EVENTS_2026:
        try:
            d = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        delta = (d - today).days
        if 0 <= delta <= days_ahead:
            upcoming.append((d, label))
    upcoming.sort()
    return upcoming


# --------------------------------------------------------------------------
# Full-market data (NSE official Bhavcopy — covers every listed equity)
# --------------------------------------------------------------------------

def _download_bhavcopy(date: datetime.date):
    """Try to download+parse the NSE UDiFF Bhavcopy for one calendar date.
    Returns a DataFrame (equity series only) or None if unavailable
    (weekends/holidays/not-yet-published all return None — this is expected,
    not an error)."""
    url = (
        "https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{date:%Y%m%d}_F_0000.csv.zip"
    )
    try:
        resp = requests.get(url, headers=NSE_HEADERS, timeout=30)
        if resp.status_code != 200 or len(resp.content) < 500:
            return None
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                df = pd.read_csv(f)
    except Exception as e:
        print(f"[warn] bhavcopy fetch failed for {date}: {e}", file=sys.stderr)
        return None

    # Keep plain equity shares only (drop debt instruments, ETFs noise etc.)
    try:
        if "SctySrs" in df.columns:
            df = df[df["SctySrs"].astype(str).str.strip() == "EQ"]
        elif "SERIES" in df.columns:  # older column naming, just in case
            df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
    except Exception:
        pass
    return df if len(df) > 0 else None


def fetch_latest_two_bhavcopies(max_lookback_days: int = 10):
    """Walk backward from yesterday to find the two most recent trading
    sessions' full-market Bhavcopy data. Returns (df_latest, df_prev), each
    possibly None if unavailable."""
    found = []
    d = datetime.date.today() - datetime.timedelta(days=1)
    tries = 0
    while len(found) < 2 and tries < max_lookback_days:
        df = _download_bhavcopy(d)
        if df is not None:
            found.append(df)
        d -= datetime.timedelta(days=1)
        tries += 1
    while len(found) < 2:
        found.append(None)
    return found[0], found[1]


def _col(df, *candidates):
    """Return the first matching column name present in df (UDiFF column
    names are all-caps in some exports, mixed-case in others)."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def compute_full_market_movers(df, top_n: int = 8, min_price: float = 10,
                                min_volume: int = 50_000):
    """Top gainers/losers across the ENTIRE NSE equity list (~2000 stocks),
    filtered to a minimum price & volume to screen out illiquid noise."""
    if df is None:
        return [], []
    try:
        sym_col = _col(df, "TckrSymb", "SYMBOL")
        close_col = _col(df, "ClsPric", "CLOSE_PRICE")
        prev_col = _col(df, "PrvsClsgPric", "PREV_CLOSE")
        vol_col = _col(df, "TtlTradgVol", "TTL_TRD_QNTY")
        if not all([sym_col, close_col, prev_col, vol_col]):
            return [], []

        d = df[[sym_col, close_col, prev_col, vol_col]].copy()
        d.columns = ["symbol", "close", "prev_close", "volume"]
        d = d[(d["prev_close"] > 0) & (d["close"] >= min_price) & (d["volume"] >= min_volume)]
        d["pct"] = (d["close"] - d["prev_close"]) / d["prev_close"] * 100
        d = d.sort_values("pct", ascending=False)

        gainers = list(d.head(top_n)[["symbol", "pct"]].itertuples(index=False, name=None))
        losers = list(d.tail(top_n)[["symbol", "pct"]].itertuples(index=False, name=None))[::-1]
        return gainers, losers
    except Exception as e:
        print(f"[warn] full-market movers computation failed: {e}", file=sys.stderr)
        return [], []


def compute_volume_spikes(df_latest, df_prev, top_n: int = 8,
                           min_volume: int = 100_000, ratio_threshold: float = 3.0):
    """Stocks trading at several times their previous session's volume —
    often an early signal that something (news, results) is moving them."""
    if df_latest is None or df_prev is None:
        return []
    try:
        sym_l, vol_l = _col(df_latest, "TckrSymb", "SYMBOL"), _col(df_latest, "TtlTradgVol", "TTL_TRD_QNTY")
        sym_p, vol_p = _col(df_prev, "TckrSymb", "SYMBOL"), _col(df_prev, "TtlTradgVol", "TTL_TRD_QNTY")
        if not all([sym_l, vol_l, sym_p, vol_p]):
            return []

        latest = df_latest[[sym_l, vol_l]].copy()
        latest.columns = ["symbol", "vol_latest"]
        prev = df_prev[[sym_p, vol_p]].copy()
        prev.columns = ["symbol", "vol_prev"]

        merged = latest.merge(prev, on="symbol", how="inner")
        merged = merged[(merged["vol_latest"] >= min_volume) & (merged["vol_prev"] > 0)]
        merged["ratio"] = merged["vol_latest"] / merged["vol_prev"]
        merged = merged[merged["ratio"] >= ratio_threshold].sort_values("ratio", ascending=False)

        return list(merged.head(top_n)[["symbol", "ratio"]].itertuples(index=False, name=None))
    except Exception as e:
        print(f"[warn] volume spike computation failed: {e}", file=sys.stderr)
        return []


def compute_52week_breakouts(df_latest, top_liquid_n: int = 250, top_n: int = 6):
    """52-week high/low breakouts. True full-market 52w tracking would mean
    pulling a year of history for ~2000 stocks, which is too slow/rate-limit
    -prone for a scheduled job — so this approximates it using the day's most
    liquid stocks by traded value, which is where breakouts matter most anyway."""
    if df_latest is None:
        return [], []
    try:
        sym_col = _col(df_latest, "TckrSymb", "SYMBOL")
        close_col = _col(df_latest, "ClsPric", "CLOSE_PRICE")
        val_col = _col(df_latest, "TtlTrfVal", "TURNOVER_LACS")
        if not all([sym_col, close_col, val_col]):
            return [], []

        d = df_latest[[sym_col, close_col, val_col]].copy()
        d.columns = ["symbol", "close", "value"]
        d = d.sort_values("value", ascending=False).head(top_liquid_n)

        tickers = [f"{s}.NS" for s in d["symbol"]]
        hist = yf.download(tickers, period="1y", interval="1d",
                            group_by="ticker", progress=False, threads=True)

        highs, lows = [], []
        for _, row in d.iterrows():
            sym, close = row["symbol"], row["close"]
            tkr = f"{sym}.NS"
            try:
                closes = hist[tkr]["Close"].dropna()
                if len(closes) < 30:
                    continue
                yr_high, yr_low = closes.max(), closes.min()
                if close >= yr_high * 0.995:
                    highs.append((sym, close, yr_high))
                elif close <= yr_low * 1.005:
                    lows.append((sym, close, yr_low))
            except Exception:
                continue

        highs.sort(key=lambda x: x[1], reverse=True)
        lows.sort(key=lambda x: x[1])
        return highs[:top_n], lows[:top_n]
    except Exception as e:
        print(f"[warn] 52-week breakout computation failed: {e}", file=sys.stderr)
        return [], []


# --------------------------------------------------------------------------
# FII/DII institutional flows
# --------------------------------------------------------------------------

def fetch_fii_dii():
    """Provisional net institutional cash-market flows (₹ crore), scraped
    from NSE's public data endpoint. NSE actively blocks non-browser traffic,
    so this visits the homepage first to pick up valid session cookies —
    the same trick real browsers rely on. This is the most fragile fetch in
    the bot and may need attention if NSE changes their anti-bot measures."""
    try:
        session = requests.Session()
        session.headers.update(NSE_HEADERS)
        session.get("https://www.nseindia.com/", timeout=15)  # sets cookies
        resp = session.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=15)
        if not resp.ok:
            return None
        data = resp.json()
        if not data:
            return None

        latest_date = data[0].get("date")
        result = {}
        for row in data:
            if row.get("date") != latest_date:
                continue
            category = str(row.get("category", "")).upper()
            key = "FII" if "FII" in category or "FPI" in category else (
                "DII" if "DII" in category else None)
            if key:
                result[key] = float(row.get("netValue", 0))
        return (latest_date, result) if result else None
    except Exception as e:
        print(f"[warn] FII/DII fetch failed: {e}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# Message formatting
# --------------------------------------------------------------------------

def fmt_pct(pct: float) -> str:
    arrow = "🟢▲" if pct >= 0 else "🔴▼"
    return f"{arrow} {pct:+.2f}%"


def build_message() -> str:
    today_str = datetime.date.today().strftime("%A, %d %B %Y")
    lines = [f"<b>📈 Market Morning Brief — {today_str}</b>", ""]

    # Domestic snapshot
    domestic = fetch_quote_changes(DOMESTIC_INDICES)
    if domestic:
        lines.append("<b>🇮🇳 Domestic (last close)</b>")
        for label, (last, pct) in domestic.items():
            lines.append(f"  {label}: {last:,.2f}  {fmt_pct(pct)}")
        lines.append("")

    # Global cues
    glob = fetch_quote_changes(GLOBAL_CUES)
    if glob:
        lines.append("<b>🌍 Global Cues</b>")
        for label, (last, pct) in glob.items():
            lines.append(f"  {label}: {last:,.2f}  {fmt_pct(pct)}")
        lines.append("")

    # Sector performance
    sectors = fetch_quote_changes(SECTOR_INDICES)
    if sectors:
        lines.append("<b>🏭 Sector Performance</b>")
        for label, (last, pct) in sorted(sectors.items(), key=lambda kv: kv[1][1], reverse=True):
            lines.append(f"  {label}: {fmt_pct(pct)}")
        lines.append("")

    # FII/DII flows
    fii_dii = fetch_fii_dii()
    if fii_dii:
        date_str_fd, flows = fii_dii
        lines.append(f"<b>🏦 FII/DII Flows (₹ cr, {date_str_fd})</b>")
        if "FII" in flows:
            lines.append(f"  FII/FPI: {flows['FII']:+,.0f}")
        if "DII" in flows:
            lines.append(f"  DII: {flows['DII']:+,.0f}")
        lines.append("")

    # Full-market data from NSE Bhavcopy (all ~2000 listed equities)
    bhav_latest, bhav_prev = fetch_latest_two_bhavcopies()

    fm_gainers, fm_losers = compute_full_market_movers(bhav_latest)
    if fm_gainers:
        lines.append("<b>🚀 Full Market Top Gainers (NSE, prev session)</b>")
        for sym, pct in fm_gainers:
            lines.append(f"  {sym}: {fmt_pct(pct)}")
        lines.append("")
    if fm_losers:
        lines.append("<b>📉 Full Market Top Losers (NSE, prev session)</b>")
        for sym, pct in fm_losers:
            lines.append(f"  {sym}: {fmt_pct(pct)}")
        lines.append("")

    spikes = compute_volume_spikes(bhav_latest, bhav_prev)
    if spikes:
        lines.append("<b>📊 Unusual Volume (vs prior session)</b>")
        for sym, ratio in spikes:
            lines.append(f"  {sym}: {ratio:.1f}x volume")
        lines.append("")

    breakout_highs, breakout_lows = compute_52week_breakouts(bhav_latest)
    if breakout_highs:
        lines.append("<b>🏔️ New 52-Week Highs</b>")
        for sym, close, yr_high in breakout_highs:
            lines.append(f"  {sym}: {close:,.2f} (52w high {yr_high:,.2f})")
        lines.append("")
    if breakout_lows:
        lines.append("<b>🕳️ New 52-Week Lows</b>")
        for sym, close, yr_low in breakout_lows:
            lines.append(f"  {sym}: {close:,.2f} (52w low {yr_low:,.2f})")
        lines.append("")

    # Movers (Nifty 50 only — smaller, faster, always-on baseline)
    gainers, losers = fetch_nifty_movers()
    if gainers:
        lines.append("<b>🚀 Nifty 50 Top Gainers (prev session)</b>")
        for tkr, pct in gainers:
            lines.append(f"  {tkr}: {fmt_pct(pct)}")
        lines.append("")
    if losers:
        lines.append("<b>📉 Nifty 50 Top Losers (prev session)</b>")
        for tkr, pct in losers:
            lines.append(f"  {tkr}: {fmt_pct(pct)}")
        lines.append("")

    # News
    news = fetch_news()
    if news:
        lines.append("<b>📰 Market-Moving Headlines</b>")
        for it in news:
            title = html.escape(it["title"])
            if it["link"]:
                lines.append(f'  • <a href="{html.escape(it["link"])}">{title}</a>')
            else:
                lines.append(f"  • {title}")
        lines.append("")

    # Events
    events = upcoming_events()
    if events:
        lines.append("<b>🗓️ Events to Watch (next 7 days)</b>")
        for d, label in events:
            lines.append(f"  {d.strftime('%d %b')}: {label}")
        lines.append("")

    lines.append("<i>Auto-generated summary for informational purposes only — not investment advice.</i>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Telegram send
# --------------------------------------------------------------------------

def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables."
        )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Telegram messages are capped at 4096 chars — split if needed.
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [text]
    for chunk in chunks:
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=30)
        if not resp.ok:
            print(f"[error] Telegram API error: {resp.status_code} {resp.text}", file=sys.stderr)
            resp.raise_for_status()


def main():
    message = build_message()
    print(message)  # useful for logs / local debugging
    send_telegram_message(message)


if __name__ == "__main__":
    main()
