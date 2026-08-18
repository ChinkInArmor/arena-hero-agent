from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from pydantic import ValidationError

from arena_tactical import TacticalCommand, TacticalController, TacticalSnapshot, enqueue_command
from test_arena_farmer import CORE_ID, ENEMY_1, RANGER_1, VANGUARD_1, WORKER_1, make_turn, unit


def command(kind: str, **overrides: object) -> TacticalCommand:
    values: dict[str, object] = {
        "command_id": str(uuid4()),
        "kind": kind,
        "issued_at": datetime.now(UTC),
        "issued_tick": 100,
        "ttl_ticks": 32,
    }
    values.update(overrides)
    return TacticalCommand(**values)


class TacticalSchemaTests(unittest.TestCase):
    def test_move_schema_is_fixed_and_rejects_raw_actions(self) -> None:
        value = command(
            "MOVE_UNITS",
            unit_ids=[UUID(WORKER_1)],
            target_x=12,
            target_y=-3,
        )
        self.assertEqual(value.expires_tick, 132)
        with self.assertRaises(ValidationError):
            TacticalCommand.model_validate({**value.model_dump(), "action": "SELF_DESTRUCT"})
        with self.assertRaises(ValidationError):
            command("MOVE_UNITS", unit_ids=[UUID(WORKER_1)], target_x=1)

    def test_ttl_and_production_weights_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            command("MOVE_CORE", target_x=1, target_y=2, ttl_ticks=65)
        with self.assertRaises(ValidationError):
            command(
                "SET_PRODUCTION_WEIGHTS",
                worker_weight=0,
                vanguard_weight=0,
                ranger_weight=0,
            )


