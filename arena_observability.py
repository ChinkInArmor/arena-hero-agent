from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

OBSERVATION_SCHEMA_VERSION = 1
DEFAULT_OBSERVATION_DIR = Path("/var/lib/arena-hero-observability/inbox")
EVENT_RETENTION_DAYS = 8
SAFE_EVENT_VALUES = {
    "amount",
    "damage",
    "shield_damage",
    "hp_damage",
    "due",
    "paid",
    "deficit",
    "destroyed",
}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_count_map(values: Counter[str]) -> dict[str, int]:
    return {name: values[name] for name in sorted(values) if values[name] > 0}


def _event_category(event_type: str) -> str:
    if "BEACON" in event_type:
        return "BEACON"
    if "SPAWN" in event_type:
        return "SPAWN"
    if event_type in {"DEPOSIT_SUCCEEDED", "HARVEST_SUCCEEDED"}:
        return "ECONOMY"
    if any(token in event_type for token in ("DAMAGED", "SHOT", "ATTACK", "DESTROY")):
        return "COMBAT"
    if event_type.startswith(("CORE_", "UNIT_")):
        return "UNIT"
    return "SYSTEM"


def _safe_event(event: object, *, tick: int, index: int) -> dict[str, Any] | None:
    event_type = getattr(event, "event_type", None)
    if not isinstance(event_type, str) or not event_type:
        return None
    reason = getattr(event, "reason_code", None)
    reason_code = reason if isinstance(reason, str) and reason else None
    raw_values = getattr(event, "values", None)
    values: dict[str, int] = {}
    if isinstance(raw_values, Mapping):
        for name in SAFE_EVENT_VALUES:
            value = raw_values.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                values[name] = value
    identity = f"{tick}:{index}:{event_type}:{reason_code or ''}"
    return {
        "event_id": hashlib.sha256(identity.encode("ascii")).hexdigest()[:24],
        "generated_at": _timestamp(),
        "tick": tick,
        "category": _event_category(event_type),
        "event_type": event_type,
        "reason_code": reason_code,
        "values": values,
    }


def build_observation(turn: object, tactic: object, accepted_tick: int) -> dict[str, Any]:
    plan = turn.plan.model_dump(mode="json", exclude_none=True)
    action_counts = Counter(
        action["type"] for action in plan.get("unit_actions", {}).values()
    )
    core_action = plan.get("core_action")
    if core_action:
        action_counts[core_action["type"]] += 1
    event_counts = Counter(
        (
            f"{event.event_type}/{event.reason_code}"
            if getattr(event, "reason_code", None)
            else event.event_type
        )
        for event in turn.events
    )
    mode_counts = Counter(tactic.worker_modes.values())
    delivery_blocked = sum(
        mode in {"RETURN_BLOCKED", "CLEAR_CORE_BLOCKED"}
        for mode in tactic.worker_modes.values()
    )
    resource_blocked = mode_counts["RESOURCE_BLOCKED"]
    core = turn.core
    parameters = tactic.strategic_parameters
    context = getattr(tactic, "current_strategic_context", None)
    adviser = tactic.strategic_controller.adviser
    adviser_status = (
        adviser.telemetry(accepted_tick, parameters.source)
        if adviser is not None
        else {
            "enabled": False,
            "provider": None,
            "model": None,
            "outcome": "disabled",
            "requests": 0,
            "applied": 0,
            "failures": 0,
            "last_request_tick": None,
            "last_applied_tick": None,
            "next_request_tick": None,
            "advice_valid_until_tick": None,
            "ttl_remaining_ticks": None,
            "overridden": False,
        }
    )
    safe_events = [
        safe
        for index, event in enumerate(turn.events)
        if (safe := _safe_event(event, tick=accepted_tick, index=index)) is not None
    ]
    generated_at = _timestamp()
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "generated_at": generated_at,
        "tick": accepted_tick,
        "agent": {
            "core_alive": core is not None,
            "compatibility_hold": bool(tactic.compatibility_hold),
            "recovery": bool(tactic.recovery_mode),
        },
        "economy": {
            "resources": turn.resources,
            "capacity": turn.resource_capacity,
            "cargo": sum(worker.cargo for worker in turn.workers),
            "visible_resources": len(turn.resource_cells),
            "known_resources": len(tactic.resource_last_seen),
            "delivery_blocked": delivery_blocked,
            "resource_blocked": resource_blocked,
        },
        "population": {
            "total": turn.state.population,
            "workers": len(turn.workers),
            "vanguards": len(turn.vanguards),
            "rangers": len(turn.rangers),
        },
        "core": {
            "alive": core is not None,
            "hp": core.hp if core is not None else None,
            "shield": core.shield if core is not None else None,
            "state": core.view.state.value if core is not None else "RESPAWNING",
        },
        "battlefield": {
            "visible_enemies": len(turn.visible_enemies),
            "danger_cells": len(tactic.last_danger_cells),
            "combat_pressure": bool(tactic.combat_pressure_active),
            "projected_core_damage": tactic.last_projected_core_damage,
            "core_survival_margin": tactic.last_core_survival_margin,
            "scout_chunks": len(tactic.scout_chunk_last_seen),
            "dedicated_scouts": len(tactic.dedicated_scout_ids),
        },
        "strategy": {
            "phase": tactic.strategy_phase(turn),
            "posture": parameters.posture.value,
            "source": parameters.source,
            "reason": _strategy_reason(parameters.source, context),
            "valid_until_tick": parameters.valid_until_tick,
            "worker_target": parameters.worker_target,
            "vanguard_target": parameters.vanguard_target,
            "ranger_target": parameters.ranger_target,
            "population_limit": parameters.population_limit,
            "economy_weight": parameters.economy_weight,
            "territory_weight": parameters.territory_weight,
            "combat_weight": parameters.combat_weight,
            "safety_weight": parameters.safety_weight,
            "beacon_priority": parameters.beacon_priority,
            "scout_percent": parameters.scout_percent,
        },
        "adviser": adviser_status,
        "actions": _safe_count_map(action_counts),
        "event_counts": _safe_count_map(event_counts),
        "worker_modes": _safe_count_map(mode_counts),
        "events": safe_events,
    }


