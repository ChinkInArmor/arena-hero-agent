# Tactical Command Console

The Dashboard tactical console is the first constrained write surface for a private Arena Hero installation. It is not a shell, service manager, credential editor, model prompt editor, or raw SDK action proxy.

## Boundary

The browser communicates only with the authenticated Dashboard edge. Caddy performs HTTPS Basic Auth and overwrites `X-Arena-Authenticated` before proxying to the loopback Dashboard. Tactical endpoints reject requests without that server-set header. State-changing requests also require a Strict SameSite CSRF cookie and matching `X-Arena-CSRF` header. Public unauthenticated responses do not contain tactical state.

The Dashboard writes fixed-schema JSON command files atomically to `/var/lib/arena-hero-tactical/commands`. The Agent is the only process with Arena Hero SDK authority. It reads, validates, owns, expires, and executes commands one legal movement step at a time. The Agent writes private snapshots, short-retention replay snapshots, and receipts; the browser never writes state or actions directly.

## Supported Commands

- `MOVE_UNITS`: dispatches one or more owned units to a coordinate with a 1-64 Tick TTL.
- `MOVE_CORE`: requests a coordinate for the Core's safe movement logic.
- `CANCEL`: cancels by unit, expedition, or the originating command ID.
- `SET_EXPEDITION` and `DELETE_EXPEDITION`: assigns eligible Vanguards and Rangers to a bounded expedition.
- `SET_PRODUCTION_WEIGHTS`: changes Worker/Vanguard/Ranger marginal-production preference from 0-10. Safety, resources, dynamic prices, force-stage targets, and population limits remain authoritative.

The console cannot request attack parameters, self-destruction, arbitrary SDK JSON, credentials, environment changes, service actions, or arbitrary model prompts. Emergency state clears manual orders when compatibility hold, recovery, Core danger, or survival-margin checks require deterministic control.

## Private Data

Tactical snapshots may contain private map coordinates, owned unit IDs, positions, cargo, visible enemy objects, active orders, and short replay history. They are available only through authenticated tactical endpoints and are not imported into the sanitized observability SQLite database. Caddy access logs remove Authorization headers and the Dashboard does not log request bodies, coordinates, or identifiers.

The tactical history retention is 48 hours. The public operations views retain their existing aggregate-only contract.

## Deployment

The Agent and Dashboard share the `arena-hero-tactical` supplementary group. The Agent owns the private snapshot, history, and receipt paths. The Dashboard can write only the command queue and can read the other tactical paths. Both services remain protected by `ProtectSystem=strict`; the explicit tactical paths are the only additional writable/readable locations.

After installing a release, verify the Agent, Dashboard, and Caddy units, confirm the Dashboard remains loopback-only on `127.0.0.1:8765`, and test both an authenticated tactical request and an unauthenticated `401` response at the public hostname. No deployment should be considered complete until command receipts show accepted, applied, expired, cancelled, or emergency-overridden outcomes.
