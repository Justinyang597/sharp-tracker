// Cloudflare Worker behind the site's Refresh button, and a backup clock.
// POST / -> starts the "Update sharp tracker" GitHub workflow, unless one is
// already running or one started in the last 90 seconds (so the button can't be spammed).
// Cron trigger (Worker → Settings → Triggers → Cron, "*/5 * * * *") -> does the same
// every 5 minutes, because GitHub's own schedule is often late or skipped.
//
// Settings (Worker → Settings → Variables and Secrets):
//   GH_TOKEN  (secret)  fine-grained GitHub token: this repo only, "Actions: Read and write"
const REPO = 'Justinyang597/sharp-tracker';
const WORKFLOW = 'update.yml';
const SITE_ORIGIN = 'https://justinyang597.github.io';
const MIN_GAP_MS = 90 * 1000;

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

// Starts a run unless one is going or one started within minGapMs.
// Returns [httpStatus, body].
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

  const res = await gh(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: 'main' }),
  });
  if (res.status !== 204) return [502, { state: 'error', message: `GitHub dispatch failed (${res.status})` }];
  return [202, { state: 'started' }];
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method !== 'POST') return reply(405, { state: 'error', message: 'Use POST' });
    const [status, body] = await startUpdate(env, MIN_GAP_MS);
    return reply(status, body);
  },

  // Backup clock: skip if any run (GitHub's own schedule, or a Refresh press)
  // started in the last 4 minutes, so runs stay about 5 minutes apart.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(startUpdate(env, 4 * 60 * 1000));
  },
};
