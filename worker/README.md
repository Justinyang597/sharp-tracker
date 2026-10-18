# Refresh button relay (Cloudflare Worker)

The site's **Refresh** button starts a new pull from Action Network by calling this
Worker, which starts the GitHub workflow. The GitHub key lives only in the Worker's
secret settings, never in the public site.

## 1. Make a GitHub key that can only start this workflow

1. Go to https://github.com/settings/personal-access-tokens/new
2. **Token name:** `sharp-tracker refresh`. **Expiration:** the longest offered (or "No expiration").
3. **Repository access:** *Only select repositories* → `sharp-tracker`.
4. **Permissions** → **Repository permissions** → **Actions:** *Read and write*. Leave everything else as is.
5. **Generate token** and copy it.

## 2. Create the Worker

1. Sign up or log in at https://dash.cloudflare.com (the free plan is enough).
2. **Workers & Pages** → **Create** → **Create Worker** (start from "Hello World").
   Name it `sharp-tracker-refresh` → **Deploy**.
3. **Edit code**, replace everything with the contents of `worker.js`, then **Deploy**.
4. Back on the Worker: **Settings** → **Variables and Secrets** → **Add** →
   type **Secret**, name `GH_TOKEN`, value = the GitHub key → **Deploy**.
5. Copy the Worker's URL (like `https://sharp-tracker-refresh.<you>.workers.dev`).

Then put that URL in `REFRESH_URL` near the bottom of `docs/index.html`.
