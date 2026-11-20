"""Cloud updater: pulls Action Network PRO sharp-money signals and rebuilds the site.

Runs in GitHub Actions (see .github/workflows/update.yml) or locally:

    AN_TOKEN=... python update.py              # last 3 days through tomorrow
    AN_TOKEN=... python update.py 20261001 20261010
    AN_TOKEN=... python update.py --check      # only confirm the token still works
    AN_TOKEN=... python update.py --alerts     # light pull (today + tomorrow) for Telegram alerts
    python update.py --telegram-test           # send a test Telegram message

Telegram alerts (optional): set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Every run sends
one message per play the first time it reaches ALERT_MOVES (default 8) before its game starts.

AN_TOKEN is the value of the AN_SESSION_TOKEN_V1 cookie from a logged-in
actionnetwork.com browser. It lasts about 180 days; when it expires the run
fails with a clear message and GitHub emails the repo owner.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# The site's data file is also the database: every bet ever logged, one per line
# so each hourly commit only stores the lines that changed.
SITE_DATA = os.path.join(HERE, "docs", "data.json")

API = "https://api.actionnetwork.com/web/v2/scoreboard/proreport/"
BOOKS = "15,30,79,2988,4727,69,68,75,123,71"
LINE_BOOKS = ["15", "68", "69"]  # Consensus, then DK, then FanDuel
MIN_MOVES = 5  # the app's "sharp moves" = steam + reverse line moves
LEAGUES = ["mlb", "nfl", "ncaaf", "nba", "nhl", "ncaab"]
FOOTBALL = {"nfl", "ncaaf"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


class TokenError(Exception):
    pass


def check_token(token):
    """Fails fast if the token isn't a logged-in session (401 from the account endpoint)."""
    req = urllib.request.Request(
        "https://api.actionnetwork.com/web/v1/me?includeChargifyBillingURL=false",
        headers={"authorization": token, "accept": "application/json", "user-agent": UA,
                 "origin": "https://www.actionnetwork.com"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TokenError(f"Action Network rejected the token ({e.code})")
        raise


def get(token, league, params):
    url = f"{API}{league}?bookIds={BOOKS}&periods=event{params}"
    req = urllib.request.Request(url, headers={
        "authorization": token,
        "accept": "application/json",
        "user-agent": UA,
        "origin": "https://www.actionnetwork.com",
        "referer": "https://www.actionnetwork.com/",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise TokenError("Action Network rejected the token (401)")
            if attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(2 * (attempt + 1))


def find_outcome(game, market, side, team_id):
    for b in LINE_BOOKS:
        lst = ((game.get("markets") or {}).get(b) or {}).get("event", {}).get(market)
        if not lst:
            continue
        for x in lst:
            if market == "total":
                if x.get("side") == side:
                    return x.get("value"), x.get("odds"), int(b)
            elif x.get("side") == side or (team_id and x.get("team_id") == team_id):
                return x.get("value"), x.get("odds"), int(b)
    return None, None, None


def grade(market, side, line, away, home):
    if away is None or home is None:
        return None
    if market == "total":
        if line is None:
            return None
        t = away + home
        if t == line:
            return "P"
        return "W" if (side == "over") == (t > line) else "L"
    mine, opp = (away, home) if side == "away" else (home, away)
    if market == "moneyline":
        return "P" if mine == opp else "W" if mine > opp else "L"
    if line is None:
        return None
    m = mine + line - opp
    return "P" if m == 0 else "W" if m > 0 else "L"


def rows_for(league, game):
    out = []
    pr = game.get("pro_report")
    if not pr:
        return out
    teams = game.get("teams") or []
    away = next((t for t in teams if t["id"] == game.get("away_team_id")), teams[0] if teams else {})
    home = next((t for t in teams if t["id"] == game.get("home_team_id")), teams[1] if len(teams) > 1 else {})
    done = game.get("status") in ("complete", "closed")
    box = game.get("boxscore") or {}
    a_s = box.get("total_away_points") if done else None
    h_s = box.get("total_home_points") if done else None
    start = game.get("start_time") or ""
    for market in ("spread", "total", "moneyline"):
        for side, sigs in (pr.get(market) or {}).items():
            s = next((x for x in (sigs or []) if x.get("signal_type") == "sharp_money"), None)
            if not s:
                continue
            meta = s.get("meta") or {}
            steam = meta.get("steam_moves") or 0
            rlm = meta.get("reverse_line_moves") or 0
            if steam + rlm < MIN_MOVES:
                continue
            team_id = away.get("id") if side == "away" else home.get("id") if side == "home" else None
            line, odds, book = find_outcome(game, market, side, team_id)
            out.append({
                "id": f"{league}-{game['id']}-{market}-{side}",
                "lg": league,
                "d": start[:10].replace("-", ""),
                "t": start,
                "a": away.get("abbr"),
                "h": home.get("abbr"),
                "m": market,
                "s": side,
                "pick": side if market == "total" else away.get("abbr") if side == "away" else home.get("abbr"),
                "line": line,
                "odds": odds,
                "book": book,
                "steam": steam,
                "rlm": rlm,
                "str": s.get("strength"),
                "as": a_s,
                "hs": h_s,
                "st": game.get("status"),
                "r": grade(market, side, line, a_s, h_s) if done else None,
            })
    return out


def game_state(league, game):
    """Status and final score for a game, keyed like the start of a row id."""
    done = game.get("status") in ("complete", "closed")
    box = game.get("boxscore") or {}
    return f"{league}-{game['id']}", {
        "st": game.get("status"),
        "as": box.get("total_away_points") if done else None,
        "hs": box.get("total_home_points") if done else None,
        "done": done,
    }


def collect(token, start, end, football_prev=True):
    """Returns (rows, games, games_seen, games_with_pro, errors).

    games maps "league-gameid" to that game's status and score, for every game
    pulled, so plays whose sharp signal later dropped below 5 still get graded."""
    rows, errors = [], []
    games_state = {}
    seen = with_pro = 0

    def pull(league, params, label):
        nonlocal seen, with_pro
        try:
            j = get(token, league, params)
        except TokenError:
            raise
        except Exception as e:  # one bad date shouldn't sink the run
            errors.append(f"{league} {label}: {e}")
            return None
        games = j.get("games") or []
        seen += len(games)
        with_pro += sum(1 for g in games if g.get("pro_report"))
        for g in games:
            rows.extend(rows_for(league, g))
            key, gs = game_state(league, g)
            games_state[key] = gs
        time.sleep(0.15)
        return j

    for lg in LEAGUES:
        if lg in FOOTBALL:
            # Football ignores dates: pull the current week, plus the previous
            # regular-season week so Monday games still get graded (skipped on alert runs).
            j = pull(lg, "", "current week")
            g = (j or {}).get("games") or []
            if football_prev and g and (g[0].get("type") or "reg") == "reg" and (g[0].get("week") or 0) > 1:
                pull(lg, f"&seasonType=reg&week={g[0]['week'] - 1}", f"week {g[0]['week'] - 1}")
            continue
        extras = ["&division=D1&tournament=0", "&division=D1&tournament=1"] if lg == "ncaab" else [""]
        d = start
        while d <= end:
            for extra in extras:
                pull(lg, f"&date={d:%Y%m%d}{extra}", f"{d:%Y%m%d}")
            d += timedelta(days=1)
    return rows, games_state, seen, with_pro, errors


# ---- Telegram alerts ----
ALERT_MOVES = int(os.environ.get("ALERT_MOVES") or 8)
SITE_URL = "https://justinyang597.github.io/sharp-tracker/"
LEAGUE_NAME = {"mlb": "MLB", "nfl": "NFL", "ncaaf": "NCAAF", "nba": "NBA", "nhl": "NHL", "ncaab": "NCAAB"}
MARKET_NAME = {"spread": "Spread", "total": "Total", "moneyline": "Moneyline"}


def telegram(text):
    """Sends a Telegram message if the bot is configured. Returns True if sent."""
    bot = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not bot or not chat:
        return False
    body = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{bot}/sendMessage", data=body,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return bool(json.load(r).get("ok"))
    except Exception as e:  # a Telegram hiccup shouldn't sink the update
        print("warning: Telegram send failed:", e)
        return False


def eastern(dt):
    """UTC datetime -> (US Eastern datetime, "EST"/"EDT"). DST: 2nd Sun of Mar to 1st Sun of Nov."""
    def nth_sunday(year, month, n):
        d = datetime(year, month, 1, tzinfo=timezone.utc)
        return d + timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
    dst = nth_sunday(dt.year, 3, 2) + timedelta(hours=7) <= dt < nth_sunday(dt.year, 11, 1) + timedelta(hours=6)
    off = -4 if dst else -5
    return dt.astimezone(timezone(timedelta(hours=off))), "EDT" if dst else "EST"


def pick_label(r):
    if r["m"] == "total":
        return f"{'Over' if r['s'] == 'over' else 'Under'} {r['line']}"
    if r["m"] == "moneyline":
        return f"{r['pick']} ML"
    line = r["line"]
    return f"{r['pick']} {'+' if line is not None and line > 0 else ''}{line}"


def alert_text(r):
    moves = r["steam"] + r["rlm"]
    when, tz = eastern(datetime.fromisoformat(r["t"].replace("Z", "+00:00")))
    today = eastern(datetime.now(timezone.utc))[0].date()
    if when.date() == today:
        day = "Today"
    elif when.date() == today + timedelta(days=1):
        day = "Tomorrow"
    else:
        day = f"{when:%a %b} {when.day}"
    clock = f"{when.hour % 12 or 12}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"
    odds = r["odds"]
    odds_s = "" if odds is None else f" ({'+' if odds > 0 else ''}{odds})"
    return (f"🔥 <b>{moves} sharp moves</b> · {LEAGUE_NAME.get(r['lg'], r['lg'].upper())} {MARKET_NAME.get(r['m'], '')}\n"
            f"<b>{pick_label(r)}</b>{odds_s}\n"
            f"{r['a']} @ {r['h']} · {day} {clock} {tz}\n"
            f"{r['steam']} steam · {r['rlm']} reverse\n"
            f'<a href="{SITE_URL}">Open tracker</a>')


# ---- Site data file ----
def load_site():
    with open(SITE_DATA, encoding="utf-8") as f:
        return json.load(f)


def write_site(rows, meta):
    """One bet per line. Line 1 (update time, login status) changes every run and is ignored
    when deciding whether to commit; line 2 (when we last warned on Telegram) is kept."""
    updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = ",\n".join(json.dumps(r, separators=(",", ":")) for r in rows)
    with open(SITE_DATA, "w", encoding="utf-8", newline="\n") as f:
        f.write(f'{{"updated":"{updated}","token_error":{json.dumps(meta.get("token_error"))},\n'
                f'"token_warned":{json.dumps(meta.get("token_warned"))},"rows":[\n{lines}\n]}}\n')


def token_failed(msg, alerts_mode):
    """Flags the site, warns on Telegram at most once a day, and stops the run."""
    site = load_site()
    now = datetime.now(timezone.utc)
    meta = {"token_error": now.isoformat(timespec="seconds"), "token_warned": site.get("token_warned")}
    last = site.get("token_warned")
    if not last or now - datetime.fromisoformat(last) > timedelta(hours=20):
        if telegram("⚠️ <b>Sharp Tracker can't log in to Action Network.</b>\n"
                    "Your login token expired. Copy a fresh AN_SESSION_TOKEN_V1 cookie into the "
                    "AN_TOKEN secret on GitHub (steps in the repo README)."):
            meta["token_warned"] = now.isoformat(timespec="seconds")
    write_site(site["rows"], meta)
    print(msg)
    # Scheduled alert checks exit cleanly so GitHub doesn't email you every 15 minutes;
    # a Refresh press fails visibly.
    sys.exit(0 if alerts_mode else 1)


def main():
    args = sys.argv[1:]
    if args == ["--telegram-test"]:
        ok = telegram(f"✅ <b>Sharp Tracker is connected.</b>\nYou'll get a message for every play that "
                      f"reaches {ALERT_MOVES}+ sharp moves before its game starts.")
        sys.exit(0 if ok else "Telegram test failed: check the TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
                 "secrets, and that you pressed Start in your bot's chat.")

    token = (os.environ.get("AN_TOKEN") or "").strip()
    if not token:
        sys.exit("AN_TOKEN is not set. Add it as a GitHub Actions secret (see README).")

    # US Eastern-ish "today"; the windows are days wide so the exact hour doesn't matter.
    today = (datetime.now(timezone.utc) - timedelta(hours=5)).date()
    check_only = args == ["--check"]
    alerts_mode = args == ["--alerts"]
    if check_only:
        start = end = today
    elif alerts_mode:
        start, end = today, today + timedelta(days=1)
    elif len(args) == 2:
        start = datetime.strptime(args[0], "%Y%m%d").date()
        end = datetime.strptime(args[1], "%Y%m%d").date()
    else:
        start, end = today - timedelta(days=3), today + timedelta(days=1)

    fix = "Copy a fresh AN_SESSION_TOKEN_V1 cookie into the AN_TOKEN secret (see README)."
    try:
        check_token(token)
        new_rows, games_state, seen, with_pro, errors = collect(token, start, end, football_prev=not alerts_mode)
    except TokenError as e:
        msg = f"{e}. Your Action Network login token expired. {fix}"
        if check_only:
            sys.exit(msg)
        token_failed(msg, alerts_mode)

    # A PRO session gets a pro_report on nearly every game; without PRO only a few
    # free games have one. Stop before saving so upcoming plays aren't dropped.
    if seen >= 20 and with_pro < seen * 0.5:
        msg = (f"Only {with_pro} of {seen} games had PRO data, so the token isn't a PRO session anymore "
               f"(logged out or subscription lapsed). {fix}")
        if check_only:
            sys.exit(msg)
        token_failed(msg, alerts_mode)

    if check_only:
        print(f"Token OK: logged in, {with_pro} of {seen} games today have PRO data.")
        return

    site = load_site()
    rows = {r["id"]: r for r in site["rows"]}
    before = len(rows)
    for r in new_rows:
        old = rows.get(r["id"])
        if old and old.get("alerted"):
            r["alerted"] = old["alerted"]
        rows[r["id"]] = r

    # A play logged earlier whose sharp signal has since dropped below 5 (or vanished)
    # isn't in new_rows, but its game was pulled: keep its last counts and line, and
    # update the status, score and result so it doesn't sit "scheduled" forever.
    fresh_ids = {r["id"] for r in new_rows}
    regraded = 0
    for r in rows.values():
        if r["id"] in fresh_ids or r.get("r"):
            continue
        gs = games_state.get(r["id"].rsplit("-", 2)[0])
        if not gs:
            continue
        r["st"], r["as"], r["hs"] = gs["st"], gs["as"], gs["hs"]
        if gs["done"]:
            r["r"] = grade(r["m"], r["s"], r["line"], gs["as"], gs["hs"])
            regraded += r["r"] is not None

    # Telegram: one alert per play, the first time it reaches ALERT_MOVES before its game starts.
    now_iso = datetime.now(timezone.utc).isoformat()
    sent = 0
    for r in sorted(rows.values(), key=lambda r: r["t"] or ""):
        if (r["steam"] + r["rlm"] >= ALERT_MOVES and not r.get("alerted") and not r.get("r")
                and r.get("st") == "scheduled" and (r["t"] or "") > now_iso):
            if telegram(alert_text(r)):
                r["alerted"] = r["steam"] + r["rlm"]
                sent += 1
                time.sleep(0.5)

    out = sorted(rows.values(), key=lambda r: (r["t"] or "", r["id"]))
    write_site(out, {"token_error": None, "token_warned": None})

    graded = sum(1 for r in out if r.get("r"))
    print(f"{start}..{end}: {seen} games ({with_pro} with PRO data), {len(new_rows)} bets with {MIN_MOVES}+ moves; "
          f"{len(out) - before} new, {regraded} older plays graded, {sent} Telegram alerts. "
          f"Total {len(out)} bets, {graded} graded.")
    for e in errors[:10]:
        print("warning:", e)


if __name__ == "__main__":
    main()
