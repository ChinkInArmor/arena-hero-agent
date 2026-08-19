# Hybrid Strategic Control

The Agent separates long-horizon strategy from Tick-critical execution.

## Control Boundary

The deterministic layer always owns:

- action legality and one-action-per-entity limits;
- pathfinding, collision reservations, and cargo delivery;
- legal combat geometry and emergency Core defense;
- compatibility hold, recovery, and plan submission;
- the final decision when strategic advice is missing, stale, or invalid.

The strategic layer owns only bounded preferences and deterministic local state:

- posture preference: `CONSOLIDATE`, `EXPAND`, `CONTEST`, or `PRESSURE`;
- economy, territory, combat, safety, and Beacon weights;
- the percentage of economic Workers biased toward exploration;
- a decision TTL;
- local `economic_target`, `military_target`, `committed_population`,
  `production_ceiling`, and `population_health` diagnostics.

The model adviser cannot set unit targets, production ceilings, migration commands,
Beacon execution, self-destruction, emergency reserves, or action legality. The
local deterministic controller retains those authorities.

A model cannot return SDK actions, object identifiers, paths, prompts for later
execution, or arbitrary configuration. Output with missing, additional, or
out-of-range fields is rejected as a whole.

## Deterministic Planner

No model is required. The local planner starts with the validated 24-population
profile and opens growth only after a complete 32-Tick window shows productive,
low-congestion deposits and at least two known resources. Storage saturation alone
is not economic evidence. A visible enemy Core raises the military target without
silently replacing the economic target. Existing population above the local
production ceiling enters `OVEREXTENDED`: discretionary production freezes and
Units are not automatically destroyed. Beacon contesting remains gated by the
runtime Beacon policy, deterministic valuation, and safety checks.

Resource assignment remains a deterministic minimum-cost matching problem.
Path cost is dominant; resource density, return safety, territorial reach,
staleness, and assignment stability influence otherwise comparable routes.
Scouting similarly combines stale coverage, expected chunk quota, territorial
reach, and explicit Beacon priority.

Optional production compares each target deficit and its strategic utility with
the current official `unit_cost()`. It therefore chooses the highest marginal
Worker, Vanguard, or Ranger purchase rather than following a fixed late-game
unit sequence. Recovery, threat pressure, cargo constraints, cooldowns, dynamic
prices, and resource reserves still gate every spawn. Surplus military patrols
and the optional Vanguard Beacon runner remain subordinate to deterministic Core
defense, local combat, healing, evasion, and compatibility hold.

## Optional Model Adviser

The adviser is disabled by default. When enabled, a daemon worker sends only an
aggregate strategic snapshot every 128-512 accepted Tick numbers. It returns only
allow-listed posture and weight preferences; the local population state and
production ceiling are preserved when advice is applied. HTTP latency
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
