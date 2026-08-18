from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

TACTICAL_SCHEMA_VERSION = 1
DEFAULT_TACTICAL_ROOT = Path("/var/lib/arena-hero-tactical")
DEFAULT_COMMAND_TTL_TICKS = 32
MIN_COMMAND_TTL_TICKS = 1
MAX_COMMAND_TTL_TICKS = 64
TACTICAL_HISTORY_HOURS = 48

ControlMode = Literal["AUTO", "MANUAL", "EXPEDITION", "EMERGENCY"]
CommandKind = Literal[
    "MOVE_UNITS",
    "MOVE_CORE",
    "CANCEL",
    "SET_EXPEDITION",
    "DELETE_EXPEDITION",
    "SET_PRODUCTION_WEIGHTS",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TacticalCommand(StrictModel):
    schema_version: Literal[1] = 1
    command_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    kind: CommandKind
    issued_at: datetime
    issued_tick: int = Field(ge=0)
    ttl_ticks: int = Field(default=DEFAULT_COMMAND_TTL_TICKS, ge=1, le=64)
    unit_ids: list[UUID] = Field(default_factory=list, max_length=64)
    target_x: int | None = None
    target_y: int | None = None
    expedition_id: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,40}$")
    cancel_command_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    name: str | None = Field(default=None, min_length=1, max_length=40)
    vanguard_count: int | None = Field(default=None, ge=0, le=32)
    ranger_count: int | None = Field(default=None, ge=0, le=32)
    worker_weight: int | None = Field(default=None, ge=0, le=10)
    vanguard_weight: int | None = Field(default=None, ge=0, le=10)
    ranger_weight: int | None = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def validate_shape(self) -> TacticalCommand:
        coordinates = self.target_x is not None and self.target_y is not None
        if (self.target_x is None) != (self.target_y is None):
            raise ValueError("both target coordinates are required")
        if self.kind == "MOVE_UNITS" and (not self.unit_ids or not coordinates):
            raise ValueError("MOVE_UNITS requires unit IDs and a target")
        if self.kind == "MOVE_CORE" and (self.unit_ids or not coordinates):
            raise ValueError("MOVE_CORE requires only a target")
        if self.kind == "CANCEL" and not (
            self.unit_ids or self.expedition_id or self.cancel_command_id
        ):
            raise ValueError("CANCEL requires unit IDs, expedition ID, or command ID")
        if self.kind == "SET_EXPEDITION" and not (
            self.expedition_id
            and self.name
            and coordinates
            and self.vanguard_count is not None
            and self.ranger_count is not None
            and self.vanguard_count + self.ranger_count > 0
        ):
            raise ValueError("SET_EXPEDITION requires a complete non-empty expedition")
        if self.kind == "DELETE_EXPEDITION" and not self.expedition_id:
            raise ValueError("DELETE_EXPEDITION requires expedition ID")
        if self.kind == "SET_PRODUCTION_WEIGHTS":
            values = (self.worker_weight, self.vanguard_weight, self.ranger_weight)
            if any(value is None for value in values) or not any(values):
                raise ValueError("production weights must be present and not all zero")
        return self

    @property
    def expires_tick(self) -> int:
        return self.issued_tick + self.ttl_ticks


class TacticalReceipt(StrictModel):
    schema_version: Literal[1] = 1
    command_id: str
    tick: int = Field(ge=0)
    status: Literal["ACCEPTED", "APPLIED", "REJECTED", "EXPIRED", "CANCELLED", "OVERRIDDEN"]
    reason: str = Field(max_length=80)
    affected_count: int = Field(default=0, ge=0, le=64)
    generated_at: datetime


class TacticalUnit(StrictModel):
    id: UUID
    unit_type: Literal["WORKER", "VANGUARD", "RANGER"]
    x: int
    y: int
    hp: int = Field(ge=0)
    cargo: int = Field(default=0, ge=0)
    mode: ControlMode
    target_x: int | None = None
    target_y: int | None = None
    behavior: str | None = Field(default=None, max_length=32)


class TacticalMemoryObject(StrictModel):
    """A remembered map entry: position plus the tick it was last seen.

    Static terrain (obstacles) is stored permanently; dynamic entries
    (enemies, resources) decay and are pruned by the controller. The
    front end derives confidence from ``tick - last_seen_tick``.
    """

    key: str = Field(max_length=64)
    x: int
    y: int
    last_seen_tick: int = Field(ge=0)
    unit_type: str | None = Field(default=None, max_length=16)


