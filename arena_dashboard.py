from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_DATABASE_PATH = Path("/var/lib/arena-hero-dashboard/dashboard.sqlite3")
DEFAULT_INBOX_PATH = Path("/var/lib/arena-hero-observability/inbox")
DEFAULT_RELEASE_PATH = Path("/opt/arena-hero-agent/current")
DEFAULT_STATIC_PATH = Path(__file__).resolve().parent / "dashboard" / "dist"
DETAIL_RETENTION_DAYS = 7
HOURLY_RETENTION_DAYS = 90
STALE_AFTER_SECONDS = 45
RANGE_HOURS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720, "90d": 2160}
MAX_HISTORY_POINTS = 720


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


class StrategyState(StrictModel):
    phase: str = Field(max_length=40)
    posture: str = Field(max_length=24)
    source: str = Field(max_length=80)
    reason: str = Field(max_length=80)
    valid_until_tick: int = Field(ge=0)
    worker_target: int = Field(ge=0)
    vanguard_target: int = Field(ge=0)
    ranger_target: int = Field(ge=0)
    population_limit: int = Field(ge=0)
    economy_weight: int = Field(ge=0, le=10)
    territory_weight: int = Field(ge=0, le=10)
    combat_weight: int = Field(ge=0, le=10)
    safety_weight: int = Field(ge=0, le=10)
    beacon_priority: int = Field(ge=0, le=10)
    scout_percent: int = Field(ge=0, le=100)


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
    schema_version: Literal[1]
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
    collect_interval_seconds: float = 5.0,
) -> FastAPI:
    database = database_path or Path(os.environ.get("ARENA_DASHBOARD_DATABASE", DEFAULT_DATABASE_PATH))
    inbox = inbox_path or Path(os.environ.get("ARENA_DASHBOARD_INBOX", DEFAULT_INBOX_PATH))
    release = release_path or Path(os.environ.get("ARENA_DASHBOARD_RELEASE", DEFAULT_RELEASE_PATH))
    static = static_path or Path(os.environ.get("ARENA_DASHBOARD_STATIC_DIR", DEFAULT_STATIC_PATH))
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
