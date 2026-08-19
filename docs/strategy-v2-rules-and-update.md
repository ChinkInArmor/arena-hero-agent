# Strategy V2 Rules And Update

## Status

Rule review is complete. Strategy V2 implementation is intentionally pending.
This document is the handoff point for the next implementation session. It records
verified gameplay rules, the current production diagnosis, and the design boundary
that must be preserved during implementation.

No production behavior, deployment configuration, or live game state was changed
as part of this review.

## Evidence And Precedence

The current compatibility target is:

- HTTP and WebSocket API: v0.1
- Gameplay rules: v0.14
- Official Python SDK: `arena-hero==0.2.9`
- Reviewed server revision named by the official release policy:
  `b24cfcd22b82c0af0f3993397d2696629762e7e5`
- Reviewed SDK revision: `423d252adcca439669adb3e7b04252e53b4430bd`

Evidence was checked in this order:

1. `arena-hero-doc`: public gameplay and API documentation.
2. `arena-hero-skill`: complete Agent rule snapshot, generated from the docs
   revision, plus rule-completeness tests and tactic-authoring constraints.
3. `arena-hero-python`: official SDK models, rule helpers, and SDK tests.
4. `arena-hero-web`: demonstration-client behavior only; never a source of
   server authority.
5. Live observations from the deployed Agent, used only to diagnose the current
   account and not to replace the published contract.

The server source repository named by the official policy,
`https://github.com/arena-hero/arena-hero`, is not publicly readable at the time
of this review. The official documentation says that server code, database
constraints, and server tests decide runtime behavior when prose disagrees with
implementation. Any unresolved edge case below must therefore be tested with an
isolated account before it becomes a production tactic assumption.

## Verified Gameplay Rules

### Population And Economy

- Population is the number of living Workers, Vanguards, and Rangers. The Core
  is not counted.
- v0.14 has no per-Tick maintenance charge and no automatic upkeep damage.
- There is no published global population cap. Production is constrained by
  resources, dynamic prices, one spawn per Tick, and cell occupancy.
- Core storage is a strict `max(10, population * 5)` resources. If population
  falls, stored resources above the new capacity are destroyed during resolution.
- Base Unit prices are Worker `5`, Vanguard `10`, and Ranger `12`.
- For population `N` when the Core action resolves:

  ```text
  k = max(0, floor((N - 20) / 5) + 1)
  price = round_half_up(base_price * (13 / 10)^k)
  ```

- The 20th Unit is base-priced; the 21st is the first increased-price Unit.
- Same-Tick Unit self-destruction and combat deaths occur before spawn pricing,
  so they can lower the settled price.
- `CORE_SPAWN_SUCCEEDED.values.cost` and
  `CORE_SPAWN_FAILED/INSUFFICIENT_RESOURCES.values.required` are authoritative.
- A Core can create at most one Unit per Tick. The Core occupies one of two cell
  slots, so a Unit already sharing the Core cell blocks another spawn.
- A newly spawned Unit cannot act or be attacked during its creation Tick.
- Initial and respawn Workers are free.
- Unit `SELF_DESTRUCT` has no refund. It removes the Unit before movement and
  spawn pricing, drops Worker cargo, and can drop a carried Beacon.

### Resolution Order That Matters To Strategy

The relevant order is:

1. Unit self-destruction and cargo drops.
2. Population-related Core resource overflow destruction.
3. Unit movement and Core migrations reaching their fourth Tick.
4. New Core `START_MOVE` validation.
5. Beacon pickup and drop.
6. Worker harvest and deposit.
7. Combat and combat deaths.
8. Core destruction, combat loot transfer, and Core self-destruction.
9. Unit healing in raw UUID order.
10. Population snapshot and stationary Core healing, shield repair, or spawn.
11. Same-Tick Core respawn attempts.
12. Resource refill after every fourth resolved Tick.

This order means that production decisions must distinguish current-state price
previews from settled spawn results, and that reducing population can destroy
stored resources before production is priced.

