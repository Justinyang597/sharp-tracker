# Cloudflare Worker

One Worker does three jobs:

- **Refresh button:** the site's Refresh starts the GitHub update workflow.
- **Telegram channel alerts:** every 3 minutes it checks Action Network and posts each play the
  first time it reaches 8+ sharp moves before its game starts.
- **Daily login check:** starts the "Check Action Network login" workflow once a day.

Paste `worker.js` into the Worker (**Edit code** → select all → paste → **Deploy**), then set:

## Variables and Secrets (Settings → Variables and Secrets)

| Name | Type | Value |
|---|---|---|
| `GH_TOKEN` | Secret | fine-grained GitHub token: `sharp-tracker` only, **Actions: Read and write** |
| `AN_TOKEN` | Secret | the `AN_SESSION_TOKEN_V1` cookie value (same as the GitHub `AN_TOKEN` secret) |
| `TELEGRAM_BOT_TOKEN` | Secret | the token from @BotFather |
| `TELEGRAM_CHAT_ID` | Text | the channel: `@yourchannel` if public, or its `-100…` id if private |

## KV storage (remembers which plays were already posted)

1. Cloudflare → **Storage & Databases** → **KV** → **Create** → name it `sharp-alerts`.
2. Worker → **Settings** → **Bindings** → **Add** → **KV namespace** →
   variable name `ALERTS`, namespace `sharp-alerts` → **Deploy**.

## Cron triggers (Settings → Trigger Events)

Add all four. The sports are split across three timers so each run stays within the free
plan's CPU limit; together every sport is checked every 3 minutes.

| Cron | Checks |
|---|---|
| `*/3 * * * *` | NFL, NCAAF |
| `1-59/3 * * * *` | NBA, NHL, MLB |
| `2-59/3 * * * *` | college basketball |
| `0 15 * * *` | daily login check |

## Channel layout

- **Pinned board:** one message listing every upcoming 8+ play, grouped by Today / Tomorrow /
  later days and sorted by start time. It's edited in place (new plays, started games removed,
  counts refreshed every 30 minutes). To pin it, the bot needs the admin right
  **Edit messages of others** (Channel → Administrators → your bot).
- **New-play posts:** each check sends at most one post with the plays that just reached 8+,
  grouped the same way.

## Filtered feed (optional second channel)

A separate channel that only gets the plays you choose, e.g. **college football at 13+ moves**.
The main channel still gets every 8+ play.

1. Create a second Telegram channel and add the bot as an admin (it only needs **Post messages**).
2. Add `FEED_CHAT_ID` (Text) in the Worker's Variables and Secrets: the new channel's `@name`
   or `-100…` id. **Deploy**.
3. Open a private chat with the bot and send `/settings`. The first time, it replies with your
   Telegram ID: add it as `TELEGRAM_ADMIN_ID` (Text), **Deploy**, and send `/settings` again.
4. Use the buttons: tap sports on or off, −/+ for the minimum moves (5–40), pause or resume,
   and **Test post** to check the channel is connected. Changes apply on the next check (≤3 min).

The Worker connects the settings chat to Telegram on its own within 3 minutes of deploying.
Only the `TELEGRAM_ADMIN_ID` account can change the settings.

## Checking it

- The channel gets a one-time "✅ Sharp Tracker alerts are live" post on the first check.
- `https://<your-worker>.workers.dev/status` shows the last check per sport group (counts
  only) and which settings are present.
- If the Action Network login expires, the channel gets one "alerts are paused" post a day
  until the `AN_TOKEN` secret is refreshed here and on GitHub.
