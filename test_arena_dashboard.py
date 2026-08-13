from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from arena_dashboard import DashboardStore, _downsample, create_app, deployment_metadata
from arena_tactical import TacticalController
from test_arena_farmer import WORKER_1, make_turn, unit


def sample_observation() -> dict[str, object]:
    return {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tick": 120,
        "agent": {"core_alive": True, "compatibility_hold": False, "recovery": False},
        "economy": {
            "resources": 80,
            "capacity": 100,
            "cargo": 6,
            "visible_resources": 2,
            "known_resources": 7,
            "delivery_blocked": 1,
            "resource_blocked": 0,
        },
        "population": {"total": 20, "workers": 13, "vanguards": 3, "rangers": 4},
        "core": {"alive": True, "hp": 5, "shield": 5, "state": "NORMAL"},
        "battlefield": {
            "visible_enemies": 0,
            "danger_cells": 0,
            "combat_pressure": False,
            "projected_core_damage": 0,
            "core_survival_margin": 10,
            "scout_chunks": 12,
            "dedicated_scouts": 3,
            "beacon_runner_active": False,
            "combat_patrol_units": 2,
        },
        "strategy": {
            "phase": "EXPANSION",
            "posture": "EXPAND",
            "source": "local-expand",
            "reason": "storage_saturated",
            "valid_until_tick": 248,
            "worker_target": 17,
            "vanguard_target": 3,
            "ranger_target": 4,
            "population_limit": 30,
            "economy_weight": 7,
            "territory_weight": 6,
            "combat_weight": 4,
            "safety_weight": 7,
            "beacon_priority": 1,
            "scout_percent": 30,
            "force_stage": "MOBILIZE",
            "force_stage_index": 1,
            "force_target_population": 26,
            "force_target_workers": 12,
            "force_target_vanguards": 6,
            "force_target_rangers": 8,
            "force_worker_deficit": 0,
            "force_vanguard_deficit": 3,
            "force_ranger_deficit": 4,
        },
        "adviser": {
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
        },
        "actions": {"MOVE": 10, "WAIT": 10},
        "event_counts": {"DEPOSIT_SUCCEEDED": 1},
        "worker_modes": {"HARVEST": 7, "RETURN_BLOCKED": 1},
        "events": [],
    }