### Core Migration

- Core movement is cardinal and moves one cell per migration.
- One cell requires four logical Ticks:

  ```text
  START_MOVE resolves -> progress 1/4
  next Tick            -> progress 2/4
  next Tick            -> progress 3/4
  next Tick            -> real movement attempt
  ```

- The migration continues without resubmitting an action. `WAIT` does not pause.
- Changing direction requires `CANCEL_MOVE`, which clears progress.
- While migrating, the Core cannot spawn, heal, repair its shield, pick up or
  drop the Beacon, or receive Worker deposits. It remains attackable and keeps
  its inventory.
- Colocated Units do not move with the Core.
- A carried Beacon remains at the Core's current logical position until the real
  move succeeds, then follows the Core.
- Completing or cancelling migration restores normal Core functions on the next
  Tick.
- Starting a migration reserves no destination. Other objects may enter or pass
  through the destination before the fourth-Tick attempt.
- The final move participates in the same global dependency graph as Unit
  movement and can fail because of terrain, occupancy, contest, swap,
  dependency, enemy entry, coordinate bounds, or final cell capacity.
- A failed final move leaves the Core at its origin and clears progress.

Strategic migration is therefore a repeated four-Tick commitment, not a cheap
teleport or a separate game mode. It must be evaluated separately from tactical
retreat.

### Champion Beacon

- There is exactly one Beacon. It starts at `[0, 0]` and cannot be destroyed.
- Its coordinate is public; ground/carried status is visibility-limited.
- Any Unit, or a normal non-migrating Core, can pick it up on the same cell.
- If multiple actors compete in one Tick, the lowest raw UUID wins.
- A living carrier cannot be robbed. A Beacon dropped or dropped on death cannot
  be picked up again until the next Tick.
- Beacon actions resolve before Worker harvesting. Successful pickup gives the
  same-Tick harvest bonus; successful drop removes the bonus for that Tick.
- A Beacon Worker harvests up to 2 resources instead of 1, while still consuming
  one resource point.
- Holding the Beacon raises the Core shield cap from 5 to 10. It does not repair
  the shield, and losing it clamps shield above 5 back to 5.
- The public Beacon score counts a Tick only if the player still holds the Beacon
  at the end of that resolved Tick.

There is no separate public territory or victory score attached to a Beacon.
Beacon contests must be justified by harvest gain, shield value, ranking value,
route safety, and opportunity cost.

### Movement And Cell Capacity

- Units move at most one cardinal cell per Tick and movement consumes their action.
- A cell holds at most two occupying entities.
- Different players contesting the same destination all fail.
- Hostile swaps fail. Longer dependency chains can succeed if every final cell is
  legal.
- Unit moves and finishing Core migrations resolve in one global dependency graph,
  not in submission order.
- Resource cells accept Units but reject migrating Cores.

## Current Production Diagnosis

The deployed Agent was observed at population 40 with the following aggregate
state:

- Population: `40`
- Composition: `18 Workers`, `10 Vanguards`, `12 Rangers`
- Core resources and capacity: `200 / 200`
- Current local strategy limit: `30`
- Current posture/source: deterministic local expansion fallback
- Visible resource evidence: only one known resource point
- Workers: all in `STOCKPILE_HOLD`
- Current plan: repeated `WAIT`

Historical observations show that a model pressure recommendation with a
population limit of 40 caused the final increase from 39 to 40. Later local
strategy reduced its limit to 30, but the Agent has no automatic excess-Unit
reclamation behavior. The current 40 Units therefore remain committed population;
the lower strategy limit only prevents additional discretionary production.

The correct diagnosis is not maintenance failure. It is a mismatch between
existing committed military population and current economic evidence, with no
current marginal production opportunity. The current state should initially enter
`OVEREXTENDED` or `CONSOLIDATE`.

Initial live response:

