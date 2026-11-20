# Sharp Move Tracker

Tracks Action Network PRO "sharp money" bets with 5+ sharp moves (steam moves +
reverse line moves) across MLB, NFL, NCAAF, NBA, NHL and NCAAB, grades them at the
consensus closing line, and publishes a dashboard with GitHub Pages.

- `update.py` pulls the PRO feed, grades finished games and rewrites `docs/data.json`.
- `.github/workflows/update.yml` runs it every hour in GitHub Actions and commits the result.
- `docs/` is the website (GitHub Pages serves it from the `main` branch, `/docs` folder).

## Refreshing the Action Network token

The updater signs in with your existing Action Network session, not your password
(Action Network's login form needs a captcha). The session token lasts about
6 months. When it expires, the workflow fails and GitHub emails you. To refresh it:

1. In Chrome, go to https://www.actionnetwork.com and make sure you're signed in.
2. Press F12 to open DevTools, then go to **Application** → **Cookies** →
   `https://www.actionnetwork.com`.
3. Find the cookie named `AN_SESSION_TOKEN_V1` and copy its **Value**.
4. In this repo on GitHub: **Settings** → **Secrets and variables** → **Actions** →
   `AN_TOKEN` → **Update**, paste the value, and save.
5. **Actions** tab → **Update sharp tracker** → **Run workflow** to confirm it works.

Treat that value like a password: anyone with it can use your Action Network account.
Signing out of Action Network ends the session, so you'd need to refresh the secret after that.

## Telegram alerts

Every run (Refresh presses, and the Worker's 15-minute check) sends one Telegram message
per play the first time it reaches 8+ sharp moves before its game starts. Set it up:

1. In Telegram, message **@BotFather** → `/newbot` → pick a name and a username ending in `bot`.
   Copy the **token** it gives you.
2. Open your new bot's chat and press **Start** (a bot can't message you until you do).
3. Message **@userinfobot** and copy your **Id** number.
4. In this repo: **Settings** → **Secrets and variables** → **Actions** → add
   `TELEGRAM_BOT_TOKEN` (the token) and `TELEGRAM_CHAT_ID` (your Id).
5. **Actions** → **Update sharp tracker** → **Run workflow** → mode `telegram-test`.

To change the threshold, add a repository **variable** or secret named `ALERT_MOVES`.

## Running locally

```bash
AN_TOKEN=... python update.py                     # last 3 days through tomorrow
AN_TOKEN=... python update.py 20261001 20261010   # a specific date range
```
