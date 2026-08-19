from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sqlite3
import subprocess
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from arena_tactical import (
    DEFAULT_COMMAND_TTL_TICKS,
    DEFAULT_TACTICAL_ROOT,
    TacticalCommand,
    TacticalReceipt,
    TacticalSnapshot,
    enqueue_command,
)

DEFAULT_DATABASE_PATH = Path("/var/lib/arena-hero-dashboard/dashboard.sqlite3")
DEFAULT_INBOX_PATH = Path("/var/lib/arena-hero-observability/inbox")
DEFAULT_RELEASE_PATH = Path("/opt/arena-hero-agent/current")
DEFAULT_STATIC_PATH = Path(__file__).resolve().parent / "dashboard" / "dist"
DETAIL_RETENTION_DAYS = 7
HOURLY_RETENTION_DAYS = 90
STALE_AFTER_SECONDS = 45
RANGE_HOURS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720, "90d": 2160}
MAX_HISTORY_POINTS = 720
DEFAULT_TACTICAL_PATH = DEFAULT_TACTICAL_ROOT
TACTICAL_AUTH_HEADER = "x-arena-authenticated"
CSRF_COOKIE = "arena_csrf"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentState(StrictModel):
    core_alive: bool
    compatibility_hold: bool
    recovery: bool


class EconomyState(StrictModel):
    resources: int = Field(ge=0)
    capacity: int = Field(ge=0)
    cargo: int = Field(ge=0)
    visible_resources: int = Field(ge=0)
    known_resources: int = Field(ge=0)
    delivery_blocked: int = Field(ge=0)
    resource_blocked: int = Field(ge=0)


class PopulationState(StrictModel):
    total: int = Field(ge=0)
    workers: int = Field(ge=0)
    vanguards: int = Field(ge=0)
    rangers: int = Field(ge=0)


class CoreState(StrictModel):
    alive: bool
    hp: int | None = Field(default=None, ge=0)
    shield: int | None = Field(default=None, ge=0)
    state: str = Field(max_length=32)


class BattlefieldState(StrictModel):
    visible_enemies: int = Field(ge=0)
    danger_cells: int = Field(ge=0)
    combat_pressure: bool
    projected_core_damage: int
    core_survival_margin: int
    scout_chunks: int = Field(ge=0)
    dedicated_scouts: int = Field(ge=0)
    beacon_runner_active: bool
    combat_patrol_units: int = Field(ge=0)


class MigrationShadowState(StrictModel):
    enabled: bool = False
    evaluated: bool = False
    status: Literal["NOT_EVALUATED", "BLOCKED", "READY"] = "NOT_EVALUATED"
    reason: str = Field(default="not_evaluated", max_length=80)
    candidate_count: int = Field(default=0, ge=0, le=4)
    reserve_sufficient: bool = False
    escort_sufficient: bool = False
    cargo_safe: bool = False
    abort_available: bool = False
    restricted_ticks_per_cell: int = Field(default=4, ge=4, le=4)
    score: int = Field(default=0, ge=-10000, le=10000)
    authoritative_rechecks: int = Field(default=0, ge=0)


class StrategyState(StrictModel):
    phase: str = Field(max_length=40)
    posture: str = Field(max_length=24)
    source: str = Field(max_length=80)
    reason: str = Field(max_length=80)
    valid_until_tick: int = Field(ge=0)
    state: str = Field(default="CONSOLIDATE", max_length=40)
    population_health: str = Field(default="CONSOLIDATING", max_length=24)
    beacon_mode: str = Field(default="DEFERRED", max_length=24)
    state_entered_tick: int = Field(default=0, ge=0)
    state_dwell_ticks: int = Field(default=0, ge=0)
    worker_target: int = Field(ge=0)
    vanguard_target: int = Field(ge=0)
    ranger_target: int = Field(ge=0)
    economic_target: int = Field(default=0, ge=0)
    military_target: int = Field(default=0, ge=0)
    committed_population: int = Field(default=0, ge=0)
    production_ceiling: int = Field(default=0, ge=0)
    population_limit: int = Field(ge=0)
    migration_shadow: MigrationShadowState = Field(default_factory=MigrationShadowState)
    economy_weight: int = Field(ge=0, le=10)
    territory_weight: int = Field(ge=0, le=10)
    combat_weight: int = Field(ge=0, le=10)
    safety_weight: int = Field(ge=0, le=10)
    beacon_priority: int = Field(ge=0, le=10)
    scout_percent: int = Field(ge=0, le=100)
    force_stage: Literal["ESTABLISH", "MOBILIZE", "CONTROL", "OVERWHELM"]
    force_stage_index: int = Field(ge=0, le=3)
    force_target_population: int = Field(ge=0, le=48)
    force_target_workers: int = Field(ge=0, le=18)
    force_target_vanguards: int = Field(ge=0, le=14)
    force_target_rangers: int = Field(ge=0, le=16)
    force_worker_deficit: int = Field(ge=0, le=18)
    force_vanguard_deficit: int = Field(ge=0, le=14)
    force_ranger_deficit: int = Field(ge=0, le=16)