def _strategy_reason(source: str, context: object | None) -> str:
    if source == "local-safety":
        return "emergency_safety_override"
    if source == "local-pressure":
        return "visible_enemy_core_opportunity"
    if source == "local-contest":
        return "beacon_contest_enabled"
    if source == "local-expand":
        if context is not None and getattr(context, "storage_saturated", False):
            return "storage_saturated"
        return "sustained_economic_evidence"
    if source == "model" or source.startswith("model:"):
        return "validated_model_advice"
    return "deterministic_baseline"


class AsyncObservationWriter:
    def __init__(self, directory: Path, *, queue_size: int = 128) -> None:
        self.directory = directory
        self._queue: Queue[dict[str, Any] | None] = Queue(maxsize=queue_size)
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="arena-observation-writer", daemon=True
        )
        self._thread.start()

    def submit(self, observation: dict[str, Any]) -> bool:
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait(observation)
            return True
        except Full:
            try:
                self._queue.get_nowait()
            except Empty:
                return False
            try:
                self._queue.put_nowait(observation)
                return True
            except Full:
                return False

    def close(self) -> None:
        self._closed.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(None)
            except (Empty, Full):
                pass
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except Empty:
                if self._closed.is_set():
                    return
                continue
            if item is None:
                return
            try:
                self._write(item)
            except (OSError, TypeError, ValueError):
                continue

    def _write(self, observation: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._atomic_snapshot(observation)
        events = observation.get("events", [])
        if events:
            day = str(observation["generated_at"])[:10]
            event_path = self.directory / f"events-{day}.jsonl"
            with event_path.open("a", encoding="utf-8", newline="\n") as handle:
                for event in events:
                    handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")))
                    handle.write("\n")
            os.chmod(event_path, 0o640)
        self._prune_event_files()

    def _atomic_snapshot(self, observation: dict[str, Any]) -> None:
        path = self.directory / "snapshot.json"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.directory,
                prefix=".snapshot.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(observation, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _prune_event_files(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=EVENT_RETENTION_DAYS)).date()
        for path in self.directory.glob("events-*.jsonl"):
            try:
                event_day = datetime.strptime(path.stem.removeprefix("events-"), "%Y-%m-%d").date()
            except ValueError:
                continue
            if event_day < cutoff:
                path.unlink(missing_ok=True)