- Freeze discretionary production.
- Do not expand automatically toward 48.
- Do not use storage saturation alone as an expansion trigger.
- Do not automatically self-destruct Units just because a target limit fell.
- Preserve Core safety, cargo recovery, healing, and emergency defense.
- Treat any later fleet reduction as an explicit, budgeted reconfiguration because
  self-destruction has no refund and population loss can destroy Core inventory.

## Strategy V2 Design

### Separate The Population Concepts

The implementation must not use one `population_limit` field for all of these:

- `economic_target`: population justified by measured resource throughput and
  transport capacity.
- `military_target`: population justified by known threats, defense geometry,
  pressure opportunities, and required escort strength.
- `committed_population`: currently living Units that already impose price,
  capacity, movement, and opportunity effects.
- `production_ceiling`: deterministic Agent policy ceiling for new production.
  This is not a game-rule cap.
- `population_health`: a state such as `RECOVERING`, `CONSOLIDATING`, `HEALTHY`,
  or `OVEREXTENDED`.

A lower target must not imply automatic destruction. Production and reconfiguration
are separate decisions.

### Strategic States

The long-horizon controller should use deterministic states with hysteresis and
minimum dwell times. Suggested states are:

- `RECOVERY`: rebuild after Core loss, severe resource loss, or fleet damage.
- `CONSOLIDATE`: preserve the current fleet while measuring throughput and risk.
- `GROW_ECONOMY`: buy only units whose measured marginal output justifies price,
  reserves, and exposure.
- `MILITARY_READY`: maintain a proven defense or escort composition without
  assuming that a larger force is automatically better.
- `MIGRATION_PREP`: evaluate and prepare a relocation without starting the
  four-Tick commitment.
- `MIGRATION_COMMIT`: execute one validated Core cell move at a time.
- `BEACON_CONTEST`: pursue Beacon value only under explicit deterministic gates.
- `PRESSURE`: respond to an authoritative enemy-Core opportunity with a bounded
  military plan.
- `OVEREXTENDED`: committed population exceeds current economic/military evidence
  or leaves too many Units idle.

These strategic states must remain separate from the existing Tick-critical threat
states such as `ALERT`, `PRE_EVADE`, `ENGAGED`, and `BREAKOUT`.

### Deterministic Production Gate

For each candidate Unit, evaluate a measured horizon rather than a fixed tier:

```text
marginal_value
  > settled_price
  + Core reserve
  + healing reserve
  + transport and migration opportunity cost
  + defense and exposure risk
```

The gate must account for:

- current population and exact SDK price;
- economic target and military target separately;
- worker utilization and successful deposit throughput;
- resource discovery confidence and route congestion;
- storage saturation without treating it as proof of economic demand;
- current health, threat, escort, and healing requirements;
- the next price boundary;
- committed Units that cannot be recovered for free.

The local deterministic controller owns the final production decision.

### Adviser Boundary

The model adviser may suggest only an allow-listed posture or unit-priority
configuration. It must not directly set or override:

- `production_ceiling`;
- economic or military safety reserves;
- Core migration commands or destinations;
- Beacon pickup/drop or contest execution;
- Unit or Core self-destruction;
- emergency defense or action legality.

Advice is optional, stale advice is invalid, and failures must fall back to the
local deterministic controller.

### Migration And Position Value

Migration and Beacon contesting need a shared deterministic position-value model,
but they are separate missions. The evaluator should include:

- expected resource throughput;
- territory and map access;
- Beacon value;
- Core and fleet defense quality;
- movement time, with four logical Ticks per Core cell;
- escort availability and cost;
- exposure to visible and remembered threats;
- cargo loss and deposit interruption;
- the value of abandoning the current Core position;
- destination uncertainty and the lack of destination reservation;
- an abort or cancellation path.

Before `MIGRATION_COMMIT`, require a safe cargo posture, a resource reserve for
four restricted Ticks, a legal adjacent destination, a fallback direction, and
sufficient defense left behind. Re-evaluate after every authoritative migration
event.

### Beacon Priority

