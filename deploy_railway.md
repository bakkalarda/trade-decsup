# Deploying Trade DSS + OpenClaw on Railway

This guide walks through deploying the Trade Decision Support System with OpenClaw on [Railway](https://railway.com) for 24/7 operation.

Railway runs the OpenClaw gateway, cron scheduler, and the Python DSS engine in a single container with a persistent volume for state, config, and the SQLite database.

---

## Prerequisites

- A [Railway account](https://railway.com) (Hobby plan at $5/month is sufficient)
- A [GitHub account](https://github.com) (Railway deploys from GitHub)
- At least one LLM API key (Anthropic recommended, or OpenAI/Google/OpenRouter)
- A Telegram bot token (created via @BotFather)

---

## Architecture on Railway

```
Railway Service (single container)
├── Node.js wrapper server (:8080, public)
│   ├── /setup        → Setup wizard (password-protected)
│   ├── /openclaw     → Control UI
│   └── /*            → Reverse proxy to gateway
├── OpenClaw Gateway (:18789, loopback)
│   ├── Telegram channel ↔ your bot
│   ├── Cron scheduler (4H, daily, weekly, position checks)
│   ├── LLM provider (Anthropic/OpenAI/etc.)
│   └── exec tool → calls `dss` CLI
└── Python DSS (/opt/trade-decsup/.venv)
    └── `dss` CLI (scan, macro-state, structure, zones, position)

Railway Volume (/data) — persists across redeploys
├── .openclaw/           → OpenClaw config, credentials, memory
│   └── openclaw.json
├── workspace/           → AGENTS.md, SOUL.md, TOOLS.md, skills/
└── trade_dss.db         → SQLite database (OHLCV cache, positions, alerts)
```

---

## Step 1 — Create the Telegram Bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot`, choose a display name (e.g. "Trade DSS") and a username (e.g. `trade_dss_bot`)
3. Copy the bot token BotFather gives you — it looks like `123456789:AAHxx...`
4. Send `/setprivacy` → select your bot → **Disable** (so the bot can read messages in groups)

Keep the token handy for Step 6.

---

## Step 2 — Push Trade DSS to GitHub

Railway deploys from a GitHub repository. Push the entire project:

```bash
cd /path/to/trade_decsup

git init
git add -A
git commit -m "Initial commit: Trade DSS + OpenClaw workspace"

# Create a private repo on GitHub, then:
git remote add origin git@github.com:YOUR_USERNAME/trade-decsup.git
git branch -M main
git push -u origin main
```

> **Tip:** Keep the repo private since it contains your trading strategy logic.

---

## Step 3 — Fork the OpenClaw Railway Template

The official template lives at:

**https://github.com/codetitlan/openclaw-railway-template**

1. Click **Fork** to create your own copy
2. Clone your fork locally:

```bash
git clone git@github.com:YOUR_USERNAME/openclaw-railway-template.git
cd openclaw-railway-template
```

---

## Step 4 — Extend the Dockerfile with the DSS

Edit the `Dockerfile` in your forked template. Add the following block **before** the final `CMD ["node", "src/server.js"]` line:

```dockerfile
# ===== Trade DSS Integration =====
# Install Python tooling (python3 already exists from base image)
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3-pip python3-venv python3-dev \
 && rm -rf /var/lib/apt/lists/*

# Clone and install the DSS engine
ARG DSS_GIT_REF=main
RUN git clone --depth 1 --branch "${DSS_GIT_REF}" \
    https://github.com/YOUR_USERNAME/trade-decsup.git /opt/trade-decsup \
 && cd /opt/trade-decsup \
 && python3 -m venv /opt/trade-decsup/.venv \
 && /opt/trade-decsup/.venv/bin/pip install --no-cache-dir -e .

# Put the dss CLI on PATH
ENV PATH="/opt/trade-decsup/.venv/bin:${PATH}"

# Stage workspace defaults so they get copied to the volume on first boot
RUN mkdir -p /opt/workspace-defaults \
 && cp -r /opt/trade-decsup/openclaw/* /opt/workspace-defaults/ \
 && cp -r /opt/trade-decsup/config /opt/workspace-defaults/dss-config
```

> **Important:** Replace `YOUR_USERNAME` with your actual GitHub username in the `git clone` URL.
> If your DSS repo is private, you'll need to use a [GitHub personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) in the URL or add a deploy key.

---

## Step 5 — Add the Workspace Init Script

Create the file `scripts/init-workspace.sh` in your forked template:

```bash
#!/usr/bin/env bash
# Copies workspace defaults to the Railway volume on first boot.
# On subsequent boots the volume already has the files, so this is a no-op.
set -euo pipefail

WORKSPACE_DIR="${OPENCLAW_WORKSPACE_DIR:-/data/workspace}"
DEFAULTS_DIR="/opt/workspace-defaults"
DSS_DB_DIR="/data"

# --- Workspace files ---
if [ -d "$DEFAULTS_DIR" ] && [ ! -f "$WORKSPACE_DIR/AGENTS.md" ]; then
  echo "[init] First boot detected — copying workspace defaults to $WORKSPACE_DIR"
  mkdir -p "$WORKSPACE_DIR"
  cp -rn "$DEFAULTS_DIR"/* "$WORKSPACE_DIR/"
fi

# --- DSS database ---
# Ensure DSS writes its SQLite DB to the persistent volume
export DSS_DB_PATH="${DSS_DB_DIR}/trade_dss.db"

# Initialize DB if it doesn't exist yet
if [ ! -f "$DSS_DB_PATH" ]; then
  echo "[init] Initializing DSS database at $DSS_DB_PATH"
  python3 -c "
import os; os.environ['DSS_DB_PATH'] = '${DSS_DB_PATH}'
from dss.storage.database import init_db; init_db()
print('  Database ready')
"
fi

# --- DSS config ---
# Point DSS at the config on the volume (if present), else fall back to baked-in
if [ -d "$WORKSPACE_DIR/dss-config" ]; then
  export DSS_CONFIG_DIR="$WORKSPACE_DIR/dss-config"
fi

exec "$@"
```

Then update the end of the `Dockerfile` to use it:

```dockerfile
# ===== Entrypoint =====
COPY scripts/init-workspace.sh /usr/local/bin/init-workspace.sh
RUN chmod +x /usr/local/bin/init-workspace.sh

ENTRYPOINT ["/usr/local/bin/init-workspace.sh"]
CMD ["node", "src/server.js"]
```

> **Note:** This replaces the original bare `CMD` at the bottom of the Dockerfile. The entrypoint runs the init logic, then hands off to the Node.js wrapper via `exec "$@"`.

Commit and push:

```bash
git add -A
git commit -m "Add Trade DSS integration + workspace init"
git push origin main
```

---

## Step 6 — Deploy on Railway

### 6a. Create the project

1. Go to **https://railway.com/dashboard**
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your forked `openclaw-railway-template` repository
4. Railway detects the Dockerfile and begins building

### 6b. Add a persistent volume

1. Click on your service in the Railway dashboard
2. Go to **Settings** → **Volumes** (or right-click the service → **Add Volume**)
3. Mount path: **`/data`**
4. Click **Add**

This volume persists OpenClaw config, workspace files, credentials, memory, and the DSS SQLite database across redeploys.

### 6c. Set environment variables

In your service's **Variables** tab, add:

| Variable | Value | Notes |
|---|---|---|
| `SETUP_PASSWORD` | `your-strong-password` | Protects the `/setup` wizard |
| `OPENCLAW_STATE_DIR` | `/data/.openclaw` | OpenClaw config & credentials |
| `OPENCLAW_WORKSPACE_DIR` | `/data/workspace` | Agent workspace (AGENTS.md etc.) |
| `OPENCLAW_GATEWAY_TOKEN` | Use `${{ secret() }}` | Auto-generated gateway auth token |
| `DSS_DB_PATH` | `/data/trade_dss.db` | SQLite on persistent volume |
| `FRED_API_KEY` | *(your key, optional)* | For FRED real yield data |

> `${{ secret() }}` is Railway's built-in secret generator — use it for the gateway token.

### 6d. Enable public networking

1. Go to **Settings** → **Networking**
2. Click **Generate Domain** (or add a custom domain)
3. Railway assigns something like `your-app.up.railway.app`

### 6e. Deploy

Click **Deploy** (or it auto-deploys on push). Watch the build logs — the first build takes 5-10 minutes because it compiles OpenClaw from source and installs Python dependencies.

---

## Step 7 — Run the Setup Wizard

1. Open `https://your-app.up.railway.app/setup` in your browser
2. Enter the `SETUP_PASSWORD` when prompted (username can be anything)
3. The wizard walks you through:
   - **AI Provider** — Select your provider (e.g. Anthropic) and paste your API key
   - **Telegram** — Paste the bot token from Step 1
4. Click **Complete Setup**

The wizard runs onboarding, writes config to the volume, and starts the gateway.

---

## Step 8 — Pair Your Telegram Account

1. Open Telegram and send any message to your bot (e.g. "hello")
2. The bot replies with a **pairing code**
3. Approve the pairing — two options:
   - **Via the setup wizard:** Go to `https://your-app.up.railway.app/setup`, look for the pairing approval section, and enter the code
   - **Via the Control UI:** Go to `https://your-app.up.railway.app/openclaw` and approve from there

After approval, the bot responds to your messages.

> **Find your Chat ID:** DM [@userinfobot](https://t.me/userinfobot) on Telegram — it replies with your numeric user ID. You need this for cron job delivery.

---

## Step 9 — Install Cron Jobs

Open a Railway shell (Dashboard → your service → **Shell** button), or use the Railway CLI:

```bash
railway shell
```

Then install the cron jobs. Replace `YOUR_CHAT_ID` with your numeric Telegram user ID:

```bash
# 4-Hour scan (primary heartbeat — every 4 hours at :05)
openclaw cron add \
  --name "4H Scan Cycle" \
  --cron "5 */4 * * *" \
  --tz "UTC" \
  --session isolated \
  --message "Run the 4H scan cycle. First search for macro headlines and event calendar. Then run: dss scan --asset all --timeframe 4h. Then run: dss position check. Report any alerts, position updates, or risk warnings to the user. If nothing notable, send a brief all-clear." \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID"

# Daily update (after UTC daily close at 00:05)
openclaw cron add \
  --name "Daily Update" \
  --cron "5 0 * * *" \
  --tz "UTC" \
  --session isolated \
  --message "Run the daily update cycle. Run dss scan --asset all --timeframe 4h for all assets. Run dss structure --asset BTC --timeframe 1d, then ETH, XRP, SOL, XAU, XAG. Note any stage transitions. Check positions. Write a daily summary to memory. Report to user." \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID"

# Weekly update (Monday 00:05 UTC — use a smarter model)
openclaw cron add \
  --name "Weekly Update" \
  --cron "5 0 * * 1" \
  --tz "UTC" \
  --session isolated \
  --model "opus" \
  --thinking high \
  --message "Run the weekly update. Run dss macro-state for regime assessment. Run dss structure --asset BTC --timeframe 1w, then ETH, XRP, SOL, XAU, XAG. Run dss zones --asset BTC --timeframe 1w for all assets. Reassess macro regime with web_search for weekly economic outlook. Write a comprehensive weekly summary to memory and send to user." \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID"

# Position check (every 30 minutes)
openclaw cron add \
  --name "Position Check" \
  --cron "*/30 * * * *" \
  --tz "UTC" \
  --session isolated \
  --message "Run dss position check. If any stops hit, targets reached, or positions need attention, alert the user immediately. If all positions are fine, do not send a message (reply HEARTBEAT_OK)." \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID"
```

Verify all four jobs are registered:

```bash
openclaw cron list
```

---

## Step 10 — Verify End-to-End

### Test DSS CLI

In Railway shell:

```bash
dss status                                   # Connector health
dss data refresh --asset BTC --timeframe 4h  # Pull data
dss scan --asset BTC --timeframe 4h          # Full scan
```

### Test via Telegram

DM your bot:

> "Run `dss scan --asset BTC --timeframe 4h` and give me the analysis"

The agent should execute the command, interpret the JSON, and reply with a trader-friendly summary.

### Test cron

Force-run the 4H scan:

```bash
openclaw cron list                   # Find the job ID
openclaw cron run <4H_SCAN_JOB_ID>  # Trigger it now
```

You should receive a Telegram message within a minute or two.

---

## Updating the DSS

When you push changes to your `trade-decsup` GitHub repo:

1. The Railway template references your DSS repo via `git clone` at build time
2. Trigger a **redeploy** in Railway (manually, or set up a [deploy hook](https://docs.railway.com/guides/webhooks))
3. Railway rebuilds the image with the latest DSS code
4. The `/data` volume persists — no data loss

To update OpenClaw workspace files (AGENTS.md, SOUL.md, etc.) **without** a redeploy:

1. Open Railway shell
2. Edit files directly in `/data/workspace/`
3. Changes take effect on the next agent session

---

## Updating OpenClaw Itself

To pin a specific OpenClaw version, set the `OPENCLAW_GIT_REF` build arg in your Railway service:

1. Go to **Settings** → **Build** → **Build Arguments**
2. Set `OPENCLAW_GIT_REF` to a tag (e.g. `v1.2.3`) or branch name
3. Redeploy

---

## Troubleshooting

### Build fails on DSS `git clone`

If your DSS repo is private, the Docker build can't access it without credentials. Options:

- **Make the repo public** (simplest)
- **Use a GitHub PAT** in the clone URL as a build arg:
  ```
  ARG GITHUB_TOKEN
  RUN git clone --depth 1 https://${GITHUB_TOKEN}@github.com/YOUR_USERNAME/trade-decsup.git /opt/trade-decsup
  ```
  Then set `GITHUB_TOKEN` as a Railway variable.

### Gateway disconnects in the Control UI

Copy the `OPENCLAW_GATEWAY_TOKEN` value from Railway Variables and paste it into the token field on the Control UI overview page, then click **Connect**.

### DSS can't write to database

Make sure `DSS_DB_PATH` is set to `/data/trade_dss.db` (on the volume). If unset, the DSS writes to the container filesystem, which doesn't survive redeploys.

### Cron jobs not firing

```bash
openclaw cron list          # Check jobs exist
openclaw gateway status     # Check gateway is running
```

If the gateway restarted, cron jobs should auto-load from the persisted config.

### Data connectors failing

Some connectors (yfinance for metals/indices) may fail outside market hours. This is normal — the DSS handles stale data gracefully. Crypto connectors (ccxt) run 24/7.

### Checking logs

- **Railway dashboard:** Click your service → **Deployments** → select a deployment → **View Logs**
- **Real-time:** Use the Railway CLI: `railway logs --follow`

---

## Cost Estimate

| Item | Estimated Cost |
|---|---|
| Railway Hobby plan | $5/month |
| Compute (single container, ~256MB RAM) | ~$2-5/month (usage-based) |
| LLM API (6 scans/day + 48 position checks + conversations) | ~$10-30/month (varies by provider) |
| **Total** | **~$17-40/month** |

> The main variable is LLM API cost, which depends on your provider, model, and how chatty you are with the bot. Anthropic Haiku for routine scans and Opus for weekly deep-dives is a good cost/quality balance.

---

## Backup & Migration

### Export a full backup

Visit `https://your-app.up.railway.app/setup/export` (password-protected). This downloads a `.tar.gz` of the entire `/data` volume — config, credentials, workspace, database, and memory.

### Migrate to a different host

1. Export the backup
2. On the new host, install OpenClaw and the DSS
3. Extract the backup into the appropriate directories
4. Start the gateway

---

## Data Flow Summary

```
Cron (every 4H)
  → OpenClaw wakes agent in isolated session
    → Agent runs web_search for headlines
    → Agent runs: exec dss scan --asset all --timeframe 4h
    → Agent runs: exec dss position check
    → Agent synthesizes results
    → Agent sends Telegram message to you

You DM the bot: "How does BTC look?"
  → Agent runs: exec dss scan --asset BTC --timeframe 4h
  → Agent gives you a full analysis in natural language
```
