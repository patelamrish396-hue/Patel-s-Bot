# Indian Market Morning Summary — Telegram Bot

Sends a daily pre-market brief to a Telegram chat: overnight global cues,
Nifty/Sensex last close, Nifty 50 top gainers/losers, market-moving news
headlines, and upcoming economic events (RBI policy, Budget, etc).

Runs automatically on weekday mornings via GitHub Actions — free, no server needed.

## 1. Create the Telegram bot

1. Open Telegram, message **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`, follow the prompts, and copy the **bot token** it gives you
   (looks like `123456789:ABCdefGhIJKlmnoPQRstuVwxyZ`).
3. Start a chat with your new bot (search its username, hit **Start**) —
   or add it to a group/channel if you want the summary posted there instead.

## 2. Get your chat ID

- **For a personal DM:** message your bot anything, then open this URL in a
  browser (replace `<TOKEN>`):
  `https://api.telegram.org/bot<TOKEN>/getUpdates`
  Look for `"chat":{"id": ...}` in the JSON response — that number is your chat ID.
- **For a group/channel:** add the bot as a member (and as admin, for channels),
  post a message, then use the same `getUpdates` URL. Group/channel IDs are
  usually negative numbers (e.g. `-1001234567890`).

## 3. Push this code to a GitHub repo

```bash
cd telegram-market-bot
git init
git add .
git commit -m "Market morning summary bot"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

## 4. Add your secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | the chat ID from step 2 |

## 5. Done

The workflow in `.github/workflows/morning-summary.yml` runs automatically at
**7:15 AM IST, every day**. You can also trigger it manually any time from the
**Actions** tab → *Market Morning Summary* → **Run workflow**.

> **Note on weekends:** NSE/BSE and most global exchanges are closed Saturday
> and Sunday, so the "last close" and "top gainers/losers" sections on those
> days will just repeat Friday's numbers. News headlines and the events
> calendar still update normally.

## Running it locally (optional, for testing)

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="123456789:ABC..."
export TELEGRAM_CHAT_ID="123456789"
python bot.py
```

## Customizing

- **Change the time:** edit the `cron` line in the workflow file. GitHub Actions
  cron is in UTC; IST = UTC + 5:30.
- **Add/remove stocks:** edit the `NIFTY50` list in `bot.py`.
- **Add/remove news sources:** edit the `NEWS_FEEDS` dict (any RSS feed works).
- **Update event dates:** edit `KEY_EVENTS_2026` at the start of each year —
  RBI MPC dates and the Union Budget date are announced yearly, so this list
  isn't auto-fetched.
- **Add FII/DII flows, options data, etc:** those aren't included since they
  need a paid data feed or fragile scraping — happy to help wire one in if
  you have a source in mind.

## Notes

- Data comes from Yahoo Finance (via `yfinance`) and public RSS feeds — free,
  but unofficial and occasionally rate-limited or delayed. Don't use this as
  your sole source for time-critical trading decisions.
- The bot only *reads and summarizes* public market data and news — it has no
  ability to place trades or influence markets itself.