Beacon activity should expose explicit priorities:

- `PRIMARY`: current Beacon value clearly dominates economic and survival costs.
- `SECONDARY`: maintain a safe route or opportunistic runner without moving the
  Core solely for the Beacon.
- `DEFERRED`: Beacon value does not justify movement, escort, exposure, or lost
  deposits.

A Beacon contest cannot be justified by its coordinate alone.

## Migration Shadow Phase

The first migration implementation is shadow-only. It evaluates at most one
legal adjacent candidate per Tick when the strategic state is `GROW_ECONOMY` or
`BEACON_CONTEST`, the Core is normal, resources meet the Core reserve, escort
coverage is present, cargo is safe, and no tactical threat, recovery, or
compatibility hold is active. Core movement remains a four-restricted-Tick
commitment per cell.

The evaluator is pure and does not mutate `Turn.plan`, call `START_MOVE`, or
compete with tactical `MOVE_CORE` orders. Its public observation is schema v4 and
contains only aggregate status, blocker reason, candidate count, readiness flags,
restricted-Tick count, authoritative migration rechecks, and a bounded score. It
never exposes destination coordinates, directions, routes, or Unit identifiers.
`SHADOW_ONLY` is a review recommendation, not execution authorization. Beacon
value is classified independently as `PRIMARY`, `SECONDARY`, or `DEFERRED` using
resource reserve, congestion, known-resource evidence, distance, and the four-Tick
migration opportunity cost. Live strategic migration remains disabled until
destination reservation, abort behavior, authoritative event rechecks, and
isolated Beacon/migration tests are complete.

## Implementation Sequence After Context Compression

1. Re-read this document, `docs/strategy.md`, `docs/hybrid-strategy.md`,
   `arena_strategy.py`, `arena_farmer.py`, and their focused tests.
2. Add explicit V2 state and measurement models without changing live behavior.
3. Add deterministic population-health and marginal-production tests around the
   current 40-population case and all dynamic-price boundaries.
4. Separate committed population, economic target, military target, and production
   ceiling in the strategy contract.
5. Add hysteresis, dwell times, cooldowns, and deterministic reasons for every
   strategic transition.
6. Restrict model advice to the allow-listed bounded fields and test stale,
   timed-out, malformed, and contradictory advice.
7. Add migration shadow evaluation first. Do not submit strategic migration until
   route, reserve, escort, abort, and event handling tests pass.
8. Add Beacon priority evaluation independently from migration.
9. Run the full local test suite, compile/type/secret checks, and review the diff.
10. Deploy only through the repository's reviewed systemd update path after the
    behavior is explicitly approved.

## Acceptance Criteria

- The planner never treats a fixed tier such as 30, 40, or 48 as a game rule.
- Population decisions explain economic target, military target, committed
  population, production ceiling, and health state separately.
- Storage saturation alone cannot trigger growth.
- A model timeout or invalid answer cannot increase the deterministic production
  ceiling or issue migration/Beacon/self-destruct actions.
- A current 40-population snapshot enters a stable consolidation or overextension
  state instead of automatically expanding to 48.
- Production uses the official SDK price helper and authoritative spawn events.
- Migration accounts for four restricted Ticks per cell, no destination reservation,
  Units left behind, and failure/cancellation.
- Beacon value accounts for harvest bonus, shield cap, lifetime ranking, and route
  risk.
- Any automatic Unit reduction has explicit inventory-overflow, cargo, Beacon, and
  combat-safety guards.
- Existing tactical threat and Core-safety behavior remains dominant.

## Source Links

- Documentation: https://github.com/arena-hero/arena-hero-doc
- Agent skill: https://github.com/arena-hero/arena-hero-skill
- Official Python SDK: https://github.com/arena-hero/arena-hero-python
- Demonstration Web client: https://github.com/arena-hero/arena-hero-web
- Published documentation: https://doc.arenahero.io/
- Version policy: https://doc.arenahero.io/reference/source-and-version