class AdviserState(StrictModel):
    enabled: bool
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    outcome: str = Field(max_length=100)
    requests: int = Field(ge=0)
    applied: int = Field(ge=0)
    failures: int = Field(ge=0)
    last_request_tick: int | None = Field(default=None, ge=0)
    last_applied_tick: int | None = Field(default=None, ge=0)
    next_request_tick: int | None = Field(default=None, ge=0)
    advice_valid_until_tick: int | None = Field(default=None, ge=0)
    ttl_remaining_ticks: int | None = Field(default=None, ge=0)
    overridden: bool


class RedactedEvent(StrictModel):
    event_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    generated_at: datetime
    tick: int = Field(gt=0)
    category: Literal["BEACON", "SPAWN", "ECONOMY", "COMBAT", "UNIT", "SYSTEM"]
    event_type: str = Field(max_length=80)
    reason_code: str | None = Field(default=None, max_length=80)
    values: dict[str, int]


class Observation(StrictModel):
    schema_version: Literal[2, 3, 4]
    generated_at: datetime
    tick: int = Field(gt=0)
    agent: AgentState
    economy: EconomyState
    population: PopulationState
    core: CoreState
    battlefield: BattlefieldState
    strategy: StrategyState
    adviser: AdviserState
    actions: dict[str, int]
    event_counts: dict[str, int]
    worker_modes: dict[str, int]
    events: list[RedactedEvent]


class TacticalCommandRequest(StrictModel):
    kind: Literal[
        "MOVE_UNITS",
        "MOVE_CORE",
        "CANCEL",
        "SET_EXPEDITION",
        "DELETE_EXPEDITION",
        "SET_PRODUCTION_WEIGHTS",
    ]
    ttl_ticks: int = Field(default=DEFAULT_COMMAND_TTL_TICKS, ge=1, le=64)
    unit_ids: list[str] = Field(default_factory=list, max_length=64)
    target_x: int | None = None
    target_y: int | None = None
    expedition_id: str | None = Field(default=None, max_length=40)
    cancel_command_id: str | None = Field(default=None, max_length=36)
    name: str | None = Field(default=None, max_length=40)
    vanguard_count: int | None = Field(default=None, ge=0, le=32)
    ranger_count: int | None = Field(default=None, ge=0, le=32)
    worker_weight: int | None = Field(default=None, ge=0, le=10)
    vanguard_weight: int | None = Field(default=None, ge=0, le=10)
    ranger_weight: int | None = Field(default=None, ge=0, le=10)


class OverviewResponse(BaseModel):
    generated_at: datetime
    status: Literal["healthy", "stale", "unavailable"]
    stale_after_seconds: int
    age_seconds: float | None
    deployment: dict[str, Any]
    observation: dict[str, Any] | None