class TacticalControllerTests(unittest.TestCase):
    def test_unknown_unit_and_control_conflict_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = TacticalController(root)
            unknown = command(
                "MOVE_UNITS", unit_ids=[uuid4()], target_x=5, target_y=5
            )
            enqueue_command(controller.inbox, unknown)
            controller.collect_commands(101, {UUID(WORKER_1)})
            receipt = next(controller.receipts.glob(f"receipt-{unknown.command_id}-*.json"))
            self.assertEqual(json.loads(receipt.read_text())["reason"], "unknown_unit")

            first = command(
                "MOVE_UNITS", unit_ids=[UUID(WORKER_1)], target_x=5, target_y=5
            )
            second = command(
                "MOVE_UNITS", unit_ids=[UUID(WORKER_1)], target_x=8, target_y=8
            )
            enqueue_command(controller.inbox, first)
            enqueue_command(controller.inbox, second)
            controller.collect_commands(102, {UUID(WORKER_1)})
            reasons = {
                json.loads(path.read_text())["reason"]
                for path in controller.receipts.glob("receipt-*.json")
            }
            self.assertIn("queued", reasons)
            self.assertIn("control_conflict", reasons)

    def test_restart_deduplicates_a_command_with_existing_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = TacticalController(root)
            value = command(
                "MOVE_UNITS", unit_ids=[UUID(WORKER_1)], target_x=5, target_y=5
            )
            enqueue_command(original.inbox, value)
            original.collect_commands(101, {UUID(WORKER_1)})

            restarted = TacticalController(root)
            enqueue_command(restarted.inbox, value)
            restarted.collect_commands(102, {UUID(WORKER_1)})
            self.assertFalse(restarted.active_orders)
            self.assertFalse(list(restarted.inbox.glob("command-*.json")))

    def test_emergency_override_and_expiry_return_control_to_auto(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = TacticalController(Path(directory))
            value = command(
                "MOVE_UNITS",
                unit_ids=[UUID(WORKER_1)],
                target_x=5,
                target_y=5,
                ttl_ticks=1,
            )
            enqueue_command(controller.inbox, value)
            controller.collect_commands(100, {UUID(WORKER_1)})
            self.assertEqual(controller.active_mode, "MANUAL")
            controller.emergency_override(101, "core_survival")
            self.assertEqual(controller.active_mode, "AUTO")
            receipt_values = [json.loads(path.read_text()) for path in controller.receipts.glob("*.json")]
            self.assertIn("OVERRIDDEN", {item["status"] for item in receipt_values})

    def test_expedition_assigns_requested_military_without_workers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = TacticalController(Path(directory))
            value = command(
                "SET_EXPEDITION",
                expedition_id="north-1",
                name="North",
                target_x=20,
                target_y=10,
                vanguard_count=1,
                ranger_count=1,
            )
            enqueue_command(controller.inbox, value)
            units = [
                unit(WORKER_1, "WORKER", (0, 0), cargo=0),
                unit(VANGUARD_1, "VANGUARD", (1, 0)),
                unit(RANGER_1, "RANGER", (-1, 0)),
            ]
            controller.collect_commands(101, {UUID(WORKER_1), UUID(VANGUARD_1), UUID(RANGER_1)})
            controller.materialize_expeditions(make_turn(tick=101, units=units), 101)
            self.assertEqual(set(controller.active_orders), {UUID(VANGUARD_1), UUID(RANGER_1)})
            self.assertTrue(all(order.mode == "EXPEDITION" for order in controller.active_orders.values()))

    def test_private_snapshot_contains_coordinates_and_control_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = TacticalController(Path(directory))
            turn = make_turn(
                tick=120,
                core_position=(4, 5),
                units=[unit(WORKER_1, "WORKER", (6, 5), cargo=1)],
                resource_cells=[(7, 5)],
                obstacles=[(8, 5)],
            )
            controller.write_snapshot(turn, emergency_reason=None, unit_modes={})
            snapshot = TacticalSnapshot.model_validate_json(
                controller.snapshot_path.read_text(encoding="utf-8")
            )
            self.assertEqual(snapshot.tick, 120)
            self.assertEqual((snapshot.units[0].x, snapshot.units[0].y), (6, 5))
            self.assertEqual({item.kind for item in snapshot.objects}, {"CORE", "RESOURCE", "OBSTACLE", "BEACON"})
            self.assertTrue((controller.history / "tick-00000000000000000120.json").exists())

    def test_snapshot_behavior_and_memory_layers(self) -> None:
        """worker behavior passes through; memory persists obstacles,
        tracks resource/enemy last_seen_tick, and prunes stale entries.
        """
        with tempfile.TemporaryDirectory() as directory:
            controller = TacticalController(Path(directory))
            first = make_turn(
                tick=200,
                core_position=(0, 0),
                units=[unit(WORKER_1, "WORKER", (1, 1), cargo=0)],
                resource_cells=[(3, 3)],
                obstacles=[(-5, -5)],
                enemies=[unit(ENEMY_1, "RANGER", (-2, -2), controlled=False)],
            )
            controller.write_snapshot(
                first, emergency_reason=None, unit_modes={UUID(WORKER_1): "RETURN_BLOCKED"}
            )
            snapshot = TacticalSnapshot.model_validate_json(
                controller.snapshot_path.read_text(encoding="utf-8")
            )
            self.assertEqual(snapshot.units[0].behavior, "RETURN_BLOCKED")
            self.assertIsNotNone(snapshot.memory)
            assert snapshot.memory is not None
            self.assertIn([-5, -5], snapshot.memory.obstacles)
            self.assertEqual(
                [(entry.x, entry.y, entry.last_seen_tick) for entry in snapshot.memory.resources],
                [(3, 3, 200)],
            )
            self.assertEqual(
                [(entry.x, entry.y, entry.unit_type) for entry in snapshot.memory.enemies],
                [(-2, -2, "RANGER")],
            )
            # history files are payload-only and omit the memory layer
            history_tick = controller.history / "tick-00000000000000000200.json"
            history_payload = json.loads(history_tick.read_text(encoding="utf-8"))
            self.assertNotIn("memory", history_payload)
            self.assertTrue(history_payload["units"][0]["behavior"] == "RETURN_BLOCKED")

            # a stale enemy expires after MEMORY_ENEMY_TTL ticks; a static
            # obstacle never expires; a refreshed resource resets its TTL.
            later = make_turn(
                tick=200 + controller.MEMORY_ENEMY_TTL + 10,
                core_position=(0, 0),
                units=[unit(WORKER_1, "WORKER", (1, 1))],
                obstacles=[(-6, -6), (-5, -5)],
                resource_cells=[(3, 3)],
            )
            controller.write_snapshot(later, emergency_reason=None, unit_modes={})
            refreshed = TacticalSnapshot.model_validate_json(
                controller.snapshot_path.read_text(encoding="utf-8")
            )
            assert refreshed.memory is not None
            self.assertIn([-5, -5], refreshed.memory.obstacles)
            self.assertIn([-6, -6], refreshed.memory.obstacles)
            self.assertEqual(refreshed.memory.enemies, [])
            self.assertEqual(refreshed.memory.resources[0].last_seen_tick, 200 + controller.MEMORY_ENEMY_TTL + 10)


if __name__ == "__main__":
    unittest.main()
