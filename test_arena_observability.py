from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from arena_observability import AsyncObservationWriter, build_observation
from arena_strategy import BASELINE_PARAMETERS, StrategicContext


class FakePlan:
    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "unit_actions": {
                "private-unit-id": {"type": "MOVE", "direction": "N"},
            },
            "core_action": {"type": "SPAWN", "unit_type": "WORKER"},
        }


class FakeAdviser:
    def telemetry(self, _tick: int, _source: str) -> dict[str, object]:
        return {
            "enabled": True,
            "provider": "openai-compatible",
            "model": "test-model",
            "outcome": "applied",
            "requests": 1,
            "applied": 1,
            "failures": 0,
            "last_request_tick": 100,
            "last_applied_tick": 100,
            "next_request_tick": 356,
            "advice_valid_until_tick": 500,
            "ttl_remaining_ticks": 399,
            "overridden": False,
        }


class ArenaObservabilityTests(unittest.TestCase):
    def _observation(self) -> dict[str, object]:
        context = StrategicContext(
            tick=101,
            resources=95,
            resource_capacity=95,
            population=19,
            workers=12,
            vanguards=3,
            rangers=4,
            deposits_window=12,
            blocked_ticks=0,
            known_resources=4,
            scout_chunks=8,
            visible_enemies=1,
            visible_enemy_cores=0,
            beacon_distance=500,
            beacon_contest_enabled=False,
            threat_level="NORMAL",
            recovery=False,
            compatibility_hold=False,
        )
        event = SimpleNamespace(
            event_type="CORE_DAMAGED",
            reason_code="ATTACK",
            actor_id="private-actor-id",
            target_id="private-target-id",
            values={"damage": 2, "actor_id": 123, "secret": 99},
        )
        core = SimpleNamespace(
            hp=5,
            shield=3,
            view=SimpleNamespace(state=SimpleNamespace(value="NORMAL")),
        )
        worker = SimpleNamespace(cargo=1)
        turn = SimpleNamespace(
            plan=FakePlan(),
            events=[event],
            core=core,
            resources=95,
            resource_capacity=95,
            workers=[worker] * 12,
            vanguards=[object()] * 3,
            rangers=[object()] * 4,
            state=SimpleNamespace(population=19),
            resource_cells=[object()] * 2,
            visible_enemies=[object()],
        )
        tactic = SimpleNamespace(
            worker_modes={"a": "RETURN_BLOCKED", "b": "HARVEST"},
            compatibility_hold=False,
            recovery_mode=False,
            resource_last_seen={(1, 2): 100},
            last_danger_cells={(2, 2)},
            combat_pressure_active=True,
            last_projected_core_damage=2,
            last_core_survival_margin=6,
            scout_chunk_last_seen={(0, 0): 100},
            dedicated_scout_ids={"a"},
            strategic_parameters=BASELINE_PARAMETERS,
            current_strategic_context=context,
            strategic_controller=SimpleNamespace(adviser=FakeAdviser()),
            strategy_phase=lambda _turn: "FORTIFY",
        )
        return build_observation(turn, tactic, 101)

    def test_observation_removes_identifiers_and_unapproved_event_values(self) -> None:
        observation = self._observation()
        encoded = json.dumps(observation)

        self.assertNotIn("private-unit-id", encoded)
        self.assertNotIn("private-actor-id", encoded)
        self.assertNotIn("private-target-id", encoded)
        self.assertNotIn("secret", encoded)
        self.assertEqual(observation["events"][0]["values"], {"damage": 2})
        self.assertEqual(observation["actions"], {"MOVE": 1, "SPAWN": 1})

    def test_async_writer_atomically_writes_snapshot_and_daily_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = AsyncObservationWriter(root)
            try:
                self.assertTrue(writer.submit(self._observation()))
                for _ in range(100):
                    if (root / "snapshot.json").exists():
                        break
                    time.sleep(0.01)
            finally:
                writer.close()

            snapshot = json.loads((root / "snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(snapshot["tick"], 101)
            event_files = list(root.glob("events-*.jsonl"))
            self.assertEqual(len(event_files), 1)
            event = json.loads(event_files[0].read_text(encoding="utf-8"))
            self.assertEqual(event["event_type"], "CORE_DAMAGED")


if __name__ == "__main__":
    unittest.main()