class ArenaDashboardTests(unittest.TestCase):
    def test_history_downsampling_is_bounded_and_keeps_endpoints(self) -> None:
        values = [{"tick": tick} for tick in range(2_000)]

        sampled = _downsample(values)

        self.assertEqual(len(sampled), 720)
        self.assertEqual(sampled[0]["tick"], 0)
        self.assertEqual(sampled[-1]["tick"], 1_999)

    def test_tactical_api_requires_proxy_auth_and_csrf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "snapshot.json").write_text(
                json.dumps(sample_observation()), encoding="utf-8"
            )
            tactical = TacticalController(root / "tactical")
            tactical.write_snapshot(
                make_turn(
                    tick=120,
                    units=[unit(WORKER_1, "WORKER", (2, 3), cargo=0)],
                ),
                emergency_reason=None,
                unit_modes={},
            )
            app = create_app(
                database_path=root / "dashboard.sqlite3",
                inbox_path=inbox,
                release_path=root,
                static_path=root / "missing-static",
                tactical_path=root / "tactical",
                collect_interval_seconds=60,
            )
            with TestClient(app, base_url="https://testserver") as client:
                self.assertEqual(client.get("/api/v1/tactical/state").status_code, 403)
                headers = {"X-Arena-Authenticated": "1"}
                state = client.get("/api/v1/tactical/state", headers=headers)
                self.assertEqual(state.status_code, 200)
                self.assertEqual(state.json()["units"][0]["id"], WORKER_1)

                without_csrf = client.post(
                    "/api/v1/tactical/commands",
                    headers=headers,
                    json={
                        "kind": "MOVE_UNITS",
                        "unit_ids": [WORKER_1],
                        "target_x": 8,
                        "target_y": 3,
                    },
                )
                self.assertEqual(without_csrf.status_code, 403)
                token = client.get("/api/v1/tactical/csrf", headers=headers).json()["csrf_token"]
                queued = client.post(
                    "/api/v1/tactical/commands",
                    headers={**headers, "X-Arena-CSRF": token},
                    json={
                        "kind": "MOVE_UNITS",
                        "unit_ids": [WORKER_1],
                        "target_x": 8,
                        "target_y": 3,
                        "ttl_ticks": 32,
                    },
                )
                self.assertEqual(queued.status_code, 202)
                self.assertEqual(queued.json()["issued_tick"], 120)
                command_files = list((root / "tactical" / "commands").glob("command-*.json"))
                self.assertEqual(len(command_files), 1)
                payload = json.loads(command_files[0].read_text(encoding="utf-8"))
                self.assertNotIn("action", payload)
                self.assertEqual(payload["target_x"], 8)

    def test_tactical_write_rejects_stale_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "snapshot.json").write_text(json.dumps(sample_observation()), encoding="utf-8")
            tactical = TacticalController(root / "tactical")
            tactical.write_snapshot(make_turn(tick=120), emergency_reason=None, unit_modes={})
            value = json.loads(tactical.snapshot_path.read_text(encoding="utf-8"))
            value["generated_at"] = (datetime.now(UTC) - __import__("datetime").timedelta(minutes=2)).isoformat()
            tactical.snapshot_path.write_text(json.dumps(value), encoding="utf-8")
            app = create_app(
                database_path=root / "dashboard.sqlite3",
                inbox_path=inbox,
                static_path=root / "missing-static",
                tactical_path=root / "tactical",
                collect_interval_seconds=60,
            )
            with TestClient(app, base_url="https://testserver") as client:
                headers = {"X-Arena-Authenticated": "1"}
                token = client.get("/api/v1/tactical/csrf", headers=headers).json()["csrf_token"]
                response = client.post(
                    "/api/v1/tactical/commands",
                    headers={**headers, "X-Arena-CSRF": token},
                    json={"kind": "MOVE_CORE", "target_x": 2, "target_y": 2},
                )
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"], "tactical_state_stale")

    def test_store_collects_valid_snapshot_and_redacted_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            database = root / "dashboard.sqlite3"
            (inbox / "snapshot.json").write_text(
                json.dumps(sample_observation()), encoding="utf-8"
            )
            event = {
                "event_id": "a" * 24,
                "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "tick": 120,
                "category": "ECONOMY",
                "event_type": "DEPOSIT_SUCCEEDED",
                "reason_code": None,
                "values": {"amount": 2},
            }
            (inbox / "events-2026-08-11.jsonl").write_text(
                json.dumps(event) + "\n", encoding="utf-8"
            )
            store = DashboardStore(database, inbox)
            store.initialize()
            store.collect()

            self.assertEqual(store.latest()["tick"], 120)
            self.assertEqual(store.history("24h")[1][0]["population"], 20)
            resolution, long_history = store.history("30d")
            self.assertEqual(resolution, "hourly")
            self.assertEqual(long_history[0]["population_max"], 20)
            self.assertEqual(store.events(10, None)[0]["values"], {"amount": 2})

    def test_api_is_read_only_and_returns_no_raw_log_or_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            database = root / "dashboard.sqlite3"
            release = root / "release"
            release.mkdir()
            (release / "source-commit").write_text("b" * 40 + "\n", encoding="utf-8")
            (inbox / "snapshot.json").write_text(
                json.dumps(sample_observation()), encoding="utf-8"
            )
            app = create_app(
                database_path=database,
                inbox_path=inbox,
                release_path=release,
                static_path=root / "missing-static",
                collect_interval_seconds=0.01,
            )
            with TestClient(app) as client:
                for _ in range(20):
                    response = client.get("/api/v1/overview")
                    if response.json()["observation"] is not None:
                        break
                self.assertEqual(response.status_code, 200)
                encoded = response.text
                self.assertNotIn("unit_id", encoded)
                self.assertNotIn("player_id", encoded)
                self.assertNotIn("raw_log", encoded)
                self.assertEqual(response.json()["observation"]["tick"], 120)
                self.assertEqual(client.post("/api/v1/overview").status_code, 405)
                self.assertEqual(
                    client.get("/api/v1/history?range=invalid").status_code, 400
                )

    def test_deployment_metadata_parses_named_systemd_properties(self) -> None:
        completed = __import__("subprocess").CompletedProcess(
            [],
            0,
            "NRestarts=2\nActiveState=active\nActiveEnterTimestamp=Mon 2026-08-10\n",
            "",
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "arena_dashboard.subprocess.run", return_value=completed
        ):
            metadata = deployment_metadata(Path(directory))

        self.assertTrue(metadata["service_active"])
        self.assertEqual(metadata["restarts"], 2)
        self.assertEqual(metadata["active_since"], "Mon 2026-08-10")


if __name__ == "__main__":
    unittest.main()