class DashboardStore:
    def __init__(self, database: Path, inbox: Path) -> None:
        self.database = database
        self.inbox = inbox
        self._last_prune = datetime.min.replace(tzinfo=UTC)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    tick INTEGER PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    resources INTEGER NOT NULL,
                    capacity INTEGER NOT NULL,
                    population INTEGER NOT NULL,
                    workers INTEGER NOT NULL,
                    vanguards INTEGER NOT NULL,
                    rangers INTEGER NOT NULL,
                    delivery_blocked INTEGER NOT NULL,
                    resource_blocked INTEGER NOT NULL,
                    visible_enemies INTEGER NOT NULL,
                    posture TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS snapshots_observed_at
                    ON snapshots(observed_at);
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    tick INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    reason_code TEXT,
                    values_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_observed_at
                    ON events(observed_at DESC);
                CREATE TABLE IF NOT EXISTS hourly (
                    hour_start TEXT PRIMARY KEY,
                    samples INTEGER NOT NULL,
                    resources_avg REAL NOT NULL,
                    resources_max INTEGER NOT NULL,
                    capacity_max INTEGER NOT NULL,
                    population_max INTEGER NOT NULL,
                    workers_max INTEGER NOT NULL,
                    vanguards_max INTEGER NOT NULL,
                    rangers_max INTEGER NOT NULL,
                    delivery_blocked_avg REAL NOT NULL,
                    resource_blocked_avg REAL NOT NULL,
                    visible_enemies_max INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS file_offsets (
                    path TEXT PRIMARY KEY,
                    offset INTEGER NOT NULL
                );
                """
            )

    def collect(self) -> None:
        snapshot_path = self.inbox / "snapshot.json"
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            observation = Observation.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError):
            observation = None
        with self.connect() as connection:
            if observation is not None:
                self._insert_snapshot(connection, observation)
            for path in sorted(self.inbox.glob("events-*.jsonl")):
                self._collect_event_file(connection, path)
            now = datetime.now(UTC)
            if now - self._last_prune >= timedelta(hours=1):
                self._aggregate_and_prune(connection, now)
                self._last_prune = now

    def _insert_snapshot(
        self, connection: sqlite3.Connection, observation: Observation
    ) -> None:
        value = observation.model_dump(mode="json")
        connection.execute(
            """
            INSERT OR IGNORE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.tick,
                value["generated_at"],
                observation.economy.resources,
                observation.economy.capacity,
                observation.population.total,
                observation.population.workers,
                observation.population.vanguards,
                observation.population.rangers,
                observation.economy.delivery_blocked,
                observation.economy.resource_blocked,
                observation.battlefield.visible_enemies,
                observation.strategy.posture,
                observation.strategy.source,
                json.dumps(value, sort_keys=True, separators=(",", ":")),
            ),
        )

    def _collect_event_file(self, connection: sqlite3.Connection, path: Path) -> None:
        row = connection.execute(
            "SELECT offset FROM file_offsets WHERE path = ?", (str(path),)
        ).fetchone()
        offset = int(row["offset"]) if row is not None else 0
        try:
            size = path.stat().st_size
            if size < offset:
                offset = 0
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                while line := handle.readline(16_385):
                    if len(line) > 16_384 or not line.endswith("\n"):
                        continue
                    try:
                        event = RedactedEvent.model_validate_json(line)
                    except ValidationError:
                        continue
                    value = event.model_dump(mode="json")
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            value["generated_at"],
                            event.tick,
                            event.category,
                            event.event_type,
                            event.reason_code,
                            json.dumps(event.values, sort_keys=True),
                        ),
                    )
                offset = handle.tell()
        except OSError:
            return
        connection.execute(
            """
            INSERT INTO file_offsets(path, offset) VALUES (?, ?)
            ON CONFLICT(path) DO UPDATE SET offset=excluded.offset
            """,
            (str(path), offset),
        )

    def _aggregate_and_prune(
        self, connection: sqlite3.Connection, now: datetime
    ) -> None:
        detail_cutoff = (now - timedelta(days=DETAIL_RETENTION_DAYS)).isoformat()
        hourly_cutoff = (now - timedelta(days=HOURLY_RETENTION_DAYS)).isoformat()
        connection.execute(
            """
            INSERT OR REPLACE INTO hourly
            SELECT strftime('%Y-%m-%dT%H:00:00Z', observed_at), COUNT(*),
                   AVG(resources), MAX(resources), MAX(capacity), MAX(population),
                   MAX(workers), MAX(vanguards), MAX(rangers),
                   AVG(delivery_blocked), AVG(resource_blocked), MAX(visible_enemies)
              FROM snapshots
             WHERE observed_at < ?
             GROUP BY strftime('%Y-%m-%dT%H:00:00Z', observed_at)
            """,
            (detail_cutoff,),
        )
        connection.execute("DELETE FROM snapshots WHERE observed_at < ?", (detail_cutoff,))
        connection.execute("DELETE FROM events WHERE observed_at < ?", (detail_cutoff,))
        connection.execute("DELETE FROM hourly WHERE hour_start < ?", (hourly_cutoff,))

    def latest(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM snapshots ORDER BY tick DESC LIMIT 1"
            ).fetchone()
        return json.loads(row["payload"]) if row is not None else None

    def history(self, range_name: str) -> tuple[str, list[dict[str, Any]]]:
        since = datetime.now(UTC) - timedelta(hours=RANGE_HOURS[range_name])
        if RANGE_HOURS[range_name] <= DETAIL_RETENTION_DAYS * 24:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT observed_at, tick, resources, capacity, population, workers,
                           vanguards, rangers, delivery_blocked, resource_blocked,
                           visible_enemies, posture, source
                      FROM snapshots WHERE observed_at >= ? ORDER BY tick
                    """,
                    (since.isoformat(),),
                ).fetchall()
            return "detail", _downsample([dict(row) for row in rows])
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM hourly WHERE hour_start >= ?
                UNION ALL
                SELECT strftime('%Y-%m-%dT%H:00:00Z', observed_at) AS hour_start,
                       COUNT(*) AS samples, AVG(resources) AS resources_avg,
                       MAX(resources) AS resources_max, MAX(capacity) AS capacity_max,
                       MAX(population) AS population_max, MAX(workers) AS workers_max,
                       MAX(vanguards) AS vanguards_max, MAX(rangers) AS rangers_max,
                       AVG(delivery_blocked) AS delivery_blocked_avg,
                       AVG(resource_blocked) AS resource_blocked_avg,
                       MAX(visible_enemies) AS visible_enemies_max
                  FROM snapshots WHERE observed_at >= ?
                 GROUP BY strftime('%Y-%m-%dT%H:00:00Z', observed_at)
                 ORDER BY hour_start
                """,
                (since.isoformat(), since.isoformat()),
            ).fetchall()
        return "hourly", _downsample([dict(row) for row in rows])

    def events(self, limit: int, category: str | None) -> list[dict[str, Any]]:
        query = "SELECT * FROM events"
        parameters: list[Any] = []
        if category:
            query += " WHERE category = ?"
            parameters.append(category)
        query += " ORDER BY observed_at DESC LIMIT ?"
        parameters.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["values"] = json.loads(value.pop("values_json"))
            result.append(value)
        return result


