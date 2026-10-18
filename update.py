"""Cloud updater: pulls Action Network PRO sharp-money signals and rebuilds the site.

Runs in GitHub Actions (see .github/workflows/update.yml) or locally:

    AN_TOKEN=... python update.py              # last 3 days through tomorrow
    AN_TOKEN=... python update.py 20261001 20261010

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


def collect(token, start, end):
    """Returns (rows, games_seen, games_with_pro, errors)."""
    rows, errors = [], []
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
        time.sleep(0.15)
        return j

    for lg in LEAGUES:
        if lg in FOOTBALL:
            # Football ignores dates: pull the current week, plus the previous
            # regular-season week so Monday games still get graded.
            j = pull(lg, "", "current week")
            g = (j or {}).get("games") or []
            if g and (g[0].get("type") or "reg") == "reg" and (g[0].get("week") or 0) > 1:
                pull(lg, f"&seasonType=reg&week={g[0]['week'] - 1}", f"week {g[0]['week'] - 1}")
            continue
        extras = ["&division=D1&tournament=0", "&division=D1&tournament=1"] if lg == "ncaab" else [""]
        d = start
        while d <= end:
            for extra in extras:
                pull(lg, f"&date={d:%Y%m%d}{extra}", f"{d:%Y%m%d}")
            d += timedelta(days=1)
    return rows, seen, with_pro, errors


def main():
    token = (os.environ.get("AN_TOKEN") or "").strip()
    if not token:
        sys.exit("AN_TOKEN is not set. Add it as a GitHub Actions secret (see README).")

    # US Eastern-ish "today"; the window is several days wide so DST doesn't matter.
    today = (datetime.now(timezone.utc) - timedelta(hours=5)).date()
    if len(sys.argv) == 3:
        start = datetime.strptime(sys.argv[1], "%Y%m%d").date()
        end = datetime.strptime(sys.argv[2], "%Y%m%d").date()
    else:
        start, end = today - timedelta(days=3), today + timedelta(days=1)

    try:
        check_token(token)
        new_rows, seen, with_pro, errors = collect(token, start, end)
    except TokenError as e:
        sys.exit(f"{e}. Your Action Network login token expired: copy a fresh "
                 "AN_SESSION_TOKEN_V1 cookie into the AN_TOKEN secret (see README).")

    # A PRO session gets a pro_report on nearly every game; without PRO only a few
    # free games have one. Stop before saving so upcoming plays aren't dropped.
    if seen >= 20 and with_pro < seen * 0.5:
        sys.exit(f"Only {with_pro} of {seen} games had PRO data, so the token isn't a PRO "
                 "session anymore (logged out or subscription lapsed). Refresh AN_TOKEN (see README).")

    with open(SITE_DATA, encoding="utf-8") as f:
        rows = {r["id"]: r for r in json.load(f)["rows"]}
    before = len(rows)
    for r in new_rows:
        rows[r["id"]] = r
    out = sorted(rows.values(), key=lambda r: (r["t"] or "", r["id"]))
    updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = ",\n".join(json.dumps(r, separators=(",", ":")) for r in out)
    with open(SITE_DATA, "w", encoding="utf-8", newline="\n") as f:
        f.write(f'{{"updated":"{updated}","rows":[\n{lines}\n]}}\n')

    graded = sum(1 for r in out if r.get("r"))
    print(f"{start}..{end}: {seen} games ({with_pro} with PRO data), {len(new_rows)} bets with {MIN_MOVES}+ moves; "
          f"{len(out) - before} new. Total {len(out)} bets, {graded} graded.")
    for e in errors[:10]:
        print("warning:", e)


if __name__ == "__main__":
    main()
