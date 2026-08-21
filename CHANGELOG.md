# Changelog

All notable changes to this project will be documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [Unreleased]

### Added

- Hash-locked runtime and build dependency sets shared by local bootstrap, CI, Docker, and systemd installation.
- Versioned systemd releases with atomic `current` activation, interruption journaling, service-state restoration, and a standalone rollback command.
- Public documentation navigation, executable clone-first quick starts, compatibility fields in bug reports, and clearer community reporting guidance.
- Release tags now pass the complete reusable CI workflow before publishing, with version validation, SBOM, provenance, and image-digest reporting.
- Tolerant stationary-Core confirmation across short visibility gaps, while still requiring three real same-position observations before a raid.
- Structured v0.11 upkeep due/paid/deficit and excess-Unit damage diagnostics with deterministic supervisor and optional model-review triggers.
- Bounded long-range raids against confirmed stationary, unprotected Cores, with strike-distance hysteresis and immediate combat-pressure recall.
- Gameplay v0.13 and official SDK 0.2.8 compatibility, including conservative Ranger cell fire against a confirmed stationary Core during short visibility gaps.
- Hierarchical lifecycle and threat assessment with explicit posture/reason diagnostics for alerts, pre-evasion, engagement, multi-axis breakout, recovery, and compatibility hold.

### Changed

- The Docker base image is pinned to an immutable multi-architecture digest.
- GitHub Actions are pinned to full commit SHAs while retaining their reviewed major-version annotations.
- systemd upgrades now preflight host requirements, restart the Agent after compatibility validation, and support explicit supervisor, AI, and optimizer disable paths.
- Docker Compose now uses the same graceful `SIGINT` shutdown contract as systemd.
- Resource targets now use deterministic minimum-cost Worker matching with limited intent stickiness instead of preserving a worse assignment indefinitely.
- Scout routes prefer less recently covered chunks and rotate after three consecutive non-improving Ticks.

### Fixed

- The saturated-stock overflow valve no longer farms Workers
  unconditionally: a full Core now rebalances by marginal utility
  (deficit x weight / cost) so it fills whichever wing is actually short
  (defenders first when RANGERs are depleted), and once every strategic
  target is met it keeps scaling the defender wings to grow population
  toward the emergency ceiling instead of idling at full stock. The
  worker pool is capped at worker_target + 6 so a saturated economy
  cannot keep growing miners beyond the available ore (previously a
  full stock grew Workers to 39 and mined the map dry while RANGERs sat
  at 2, then froze in STOCKPILE_HOLD at 250/250 with no ore left).
- A fully saturated Core (resources equal to capacity) no longer idles
  forever: the saturated-stock short circuit in `_growth_is_ready` is
  restored, the strategic production ceiling is waived while the stock
  sits exactly at capacity, and a dedicated saturated-expansion branch
  spawns a Worker to spend the dead margin and grow capacity (bounded by the
  emergency ceiling and the resource reserve). Previously a full stock with
  worker/defender targets already met would never spawn, and with an
  OVEREXTENDED population above the production ceiling all production was
  blocked, so a 190/190 stock stalled for hours until an external event
  freed space.
- Core delivery no longer deadlocks when a flushed Worker parks on the Core
  and every passable Core neighbor is packed two cargo Workers deep (the
  game limits the Core cell to one unit, so the resident had to vacate
  first, and packed ring cells sealed it in forever). A packed delivery-ring
  Worker now steps aside to thin the ring, a resident Worker can vacate the
  Core through a single-friendly cell, and a full-stock cargo Worker can
  clear a sealed Core the same way, so deliveries resume and resource stock
  keeps growing.
- Full-stock delivery no longer storms a Core that cannot accept cargo: at
  a saturated stock the carrier on the Core holds position (CORE_HOLD)
  instead of being evicted each tick, other carriers wait in the ring
  (RING_HOLD) or at distance (STOCKPILE_HOLD), and the delivery-ring chain
  can start from a doubly-occupied neighbor so a sealed ring frees the Core
  for a spawn that spends the surplus. Production environments showed ~85% of
  ticks with 10+ RETURN_BLOCKED carriers frozen at a full stock; the sealed
  ring now opens and the stock drains again.

### Changed

- A Core under direct attack with adjacent defenders now stands and fights
  instead of relocating: the raid replay showed 8 core relocations in 90
  ticks with the defenders chasing the zig-zagging Core and nearly zero
  return fire. EVADE now cancels once the Core is actually being hit and
  defenders can answer, and an EVADE relocation keeps its direction for a
  few ticks instead of flipping every time the raider crosses.
- Combat pressure is sticky: the tactical loop rises to ENGAGED instantly
  but now holds a few ticks after the last contact, so posture, recall and
  economy gates do not flap mid-battle. Worker recruitment stays suppressed
  while the hold is active, and idle workers stop opening new mining runs
  while the fleet is engaged.
- A stock pinned at the storage ceiling no longer idles during combat: any
  unit death shrinks capacity by five and overflows the Core
  (CORE_RESOURCE_OVERFLOW_DESTROYED), destroying one unit per tick. With a
  real threat at the Core and storage within the safety padding, the Core
  spends the overflow margin on a defender instead of waiting through the
  engagement.

## [0.1.0] - 2026-08-03

### Added

- Cross-platform local bootstrap and launch scripts.
- Docker and Docker Compose deployment with runtime secret mounting.
- Hardened systemd installer with optional supervisor, AI review, and optimizer tiers.
- GitHub CI, community health files, and release documentation.
- Accepted-Turn heartbeat and deterministic unattended health checks for systemd and Compose.
- Deterministic resource-first tactic, structured diagnostics, compatibility monitor, read-only supervisor, and bounded runtime optimizer.
- Tag-driven GHCR release images for build-free Compose deployment.

### Changed

- AI supervisor review now requires explicit `ARENA_SUPERVISOR_AI_ENABLED=true` opt-in.
- Model IDs and model credentials are no longer embedded in systemd units.
- The main systemd service no longer depends on a supervisor refresh timer.
- systemd installation now requires an immediate compatibility check before starting the Agent.
