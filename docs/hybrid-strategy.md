# Hybrid Strategic Control

The Agent separates long-horizon strategy from Tick-critical execution.

## Control Boundary

The deterministic layer always owns:

- action legality and one-action-per-entity limits;
- pathfinding, collision reservations, and cargo delivery;
- legal combat geometry and emergency Core defense;
- compatibility hold, recovery, and plan submission;
- the final decision when strategic advice is missing, stale, or invalid.

The strategic layer owns only bounded parameters:

- posture: `CONSOLIDATE`, `EXPAND`, `CONTEST`, or `PRESSURE`;
- Worker, Vanguard, and Ranger targets;
- a population limit;
- economy, territory, combat, safety, and Beacon weights;
- the percentage of economic Workers biased toward exploration;
- a decision TTL.

A model cannot return SDK actions, object identifiers, paths, prompts for later
execution, or arbitrary configuration. Output with missing, additional, or
out-of-range fields is rejected as a whole.

## Deterministic Planner

No model is required. The local planner starts with the validated 24-population
profile and opens bounded growth only when it observes evidence such as saturated
storage or a productive, low-congestion deposit window. It can select combat
growth when an enemy Core is visible and Beacon contesting only when the explicit
Beacon policy is `pursue`.

Resource assignment remains a deterministic minimum-cost matching problem.
Path cost is dominant; resource density, return safety, territorial reach,
staleness, and assignment stability influence otherwise comparable routes.
Scouting similarly combines stale coverage, expected chunk quota, territorial
reach, and explicit Beacon priority.

Optional production compares each target deficit and its strategic utility with
the current official `unit_cost()`. It therefore chooses the highest marginal
Worker, Vanguard, or Ranger purchase rather than following a fixed late-game
unit sequence. Recovery, threat pressure, cargo constraints, cooldowns, dynamic
prices, and resource reserves still gate every spawn.

## Optional Model Adviser

The adviser is disabled by default. When enabled, a daemon worker sends only an
aggregate strategic snapshot every 128-512 accepted Tick numbers. HTTP latency
does not block the current Tick plan. A successful decision is valid for 128-1024
Ticks; stale advice disappears automatically.

Supported transports:

- `openai-compatible`: `/chat/completions`, for OpenAI, DeepSeek, Ollama, vLLM,
  and compatible gateways;
- `anthropic`: `/v1/messages`.

Configuration uses process environment variables, but the credential itself is
read from a separate file:

```dotenv
ARENA_STRATEGY_PROVIDER=openai-compatible
ARENA_STRATEGY_BASE_URL=https://api.openai.com/v1
ARENA_STRATEGY_MODEL=gpt-5-mini
ARENA_STRATEGY_API_KEY_FILE=/etc/arena-hero-agent-strategy.key
ARENA_STRATEGY_INTERVAL_TICKS=256
ARENA_STRATEGY_TIMEOUT_SECONDS=8
```

For a local OpenAI-compatible endpoint, omit `ARENA_STRATEGY_API_KEY_FILE`:

```dotenv
ARENA_STRATEGY_PROVIDER=openai-compatible
ARENA_STRATEGY_BASE_URL=http://127.0.0.1:11434/v1
ARENA_STRATEGY_MODEL=qwen3:8b
```

Install a server credential outside the checkout with restrictive permissions:

```bash
sudo install -o root -g arena-hero -m 0640 \
  /secure/model-api-key /etc/arena-hero-agent-strategy.key
```

Do not place a model key in `.env`, `/etc/arena-hero-agent/runtime.env`, a CLI
argument, a log, or source control. The model key is separate from
`ARENA_HERO_API_KEY`.

## Failure Behavior

The following all preserve a valid deterministic plan:

- no adviser configuration or no key for a local endpoint;
- timeout, DNS, TLS, HTTP, rate-limit, or provider failure;
- malformed JSON, schema drift, extra fields, invalid ranges, or expired TTL;
- a request still running at the next cadence boundary;
- recovery, compatibility hold, pre-evasion, engagement, or breakout.

Failures are reduced to a non-sensitive outcome class such as
`failed:TimeoutException`; response bodies, prompts, credentials, and private
object identifiers are not logged.
