# Read-only operations Dashboard

The first Dashboard release is a single-Agent, read-only operations surface for
`https://arena.911439925.xyz`. It is not a game-control API.

## Data boundary

After an accepted Turn, the Agent submits an aggregate observation to a bounded
in-process queue. A daemon writer atomically replaces `snapshot.json` and appends
selected events to daily JSONL files. Disk latency, a full queue, malformed output,
or a stopped Dashboard cannot reject a Turn or stop the Agent.

The observation contract includes aggregate economy, population, fleet, Core,
worker-mode, battlefield, strategy, and adviser telemetry. Event values use an
explicit numeric whitelist. The producer does not serialize player names, Unit
IDs, authorization headers, model prompts, model responses, or raw logs.

`arena-hero-dashboard` validates the producer files with Pydantic before writing
SQLite. SQLite uses WAL mode. Detailed snapshots and events are retained for 7
days; hourly aggregates are retained for 90 days.

## Process isolation

The deployment creates these identities and paths:

| Resource | Ownership | Purpose |
|---|---|---|
| `arena-hero-observe` | system group | One-way observation exchange |
| `arena-hero` | member of observation group | Writes redacted producer files |
| `arena-hero-dashboard` | primary observation group | Reads producer files and owns SQLite |
| `/var/lib/arena-hero-observability/inbox` | `root:arena-hero-observe`, `2770` | Snapshot and daily events |
| `/var/lib/arena-hero-dashboard` | `arena-hero-dashboard:arena-hero-observe`, `0750` | SQLite state |

The Dashboard account is not a member of `arena-hero`. It cannot read the Arena
Hero API environment file or the strategic adviser key file. Its systemd unit
has a strict filesystem policy and listens only on `127.0.0.1:8765`.

## API

The browser uses same-origin, read-only endpoints:

- `GET /api/v1/overview`
- `GET /api/v1/history?range=1h|6h|24h|7d|30d|90d`
- `GET /api/v1/events?limit=80&category=COMBAT`
- `GET /api/v1/health`

No mutation route, terminal, service action, configuration editor, raw log route,
or credential route exists.

## Build

```bash
cd dashboard
npm ci
npm run build
cd ..
python -m unittest -v test_arena_dashboard test_arena_observability
```

The generated `dashboard/dist` assets are committed so the immutable systemd
release does not require Node.js on the server. CI rebuilds the assets and fails
when the committed output differs.

## Systemd deployment

The normal transactional Agent installer creates the observation identities,
installs the Dashboard backend and frontend, and installs
`arena-hero-dashboard.service`. It does not enable the public Dashboard on a
first installation. Once the edge is ready, run the dedicated installer.

Generate a bcrypt hash interactively. The cleartext password must never be a
command argument, environment file, Git file, chat message, or log entry:

```bash
umask 077
caddy hash-password > /root/arena-dashboard-password.bcrypt
```

Then install the edge configuration:

```bash
sudo sh scripts/install-dashboard.sh \
  --password-hash-file /root/arena-dashboard-password.bcrypt
sudo rm -f /root/arena-dashboard-password.bcrypt
```

The installer stores only the bcrypt hash in `/etc/arena-hero-dashboard.env`,
adds an isolated Caddy site snippet, enables the loopback Dashboard service,
validates Caddy, reloads it, and probes the local API.

## Caddy and Cloudflare

The public path is:

```text
Cloudflare proxy -> Caddy HTTPS + Basic Auth -> 127.0.0.1:8765
```

The Caddy site removes the Authorization header from access logs and applies
HSTS, frame, referrer, MIME, and permissions headers. The backend also emits
security headers and disables API caching.

Keep the `arena` DNS record proxied. The origin firewall should expose only the
ports deliberately used by this host; `8765` must never be opened publicly.
Before changing Cloudflare SSL from Automatic to Full (strict), verify every
other proxied hostname on the zone has a valid origin certificate.

## Verification

```bash
systemctl status arena-hero-agent.service arena-hero-dashboard.service caddy
ss -lntp | grep -E ':(80|443|8765)\\b'
curl -fsS http://127.0.0.1:8765/api/v1/health
curl -I https://arena.911439925.xyz
```

The local API probe should succeed. The public request without credentials must
return `401`. After authentication, inspect `/api/v1/overview` in browser dev
tools and verify that no identifier, raw log, authorization value, or model body
is present.