class TacticalMemory(StrictModel):
    obstacles: list[list[int]] = Field(default_factory=list)  # [x, y] pairs
    resources: list[TacticalMemoryObject] = Field(default_factory=list)
    enemies: list[TacticalMemoryObject] = Field(default_factory=list)


class TacticalObject(StrictModel):
    kind: Literal["CORE", "ENEMY_CORE", "ENEMY_UNIT", "RESOURCE", "OBSTACLE", "BEACON"]
    id: UUID | None = None
    x: int
    y: int
    unit_type: str | None = Field(default=None, max_length=16)
    hp: int | None = Field(default=None, ge=0)
    shield: int | None = Field(default=None, ge=0)
    last_seen_tick: int = Field(ge=0)


class TacticalSnapshot(StrictModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    tick: int = Field(ge=0)
    control_mode: ControlMode
    emergency_reason: str | None = Field(default=None, max_length=80)
    production_weights: dict[str, int]
    units: list[TacticalUnit]
    objects: list[TacticalObject]
    active_commands: list[dict[str, Any]]
    expeditions: list[dict[str, Any]]
    memory: TacticalMemory | None = None


@dataclass(slots=True)
class ActiveOrder:
    command_id: str
    unit_id: UUID | None
    target: tuple[int, int]
    expires_tick: int
    mode: ControlMode
    expedition_id: str | None = None


def _timestamp() -> datetime:
    return datetime.now(UTC)


def _atomic_json(path: Path, value: BaseModel | Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o640)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def command_filename(command_id: str) -> str:
    return f"command-{command_id}.json"


def enqueue_command(inbox: Path, command: TacticalCommand) -> Path:
    destination = inbox / command_filename(command.command_id)
    if destination.exists():
        raise FileExistsError("command already exists")
    _atomic_json(destination, command)
    return destination


class TacticalController:
    # Memory (persistent known map) retention in ticks.
    MEMORY_RESOURCE_TTL = 256
    MEMORY_ENEMY_TTL = 96
    MEMORY_OBSTACLE_CAP = 20000

    def __init__(self, root: Path) -> None:
        self.root = root
        self.inbox = root / "commands"
        self.receipts = root / "receipts"
        self.history = root / "history"
        self.snapshot_path = root / "snapshot.json"
        self.active_orders: dict[UUID | None, ActiveOrder] = {}
        self.expeditions: dict[str, dict[str, Any]] = {}
        self.production_weights = {"WORKER": 4, "VANGUARD": 1, "RANGER": 1}
        self.production_weights_override: dict[str, int] | None = None
        self._processed: set[str] = set()
        self._last_history_prune = datetime.min.replace(tzinfo=UTC)
        self._memory: dict[tuple[str, str], TacticalMemoryObject] = {}
        self._memory_tick = 0
        self.root.mkdir(parents=True, exist_ok=True)
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.receipts.mkdir(parents=True, exist_ok=True)
        self.history.mkdir(parents=True, exist_ok=True)
        self._restore_state()

    def _restore_state(self) -> None:
        try:
            snapshot = TacticalSnapshot.model_validate_json(
                self.snapshot_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            return
        weights = snapshot.production_weights
        if set(weights) == {"WORKER", "VANGUARD", "RANGER"} and any(weights.values()):
            self.production_weights = dict(weights)
            self.production_weights_override = dict(weights)
        self.expeditions = {
            str(item["id"]): dict(item)
            for item in snapshot.expeditions
            if isinstance(item, Mapping) and item.get("id")
        }
        for item in snapshot.active_commands:
            try:
                unit_id = UUID(item["unit_id"]) if item.get("unit_id") else None
                order = ActiveOrder(
                    command_id=str(item["command_id"]),
                    unit_id=unit_id,
                    target=(int(item["target_x"]), int(item["target_y"])),
                    expires_tick=int(item["expires_tick"]),
                    mode=item["mode"],
                    expedition_id=item.get("expedition_id"),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self.active_orders[unit_id] = order

    def _receipt(
        self,
        command: TacticalCommand,
        tick: int,
        status: str,
        reason: str,
        affected_count: int = 0,
    ) -> None:
        receipt = TacticalReceipt(
            command_id=command.command_id,
            tick=tick,
            status=status,
            reason=reason,
            affected_count=affected_count,
            generated_at=_timestamp(),
        )
        _atomic_json(self.receipts / f"receipt-{command.command_id}-{tick}.json", receipt)

    def collect_commands(self, tick: int, owned_unit_ids: set[UUID]) -> None:
        for path in sorted(self.inbox.glob("command-*.json")):
            try:
                command = TacticalCommand.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError):
                path.unlink(missing_ok=True)
                continue
            if command.command_id in self._processed or any(
                self.receipts.glob(f"receipt-{command.command_id}-*.json")
            ):
                path.unlink(missing_ok=True)
                continue
            self._processed.add(command.command_id)
            if tick > command.expires_tick:
                self._receipt(command, tick, "EXPIRED", "ttl_expired")
                path.unlink(missing_ok=True)
                continue
            unknown = set(command.unit_ids) - owned_unit_ids
            if unknown:
                self._receipt(command, tick, "REJECTED", "unknown_unit")
                path.unlink(missing_ok=True)
                continue
            if command.kind in {"MOVE_UNITS", "MOVE_CORE"}:
                keys: list[UUID | None] = list(command.unit_ids) or [None]
                if any(key in self.active_orders for key in keys):
                    self._receipt(command, tick, "REJECTED", "control_conflict")
                else:
                    target = (int(command.target_x), int(command.target_y))
                    for key in keys:
                        self.active_orders[key] = ActiveOrder(
                            command.command_id,
                            key,
                            target,
                            command.expires_tick,
                            "MANUAL",
                        )
                    self._receipt(command, tick, "ACCEPTED", "queued", len(keys))
            elif command.kind == "CANCEL":
                affected = 0
                for unit_id in command.unit_ids:
                    affected += int(self.active_orders.pop(unit_id, None) is not None)
                for key, order in tuple(self.active_orders.items()):
                    if (
                        command.expedition_id
                        and order.expedition_id == command.expedition_id
                    ) or (
                        command.cancel_command_id
                        and order.command_id == command.cancel_command_id
                    ):
                        self.active_orders.pop(key)
                        affected += 1
                self._receipt(command, tick, "CANCELLED", "operator_cancel", affected)
            elif command.kind == "SET_PRODUCTION_WEIGHTS":
                self.production_weights = {
                    "WORKER": int(command.worker_weight),
                    "VANGUARD": int(command.vanguard_weight),
                    "RANGER": int(command.ranger_weight),
                }
                self.production_weights_override = dict(self.production_weights)
                self._receipt(command, tick, "APPLIED", "production_weights_updated")
            elif command.kind == "SET_EXPEDITION":
                self.expeditions[str(command.expedition_id)] = {
                    "id": command.expedition_id,
                    "name": command.name,
                    "target_x": command.target_x,
                    "target_y": command.target_y,
                    "vanguard_count": command.vanguard_count,
                    "ranger_count": command.ranger_count,
                    "expires_tick": command.expires_tick,
                }
                self._receipt(command, tick, "APPLIED", "expedition_saved")
            elif command.kind == "DELETE_EXPEDITION":
                removed = self.expeditions.pop(str(command.expedition_id), None)
                self._receipt(
                    command,
                    tick,
                    "APPLIED" if removed else "REJECTED",
                    "expedition_deleted" if removed else "expedition_not_found",
                )
            path.unlink(missing_ok=True)

    def materialize_expeditions(self, turn: object, tick: int) -> None:
        living = {unit.id: unit for unit in turn.units}
        for key in tuple(self.active_orders):
            order = self.active_orders[key]
            if key is not None and key not in living:
                self.active_orders.pop(key)
            elif order.mode == "EXPEDITION" and order.expedition_id not in self.expeditions:
                self.active_orders.pop(key)
        claimed = {key for key in self.active_orders if key is not None}
        for expedition_id, expedition in sorted(self.expeditions.items()):
            if tick > int(expedition["expires_tick"]):
                self.expeditions.pop(expedition_id)
                continue
            target = int(expedition["target_x"]), int(expedition["target_y"])
            wanted = {
                "VANGUARD": int(expedition["vanguard_count"]),
                "RANGER": int(expedition["ranger_count"]),
            }
            existing = {
                kind: sum(
                    order.mode == "EXPEDITION"
                    and order.expedition_id == expedition_id
                    and living.get(unit_id) is not None
                    and living[unit_id].unit_type.value == kind
                    for unit_id, order in self.active_orders.items()
                    if unit_id is not None
                )
                for kind in wanted
            }
            for kind, count in wanted.items():
                candidates = sorted(
                    (
                        unit
                        for unit in turn.units
                        if unit.unit_type.value == kind and unit.id not in claimed
                    ),
                    key=lambda unit: (abs(unit.position[0] - target[0]) + abs(unit.position[1] - target[1]), unit.id.bytes),
                )
                for unit in candidates[: max(0, count - existing[kind])]:
                    self.active_orders[unit.id] = ActiveOrder(
                        f"expedition:{expedition_id}",
                        unit.id,
                        target,
                        int(expedition["expires_tick"]),
                        "EXPEDITION",
                        expedition_id,
                    )
                    claimed.add(unit.id)

    def complete(self, unit_id: UUID | None, tick: int, reason: str = "target_reached") -> None:
        order = self.active_orders.pop(unit_id, None)
        if order is None or order.command_id.startswith("expedition:"):
            return
        path = self.receipts / f"receipt-{order.command_id}-{tick}.json"
        affected_count = 1
        try:
            previous = TacticalReceipt.model_validate_json(path.read_text(encoding="utf-8"))
            affected_count += previous.affected_count
        except (OSError, ValidationError):
            pass
        receipt = TacticalReceipt(
            command_id=order.command_id,
            tick=tick,
            status="APPLIED",
            reason=reason,
            affected_count=affected_count,
            generated_at=_timestamp(),
        )
        _atomic_json(path, receipt)

    def expire(self, tick: int) -> None:
        expired: dict[str, int] = {}
        for key, order in tuple(self.active_orders.items()):
            if tick > order.expires_tick:
                self.active_orders.pop(key)
                if not order.command_id.startswith("expedition:"):
                    expired[order.command_id] = expired.get(order.command_id, 0) + 1
        for command_id, count in expired.items():
            receipt = TacticalReceipt(
                command_id=command_id,
                tick=tick,
                status="EXPIRED",
                reason="ttl_expired",
                affected_count=count,
                generated_at=_timestamp(),
            )
            _atomic_json(self.receipts / f"receipt-{command_id}-{tick}.json", receipt)

    def emergency_override(self, tick: int, reason: str) -> None:
        if not self.active_orders:
            return
        command_ids = {order.command_id for order in self.active_orders.values()}
        for command_id in command_ids:
            receipt = TacticalReceipt(
                command_id=command_id,
                tick=tick,
                status="OVERRIDDEN",
                reason=reason,
                affected_count=sum(
                    order.command_id == command_id for order in self.active_orders.values()
                ),
                generated_at=_timestamp(),
            )
            _atomic_json(self.receipts / f"receipt-{command_id}-{tick}.json", receipt)
        self.active_orders.clear()

    @property
    def active_mode(self) -> ControlMode:
        if any(order.mode == "MANUAL" for order in self.active_orders.values()):
            return "MANUAL"
        if self.active_orders or self.expeditions:
            return "EXPEDITION"
        return "AUTO"

    def _remember(self, turn: object) -> None:
        """Merge visible terrain into the persistent known map.

        Obstacles are static and kept forever (bounded by a cap);
        resources and enemies carry last_seen_tick so the front end can
        render confidence (= f(tick - last_seen_tick)) and the controller
        can prune stale entries.
        """
        tick = turn.tick
        self._memory_tick = tick
        for x, y in turn.obstacle_cells:
            self._memory[("obstacle", f"{x},{y}")] = TacticalMemoryObject(
                key=f"{x},{y}", x=x, y=y, last_seen_tick=tick
            )
        for x, y in turn.resource_cells:
            key = f"{x},{y}"
            self._memory[("resource", key)] = TacticalMemoryObject(
                key=key, x=x, y=y, last_seen_tick=tick
            )
        for enemy in turn.visible_enemies:
            key = str(getattr(enemy, "id", "")) or f"{enemy.position[0]},{enemy.position[1]}"
            self._memory[("enemy", key)] = TacticalMemoryObject(
                key=key,
                x=enemy.position[0],
                y=enemy.position[1],
                last_seen_tick=tick,
                unit_type=getattr(getattr(enemy, "unit_type", None), "value", None),
            )
        # Prune dynamic entries past their TTL; bound static obstacle count.
        for (kind, key), entry in tuple(self._memory.items()):
            age = tick - entry.last_seen_tick
            if kind == "obstacle":
                if len(self._memory) <= self.MEMORY_OBSTACLE_CAP + 512:
                    continue
                self._memory.pop((kind, key), None)  # evict oldest-seen first
            elif kind == "resource" and age > self.MEMORY_RESOURCE_TTL:
                self._memory.pop((kind, key), None)
            elif kind == "enemy" and age > self.MEMORY_ENEMY_TTL:
                self._memory.pop((kind, key), None)

    def _memory_snapshot(self) -> TacticalMemory:
        obstacles = []
        resources = []
        enemies = []
        for (kind, _), entry in self._memory.items():
            target = {"obstacle": obstacles, "resource": resources, "enemy": enemies}[kind]
            target.append(entry)
        return TacticalMemory(
            obstacles=sorted(([e.x, e.y] for e in obstacles), key=lambda pair: (pair[1], pair[0])),
            resources=sorted(resources, key=lambda e: (e.y, e.x)),
            enemies=sorted(enemies, key=lambda e: (e.y, e.x)),
        )

    def write_snapshot(
        self,
        turn: object,
        *,
        emergency_reason: str | None,
        unit_modes: Mapping[UUID, str],
    ) -> None:
        mode: ControlMode = "EMERGENCY" if emergency_reason else (
            "MANUAL" if self.active_orders else "EXPEDITION" if self.expeditions else "AUTO"
        )
        units = []
        for unit in turn.units:
            order = self.active_orders.get(unit.id)
            units.append(
                TacticalUnit(
                    id=unit.id,
                    unit_type=unit.unit_type.value,
                    x=unit.position[0],
                    y=unit.position[1],
                    hp=unit.hp,
                    cargo=getattr(unit, "cargo", 0),
                    mode=order.mode if order else "AUTO",
                    target_x=order.target[0] if order else None,
                    target_y=order.target[1] if order else None,
                    behavior=unit_modes.get(unit.id),
                )
            )
        objects: list[TacticalObject] = []
        if turn.core is not None:
            objects.append(TacticalObject(kind="CORE", id=turn.core.id, x=turn.core.position[0], y=turn.core.position[1], hp=turn.core.hp, shield=turn.core.shield, last_seen_tick=turn.tick))
        for enemy in turn.visible_enemies:
            objects.append(TacticalObject(kind="ENEMY_CORE" if getattr(enemy, "kind") == "CORE" else "ENEMY_UNIT", id=enemy.id, x=enemy.position[0], y=enemy.position[1], unit_type=getattr(getattr(enemy, "unit_type", None), "value", None), hp=getattr(enemy, "hp", None), last_seen_tick=turn.tick))
        objects.extend(TacticalObject(kind="RESOURCE", x=x, y=y, last_seen_tick=turn.tick) for x, y in turn.resource_cells)
        objects.extend(TacticalObject(kind="OBSTACLE", x=x, y=y, last_seen_tick=turn.tick) for x, y in turn.obstacle_cells)
        objects.append(TacticalObject(kind="BEACON", id=getattr(turn.beacon, "carrier_id", None), x=turn.beacon.position[0], y=turn.beacon.position[1], last_seen_tick=turn.tick))
        self._remember(turn)
        snapshot = TacticalSnapshot(
            generated_at=_timestamp(),
            tick=turn.tick,
            control_mode=mode,
            emergency_reason=emergency_reason,
            production_weights=self.production_weights,
            units=units,
            objects=objects,
            active_commands=[
                {
                    "command_id": order.command_id,
                    "unit_id": str(order.unit_id) if order.unit_id else None,
                    "target_x": order.target[0],
                    "target_y": order.target[1],
                    "expires_tick": order.expires_tick,
                    "mode": order.mode,
                    "expedition_id": order.expedition_id,
                }
                for order in self.active_orders.values()
            ],
            expeditions=list(self.expeditions.values()),
            memory=self._memory_snapshot(),
        )
        _atomic_json(self.snapshot_path, snapshot)
        history_payload = snapshot.model_dump(mode="json")
        history_payload.pop("memory", None)
        _atomic_json(self.history / f"tick-{turn.tick:020d}.json", history_payload)
        self._prune_history()

    def _prune_history(self) -> None:
        now = datetime.now(UTC)
        if now - self._last_history_prune < timedelta(hours=1):
            return
        self._last_history_prune = now
        cutoff = now - timedelta(hours=TACTICAL_HISTORY_HOURS)
        for path in self.history.glob("tick-*.json"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                    path.unlink()
            except OSError:
                continue

    @staticmethod
    def audit_digest(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:24]
