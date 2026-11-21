// Cloudflare Worker for Sharp Tracker:
//   1. Refresh button relay: POST / starts the "Update sharp tracker" GitHub workflow
//      (skipped if one is running or started in the last 90 seconds).
//   2. Telegram channel alerts: every 3 minutes, checks Action Network and posts each
//      play the first time it reaches ALERT_MOVES (8) sharp moves before its game starts.
//   3. Daily login check: starts the "Check Action Network login" GitHub workflow.
//
// Settings → Variables and Secrets:
//   GH_TOKEN            secret  fine-grained GitHub token: sharp-tracker only, "Actions: Read and write"
//   AN_TOKEN            secret  value of the AN_SESSION_TOKEN_V1 cookie (same as the GitHub secret)
//   TELEGRAM_BOT_TOKEN  secret  from @BotFather
//   TELEGRAM_CHAT_ID    text    the channel: "@yourchannel" (public) or its -100... id (private)
// Bindings → KV namespace:  variable name ALERTS
// Settings → Triggers → Cron Triggers (all four):
//   */3 * * * *     football (NFL, NCAAF)
//   1-59/3 * * * *  NBA, NHL, MLB
//   2-59/3 * * * *  college basketball
//   0 15 * * *      daily login check
// The sports are split across three timers so each run stays within the free plan's CPU limit.

const REPO = 'Justinyang597/sharp-tracker';
const WORKFLOW = 'update.yml';
const CHECK_WORKFLOW = 'check-token.yml';
const SITE_ORIGIN = 'https://justinyang597.github.io';
const SITE_URL = 'https://justinyang597.github.io/sharp-tracker/';
const MIN_GAP_MS = 90 * 1000;
const ALERT_MOVES = 8;

const GROUPS = {
  '*/3 * * * *': { name: 'football', leagues: ['nfl', 'ncaaf'] },
  '1-59/3 * * * *': { name: 'pro', leagues: ['nba', 'nhl', 'mlb'] },
  '2-59/3 * * * *': { name: 'ncaab', leagues: ['ncaab'] },
};
const DAILY_CRON = '0 15 * * *';

const LEAGUE_NAME = { mlb: 'MLB', nfl: 'NFL', ncaaf: 'NCAAF', nba: 'NBA', nhl: 'NHL', ncaab: 'NCAAB' };
const MARKET_NAME = { spread: 'Spread', total: 'Total', moneyline: 'Moneyline' };
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36';

// ---------- GitHub ----------
const cors = {
  'Access-Control-Allow-Origin': SITE_ORIGIN,
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};
const reply = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json' } });

async function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'sharp-tracker-refresh',
      ...(init.headers || {}),
    },
  });
}
const dispatch = (env, workflow, inputs) =>
  gh(env, `/actions/workflows/${workflow}/dispatches`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(inputs ? { ref: 'main', inputs } : { ref: 'main' }),
  });

// Starts a full update unless one is going or one started within minGapMs. Returns [status, body].
async function startUpdate(env, minGapMs) {
  if (!env.GH_TOKEN) return [500, { state: 'error', message: 'GH_TOKEN is not set' }];
  const runs = await gh(env, `/actions/workflows/${WORKFLOW}/runs?per_page=5`);
  if (!runs.ok) return [502, { state: 'error', message: `GitHub runs lookup failed (${runs.status})` }];
  const list = (await runs.json()).workflow_runs || [];
  if (list.some((r) => r.status === 'queued' || r.status === 'in_progress' || r.status === 'waiting')) {
    return [200, { state: 'running' }];
  }
  const last = list[0];
  if (last && Date.now() - Date.parse(last.created_at) < minGapMs) return [200, { state: 'running' }];
  const res = await dispatch(env, WORKFLOW);
  if (res.status !== 204) return [502, { state: 'error', message: `GitHub dispatch failed (${res.status})` }];
  return [202, { state: 'started' }];
}

// ---------- Telegram ----------
async function telegram(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return false;
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: 'HTML', disable_web_page_preview: true }),
  });
  if (res.status === 429) {
    // Rate limited (a burst on a busy slate): wait as told, then try once more.
    const j = await res.json().catch(() => ({}));
    await sleep(((j.parameters && j.parameters.retry_after) || 3) * 1000);
    return telegram(env, text);
  }
  if (!res.ok) console.log('Telegram error', res.status, await res.text());
  return res.ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const escHtml = (s) => String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