def _downsample(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(values) <= MAX_HISTORY_POINTS:
        return values
    last_index = len(values) - 1
    return [
        values[index * last_index // (MAX_HISTORY_POINTS - 1)]
        for index in range(MAX_HISTORY_POINTS)
    ]


def _read_release_value(release_path: Path, name: str) -> str | None:
    try:
        value = (release_path / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value[:160] or None


def deployment_metadata(release_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "release": _read_release_value(release_path, "release-id"),
        "version": _read_release_value(release_path, "source-version"),
        "commit": _read_release_value(release_path, "source-commit"),
        "service_active": None,
        "restarts": None,
        "active_since": None,
    }
    try:
        completed = subprocess.run(
            [
                "systemctl",
                "show",
                "arena-hero-agent.service",
                "--property=ActiveState",
                "--property=NRestarts",
                "--property=ActiveEnterTimestamp",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        values = {
            key: value
            for line in completed.stdout.splitlines()
            if "=" in line
            for key, value in [line.split("=", 1)]
        }
        if completed.returncode == 0:
            result["service_active"] = values.get("ActiveState") == "active"
            restarts = values.get("NRestarts", "")
            result["restarts"] = int(restarts) if restarts.isdigit() else None
            result["active_since"] = values.get("ActiveEnterTimestamp") or None
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def create_app(
    *,
    database_path: Path | None = None,
    inbox_path: Path | None = None,
    release_path: Path | None = None,
    static_path: Path | None = None,
    tactical_path: Path | None = None,
    collect_interval_seconds: float = 5.0,
) -> FastAPI:
    database = database_path or Path(os.environ.get("ARENA_DASHBOARD_DATABASE", DEFAULT_DATABASE_PATH))
    inbox = inbox_path or Path(os.environ.get("ARENA_DASHBOARD_INBOX", DEFAULT_INBOX_PATH))
    release = release_path or Path(os.environ.get("ARENA_DASHBOARD_RELEASE", DEFAULT_RELEASE_PATH))
    static = static_path or Path(os.environ.get("ARENA_DASHBOARD_STATIC_DIR", DEFAULT_STATIC_PATH))
    tactical = tactical_path or Path(
        os.environ.get("ARENA_DASHBOARD_TACTICAL_ROOT", DEFAULT_TACTICAL_PATH)
    )
    store = DashboardStore(database, inbox)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.initialize()
        stop = asyncio.Event()

        async def collect_loop() -> None:
            while not stop.is_set():
                await asyncio.to_thread(store.collect)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=collect_interval_seconds)
                except TimeoutError:
                    pass

        task = asyncio.create_task(collect_loop())
        yield
        stop.set()
        await task

    app = FastAPI(
        title="Arena Hero Operations Dashboard",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.csrf_tokens = set()

    def require_tactical_auth(request: Request) -> None:
        if request.headers.get(TACTICAL_AUTH_HEADER) != "1":
            raise HTTPException(status_code=403, detail="tactical_auth_required")

    def require_csrf(request: Request, token: str | None) -> None:
        cookie = request.cookies.get(CSRF_COOKIE)
        if (
            token is None
            or cookie is None
            or token != cookie
            or token not in app.state.csrf_tokens
        ):
            raise HTTPException(status_code=403, detail="csrf_validation_failed")

    def current_tactical_tick() -> int:
        try:
            value = TacticalSnapshot.model_validate_json(
                (tactical / "snapshot.json").read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise HTTPException(status_code=503, detail="tactical_state_unavailable") from exc
        age = (datetime.now(UTC) - value.generated_at).total_seconds()
        if age > STALE_AFTER_SECONDS:
            raise HTTPException(status_code=409, detail="tactical_state_stale")
        return value.tick

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health")
    async def api_health() -> JSONResponse:
        latest = await asyncio.to_thread(store.latest)
        return JSONResponse({"ok": latest is not None})

    @app.get("/api/v1/overview", response_model=OverviewResponse)
    async def overview() -> OverviewResponse:
        latest = await asyncio.to_thread(store.latest)
        age: float | None = None
        status: Literal["healthy", "stale", "unavailable"] = "unavailable"
        if latest is not None:
            observed_at = datetime.fromisoformat(latest["generated_at"].replace("Z", "+00:00"))
            age = max(0.0, (datetime.now(UTC) - observed_at).total_seconds())
            status = "healthy" if age <= STALE_AFTER_SECONDS else "stale"
        deployment = await asyncio.to_thread(deployment_metadata, release)
        return OverviewResponse(
            generated_at=datetime.now(UTC),
            status=status,
            stale_after_seconds=STALE_AFTER_SECONDS,
            age_seconds=round(age, 1) if age is not None else None,
            deployment=deployment,
            observation=latest,
        )

    @app.get("/api/v1/history")
    async def history(
        range_name: str = Query(default="24h", alias="range")
    ) -> dict[str, Any]:
        if range_name not in RANGE_HOURS:
            raise HTTPException(status_code=400, detail="unsupported_range")
        resolution, points = await asyncio.to_thread(store.history, range_name)
        return {"range": range_name, "resolution": resolution, "points": points}

    @app.get("/api/v1/events")
    async def events(
        limit: int = Query(default=80, ge=1, le=200),
        category: str | None = Query(default=None, max_length=20),
    ) -> dict[str, Any]:
        if category is not None and category not in {
            "BEACON", "SPAWN", "ECONOMY", "COMBAT", "UNIT", "SYSTEM"
        }:
            raise HTTPException(status_code=400, detail="unsupported_category")
        items = await asyncio.to_thread(store.events, limit, category)
        return {"items": items}

    @app.get("/api/v1/tactical/csrf")
    async def tactical_csrf(request: Request, response: Response) -> dict[str, str]:
        require_tactical_auth(request)
        token = secrets.token_urlsafe(32)
        app.state.csrf_tokens.add(token)
        if len(app.state.csrf_tokens) > 128:
            app.state.csrf_tokens = {token}
        response.set_cookie(
            CSRF_COOKIE,
            token,
            secure=True,
            httponly=False,
            samesite="strict",
            max_age=3600,
            path="/api/v1/tactical",
        )
        return {"csrf_token": token}

    @app.get("/api/v1/tactical/state")
    async def tactical_state(request: Request) -> dict[str, Any]:
        require_tactical_auth(request)
        try:
            snapshot = TacticalSnapshot.model_validate_json(
                (tactical / "snapshot.json").read_text(encoding="utf-8")
            )
        except OSError as exc:
            raise HTTPException(status_code=503, detail="tactical_state_unavailable") from exc
        except ValidationError as exc:
            raise HTTPException(status_code=503, detail="tactical_state_invalid") from exc
        return snapshot.model_dump(mode="json")

    @app.get("/api/v1/tactical/history")
    async def tactical_history(
        request: Request,
        before_tick: int | None = Query(default=None, ge=0),
        limit: int = Query(default=120, ge=1, le=500),
    ) -> dict[str, Any]:
        require_tactical_auth(request)
        paths = sorted((tactical / "history").glob("tick-*.json"), reverse=True)
        items = []
        for path in paths:
            try:
                snapshot = TacticalSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError):
                continue
            if before_tick is not None and snapshot.tick >= before_tick:
                continue
            items.append(snapshot.model_dump(mode="json"))
            if len(items) >= limit:
                break
        return {"items": items, "retention_hours": 48}

    @app.get("/api/v1/tactical/receipts")
    async def tactical_receipts(
        request: Request,
        limit: int = Query(default=80, ge=1, le=200),
    ) -> dict[str, Any]:
        require_tactical_auth(request)
        items = []
        for path in sorted((tactical / "receipts").glob("receipt-*.json"), reverse=True):
            try:
                receipt = TacticalReceipt.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError):
                continue
            items.append(receipt.model_dump(mode="json"))
            if len(items) >= limit:
                break
        return {"items": items}

    @app.post("/api/v1/tactical/commands", status_code=202)
    async def tactical_command(
        payload: TacticalCommandRequest,
        request: Request,
        x_arena_csrf: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_tactical_auth(request)
        require_csrf(request, x_arena_csrf)
        command_id = str(__import__("uuid").uuid4())
        try:
            command = TacticalCommand(
                command_id=command_id,
                kind=payload.kind,
                issued_at=datetime.now(UTC),
                issued_tick=current_tactical_tick(),
                ttl_ticks=payload.ttl_ticks,
                unit_ids=payload.unit_ids,
                target_x=payload.target_x,
                target_y=payload.target_y,
                expedition_id=payload.expedition_id,
                cancel_command_id=payload.cancel_command_id,
                name=payload.name,
                vanguard_count=payload.vanguard_count,
                ranger_count=payload.ranger_count,
                worker_weight=payload.worker_weight,
                vanguard_weight=payload.vanguard_weight,
                ranger_weight=payload.ranger_weight,
            )
            enqueue_command(tactical / "commands", command)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid_tactical_command") from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail="duplicate_tactical_command") from exc
        return {
            "command_id": command.command_id,
            "status": "QUEUED",
            "issued_tick": command.issued_tick,
            "expires_tick": command.expires_tick,
        }

    @app.delete("/api/v1/tactical/commands/{command_id}", status_code=202)
    async def cancel_tactical_command(
        command_id: str,
        request: Request,
        x_arena_csrf: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_tactical_auth(request)
        require_csrf(request, x_arena_csrf)
        payload = TacticalCommandRequest(
            kind="CANCEL",
            cancel_command_id=command_id,
            ttl_ticks=DEFAULT_COMMAND_TTL_TICKS,
        )
        return await tactical_command(payload, request, x_arena_csrf)

    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="dashboard")

    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the read-only Arena Hero Dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("Dashboard must listen on loopback")
    import uvicorn

    uvicorn.run("arena_dashboard:app", host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
