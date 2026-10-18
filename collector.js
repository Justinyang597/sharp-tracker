// Action Network sharp-move collector.
// Run inside a logged-in actionnetwork.com tab via the Chrome javascript tool:
//   1. Paste this whole file once (installs window.__anGet / window.__collect).
//   2. Click "Previous Date" (or any date/market control) so the page makes one
//      authenticated API call; the hook keeps its auth header inside the page.
//   3. await __collect(['mlb','nfl'], '20261001', '20261016')
// The auth token never leaves the page; __collect returns only bet rows.
(() => {
  if (!window.__anGet) {
    let H = null;
    const base = window.fetch;
    window.fetch = function (a, b) {
      try {
        const u = (a && a.url) || String(a);
        if (u.includes('api.actionnetwork.com') && !H) {
          const h = a instanceof Request ? a.headers : b && b.headers;
          if (h) {
            const hh = h instanceof Headers ? h : new Headers(h);
            if (hh.get('authorization')) H = new Headers(hh);
          }
        }
      } catch (e) {}
      return base.apply(this, arguments);
    };
    window.__anReady = () => !!H;
    window.__anGet = async (url) => {
      if (!H) throw new Error('no auth yet: click Previous Date first');
      const r = await base(url, { headers: H });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    };
  }

  const BOOKS = '15,30,79,2988,4727,69,68,75,123,71';
  const LINE_BOOKS = ['15', '68', '69']; // Consensus, then DK, then FanDuel
  const MIN_MOVES = 5; // the app's "sharp moves" = steam + reverse line moves

  const ymd = (d) => d.toISOString().slice(0, 10).replace(/-/g, '');
  const addDay = (s) => {
    const d = new Date(Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8)));
    d.setUTCDate(d.getUTCDate() + 1);
    return ymd(d);
  };

  function findOutcome(game, market, side, teamId) {
    for (const b of LINE_BOOKS) {
      const list = game.markets?.[b]?.event?.[market];
      if (!list) continue;
      const o = list.find((x) =>
        market === 'total' ? x.side === side : x.side === side || (teamId && x.team_id === teamId)
      );
      if (o) return { line: o.value ?? null, odds: o.odds ?? null, book: +b };
    }
    return { line: null, odds: null, book: null };
  }

  function grade(market, side, line, away, home) {
    if (away == null || home == null) return null;
    if (market === 'total') {
      if (line == null) return null;
      const t = away + home;
      if (t === line) return 'P';
      return (side === 'over') === t > line ? 'W' : 'L';
    }
    const mine = side === 'away' ? away : home;
    const opp = side === 'away' ? home : away;
    if (market === 'moneyline') return mine === opp ? 'P' : mine > opp ? 'W' : 'L';
    if (line == null) return null;
    const m = mine + line - opp;
    return m === 0 ? 'P' : m > 0 ? 'W' : 'L';
  }

  function rowsFor(league, date, game) {
    const out = [];
    const pr = game.pro_report;
    if (!pr) return out;
    const away = game.teams.find((t) => t.id === game.away_team_id) || game.teams[0];
    const home = game.teams.find((t) => t.id === game.home_team_id) || game.teams[1];
    const done = game.status === 'complete' || game.status === 'closed';
    const as = done ? game.boxscore?.total_away_points ?? null : null;
    const hs = done ? game.boxscore?.total_home_points ?? null : null;
    for (const market of ['spread', 'total', 'moneyline']) {
      for (const [side, sigs] of Object.entries(pr[market] || {})) {
        const s = (sigs || []).find((x) => x.signal_type === 'sharp_money');
        if (!s) continue;
        const steam = s.meta?.steam_moves || 0;
        const rlm = s.meta?.reverse_line_moves || 0;
        if (steam + rlm < MIN_MOVES) continue;
        const teamId = side === 'away' ? away.id : side === 'home' ? home.id : null;
        const { line, odds, book } = findOutcome(game, market, side, teamId);
        out.push({
          id: `${league}-${game.id}-${market}-${side}`,
          lg: league,
          d: date,
          t: game.start_time,
          a: away.abbr,
          h: home.abbr,
          m: market,
          s: side,
          pick: market === 'total' ? side : side === 'away' ? away.abbr : home.abbr,
          line,
          odds,
          book,
          steam,
          rlm,
          str: s.strength ?? null,
          as,
          hs,
          st: game.status,
          r: done ? grade(market, side, line, as, hs) : null,
        });
      }
    }
    return out;
  }

  const FOOTBALL = ['nfl', 'ncaaf'];
  const pull = async (lg, extra, label, rows, errors) => {
    const url = `https://api.actionnetwork.com/web/v2/scoreboard/proreport/${lg}?bookIds=${BOOKS}&periods=event${extra}`;
    try {
      const j = await window.__anGet(url);
      for (const g of j.games || []) rows.push(...rowsFor(lg, (g.start_time || '').slice(0, 10).replace(/-/g, ''), g));
      return j;
    } catch (e) {
      errors.push(`${lg} ${label}: ${e.message}`);
      return null;
    }
  };

  // Football ignores dates: pull by week. extras like '&seasonType=reg&week=3'
  // or '&seasonType=post&week=1&season=2025'.
  window.__collectWeeks = async (lg, extrasList, delayMs = 150) => {
    const rows = [];
    const errors = [];
    for (const extra of extrasList) {
      await pull(lg, extra, extra, rows, errors);
      await new Promise((r) => setTimeout(r, delayMs));
    }
    window.__rows = [...new Map(rows.map((r) => [r.id, r])).values()];
    return { count: window.__rows.length, errors };
  };

  // Dates apply to MLB/NBA/NHL/NCAAB. For football, start/end are ignored and the
  // current week plus the previous week are pulled (so Monday games still get graded).
  window.__collect = async (leagues, start, end, delayMs = 150) => {
    const rows = [];
    const errors = [];
    for (const lg of leagues) {
      if (FOOTBALL.includes(lg)) {
        const j = await pull(lg, '', 'current week', rows, errors);
        const g = j && (j.games || [])[0];
        if (g && g.week > 1 && (g.type || 'reg') === 'reg') {
          await pull(lg, `&seasonType=reg&week=${g.week - 1}`, `week ${g.week - 1}`, rows, errors);
        }
        continue;
      }
      // College basketball needs a division, and March Madness lives under tournament=1.
      const extras = lg === 'ncaab' ? ['&division=D1&tournament=0', '&division=D1&tournament=1'] : [''];
      for (let d = start; d <= end; d = addDay(d)) {
        for (const extra of extras) {
          const url = `https://api.actionnetwork.com/web/v2/scoreboard/proreport/${lg}?bookIds=${BOOKS}&date=${d}&periods=event${extra}`;
          try {
            const j = await window.__anGet(url);
            for (const g of j.games || []) rows.push(...rowsFor(lg, d, g));
          } catch (e) {
            errors.push(`${lg} ${d}: ${e.message}`);
          }
          await new Promise((r) => setTimeout(r, delayMs));
        }
      }
    }
    // Football endpoints return the whole week for any date, so dedupe by id.
    window.__rows = [...new Map(rows.map((r) => [r.id, r])).values()];
    return { count: window.__rows.length, errors };
  };

  // Sends rows to receiver.py on this PC (Chrome asks once to allow local access).
  window.__push = async (rows) => {
    const out = [];
    for (let i = 0; i < rows.length; i += 2000) {
      const r = await fetch('http://127.0.0.1:8765/rows', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rows.slice(i, i + 2000)),
      });
      out.push(await r.json());
    }
    return out;
  };
})();
'collector installed';