const ET = 'America/New_York';
const etDate = (d) => new Intl.DateTimeFormat('en-CA', { timeZone: ET }).format(d); // YYYY-MM-DD
function whenText(iso) {
  const d = new Date(iso);
  const day = etDate(d);
  const now = new Date();
  const label = day === etDate(now) ? 'Today'
    : day === etDate(new Date(now.getTime() + 864e5)) ? 'Tomorrow'
    : new Intl.DateTimeFormat('en-US', { timeZone: ET, weekday: 'short', month: 'short', day: 'numeric' }).format(d);
  const time = new Intl.DateTimeFormat('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(d);
  return `${label} ${time}`;
}
function pickLabel(p) {
  if (p.m === 'total') return `${p.s === 'over' ? 'Over' : 'Under'}${p.line != null ? ' ' + p.line : ''}`;
  if (p.m === 'moneyline') return `${p.team} ML`;
  return `${p.team}${p.line != null ? ' ' + (p.line > 0 ? '+' : '') + p.line : ''}`;
}
function alertText(p) {
  const odds = p.odds == null ? '' : ` (${p.odds > 0 ? '+' : ''}${p.odds})`;
  return `🔥 <b>${p.steam + p.rlm} sharp moves</b> · ${LEAGUE_NAME[p.lg] || p.lg.toUpperCase()} ${MARKET_NAME[p.m] || ''}\n` +
    `<b>${escHtml(pickLabel(p))}</b>${odds}\n` +
    `${escHtml(p.a)} @ ${escHtml(p.h)} · ${whenText(p.t)}\n` +
    `${p.steam} steam · ${p.rlm} reverse\n` +
    `<a href="${SITE_URL}">Open tracker</a>`;
}

// ---------- Action Network ----------
class TokenError extends Error {}

async function an(env, league, params) {
  const res = await fetch(`https://api.actionnetwork.com/web/v2/scoreboard/proreport/${league}?bookIds=15&periods=event${params}`, {
    headers: {
      authorization: env.AN_TOKEN,
      accept: 'application/json',
      'user-agent': UA,
      origin: 'https://www.actionnetwork.com',
      referer: 'https://www.actionnetwork.com/',
    },
  });
  if (res.status === 401) throw new TokenError('Action Network rejected the login (401)');
  if (!res.ok) throw new Error(`${league}${params}: HTTP ${res.status}`);
  return res.json();
}

// Plays in one game that have ALERT_MOVES+ sharp moves and haven't started.
function hotPlays(league, game, now) {
  const out = [];
  const pr = game.pro_report;
  if (!pr || game.status !== 'scheduled' || Date.parse(game.start_time) <= now) return out;
  const teams = game.teams || [];
  const away = teams.find((t) => t.id === game.away_team_id) || teams[0] || {};
  const home = teams.find((t) => t.id === game.home_team_id) || teams[1] || {};
  const book = ((game.markets || {})['15'] || {}).event || {};
  for (const m of ['spread', 'total', 'moneyline']) {
    for (const [side, sigs] of Object.entries(pr[m] || {})) {
      const s = (sigs || []).find((x) => x.signal_type === 'sharp_money');
      if (!s) continue;
      const steam = (s.meta && s.meta.steam_moves) || 0;
      const rlm = (s.meta && s.meta.reverse_line_moves) || 0;
      if (steam + rlm < ALERT_MOVES) continue;
      const teamId = side === 'away' ? away.id : side === 'home' ? home.id : null;
      const o = (book[m] || []).find((x) => (m === 'total' ? x.side === side : x.side === side || (teamId && x.team_id === teamId)));
      out.push({
        id: `${league}-${game.id}-${m}-${side}`, lg: league, m, s: side, t: game.start_time,
        a: away.abbr, h: home.abbr, team: side === 'away' ? away.abbr : home.abbr,
        line: o ? o.value : null, odds: o ? o.odds : null, steam, rlm,
      });
    }
  }
  return out;
}

async function poll(env, group) {
  const now = Date.now();
  const days = [etDate(new Date(now)), etDate(new Date(now + 864e5))].map((d) => d.replace(/-/g, ''));
  const plays = [];
  let seen = 0, withPro = 0;
  const errors = [];
  for (const lg of group.leagues) {
    // Football ignores dates and returns the current week; the rest go by date.
    const queries = lg === 'nfl' || lg === 'ncaaf' ? ['']
      : lg === 'ncaab' ? days.flatMap((d) => [`&date=${d}&division=D1&tournament=0`, `&date=${d}&division=D1&tournament=1`])
      : days.map((d) => `&date=${d}`);
    for (const q of queries) {
      try {
        const j = await an(env, lg, q);
        for (const g of j.games || []) {
          seen++;
          if (g.pro_report) withPro++;
          plays.push(...hotPlays(lg, g, now));
        }
      } catch (e) {
        if (e instanceof TokenError) throw e;
        errors.push(String(e.message || e));
      }
    }
  }
  // A PRO session has a pro_report on nearly every game; only a few free games have one otherwise.
  if (seen >= 10 && withPro < seen * 0.5) throw new TokenError(`Only ${withPro} of ${seen} games had PRO data (not a PRO login)`);

  // Post each play once. Remembered per group until two days after its game.
  const key = `alerted:${group.name}`;
  const alerted = JSON.parse((await env.ALERTS.get(key)) || '{}');
  let changed = false;
  for (const [id, until] of Object.entries(alerted)) if (until < now) { delete alerted[id]; changed = true; }
  plays.sort((a, b) => a.t.localeCompare(b.t));
  let sent = 0;
  for (const p of plays) {
    if (alerted[p.id]) continue;
    if (await telegram(env, alertText(p))) {
      alerted[p.id] = Date.parse(p.t) + 2 * 864e5;
      changed = true;
      sent++;
      await sleep(1100); // stay under Telegram's ~20 posts/minute per channel
    }
  }
  if (changed) await env.ALERTS.put(key, JSON.stringify(alerted));

  // One-time hello so you know the channel is wired up; plus a once-an-hour status for checking.
  if (!(await env.ALERTS.get('hello'))) {
    if (await telegram(env, `✅ <b>Sharp Tracker alerts are live.</b>\nThis channel gets a post for every play that reaches ${ALERT_MOVES}+ sharp moves before its game starts, checked every 3 minutes.\n<a href="${SITE_URL}">Open tracker</a>`)) {
      await env.ALERTS.put('hello', new Date().toISOString());
    }
  }
  const status = { at: new Date().toISOString(), group: group.name, games: seen, withPro, hot: plays.length, sent, errors: errors.slice(0, 5) };
  if (new Date().getUTCMinutes() < 3 || sent || errors.length) await env.ALERTS.put(`status:${group.name}`, JSON.stringify(status));
  console.log(JSON.stringify(status));
}

// Expired login: warn the channel at most once a day.
async function tokenProblem(env, err) {
  console.log('Login problem:', err.message);
  if (await env.ALERTS.get('token_warned')) return;
  if (await telegram(env, '⚠️ <b>Sharp Tracker alerts are paused.</b>\nThe Action Network login expired, so new plays can\'t be checked until it\'s refreshed.')) {
    await env.ALERTS.put('token_warned', new Date().toISOString(), { expirationTtl: 20 * 3600 });
  }
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    // GET /status: last alert-check summary per sport group (counts only, no secrets).
    if (request.method === 'GET' && new URL(request.url).pathname === '/status') {
      const out = {};
      for (const g of Object.values(GROUPS)) out[g.name] = JSON.parse((await env.ALERTS?.get(`status:${g.name}`)) || 'null');
      out.hello = (await env.ALERTS?.get('hello')) || null;
      out.configured = { AN_TOKEN: !!env.AN_TOKEN, TELEGRAM_BOT_TOKEN: !!env.TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID: !!env.TELEGRAM_CHAT_ID, ALERTS: !!env.ALERTS };
      return new Response(JSON.stringify(out, null, 2), { headers: { ...cors, 'Content-Type': 'application/json' } });
    }
    if (request.method !== 'POST') return reply(405, { state: 'error', message: 'Use POST' });
    const [status, body] = await startUpdate(env, MIN_GAP_MS);
    return reply(status, body);
  },

  async scheduled(event, env, ctx) {
    if (event.cron === DAILY_CRON) {
      ctx.waitUntil(dispatch(env, CHECK_WORKFLOW));
      return;
    }
    const group = GROUPS[event.cron];
    if (!group || !env.AN_TOKEN || !env.ALERTS) return;
    ctx.waitUntil(poll(env, group).catch((e) => (e instanceof TokenError ? tokenProblem(env, e) : console.log('Alert check failed:', e.message || e))));
  },
};
