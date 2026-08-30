"""
Indian Market Morning Summary — Telegram Bot
=============================================
Fetches, before market open:
  1. Global overnight cues (US & Asian indices, crude, USD/INR)
  2. Domestic snapshot (Nifty 50 / Sensex previous close)
  3. Nifty 50 top gainers & losers (previous session)
  4. Market-moving news headlines (RSS, no API key needed)
  5. A curated economic-events watchlist for the week

...and sends it as one formatted message to a Telegram chat.

Run manually:   python bot.py
Run on schedule: see .github/workflows/morning-summary.yml
"""

import os
import sys
import datetime
import html
import requests
import feedparser
import yfinance as yf

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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
    "TATAMOTORS.NS", "TATASTEEL.NS", "TCS.NS", "TECHM.NS", "TITAN.NS",
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

    # Movers
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
