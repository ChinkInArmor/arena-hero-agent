from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from arena_strategy import (
    AdviserConfig,
    AsyncStrategicAdviser,
    BASELINE_PARAMETERS,
    beacon_priority_for_context,
    StrategicAdviceClient,
    StrategicContext,
    StrategicController,
    StrategicPosture,
    StrategicState,
    PopulationHealth,
    StrategyValidationError,
    force_stage,
    plan_local_strategy,
    resource_assignment_cost,
    select_marginal_unit,
    validate_strategic_advice,
)


def context(**overrides: object) -> StrategicContext:
    values: dict[str, object] = {
        "tick": 1000,
        "resources": 60,
        "resource_capacity": 120,
        "population": 24,
        "workers": 17,
        "vanguards": 3,
        "rangers": 4,
        "deposits_window": 4,
        "blocked_ticks": 0,
        "known_resources": 3,
        "scout_chunks": 24,
        "visible_enemies": 0,
        "visible_enemy_cores": 0,
        "beacon_distance": 200,
        "beacon_contest_enabled": False,
        "threat_level": "NORMAL",
        "recovery": False,
        "compatibility_hold": False,
        "economic_window_ticks": 32,
    }
    values.update(overrides)
    return StrategicContext(**values)  # type: ignore[arg-type]


def candidate(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "posture": "EXPAND",
        "economy_weight": 7,
        "territory_weight": 7,
        "combat_weight": 5,
        "safety_weight": 6,
        "beacon_priority": 3,
        "scout_percent": 30,
        "ttl_ticks": 256,
    }
    values.update(overrides)
    return values


class StrategicValidationTests(unittest.TestCase):
    def test_valid_advice_is_bounded_and_gets_absolute_expiry(self) -> None:
        result = validate_strategic_advice(candidate(), current_tick=900)
        self.assertEqual(result.posture, StrategicPosture.EXPAND)
        self.assertEqual(result.valid_until_tick, 1156)
        self.assertEqual(result.source, "model")

    def test_unknown_fields_and_production_authority_are_rejected(self) -> None:
        with self.assertRaisesRegex(StrategyValidationError, "fields"):
            validate_strategic_advice(
                {**candidate(), "commands": ["ATTACK"]}, current_tick=1
            )
        with self.assertRaisesRegex(StrategyValidationError, "fields"):
            validate_strategic_advice(
                {**candidate(), "population_limit": 48}, current_tick=1
            )
        with self.assertRaisesRegex(StrategyValidationError, "fields"):
            validate_strategic_advice(
                {**candidate(), "worker_target": 18}, current_tick=1
            )

    def test_force_stage_reports_the_next_composition_deficits(self) -> None:
        self.assertEqual(force_stage(24, 17, 3, 4)["name"], "MOBILIZE")
        control = force_stage(26, 12, 6, 8)
        self.assertEqual(control["name"], "CONTROL")
        self.assertEqual(control["worker_deficit"], 6)
        self.assertEqual(control["vanguard_deficit"], 4)
        self.assertEqual(control["ranger_deficit"], 4)
        self.assertEqual(force_stage(48, 18, 14, 16)["name"], "OVERWHELM")

    def test_adviser_configuration_enforces_cadence_and_provider(self) -> None:
        with self.assertRaises(ValueError):
            AdviserConfig("openai-compatible", "http://localhost:11434", "m", interval_ticks=64)
        with self.assertRaises(ValueError):
            AdviserConfig("unknown", "https://example.test", "m")
        self.assertEqual(
            AdviserConfig(
                "openai-compatible",
                "https://example.test",
                "m",
                timeout_seconds=60,
            ).timeout_seconds,
            60,
        )
        with self.assertRaisesRegex(ValueError, "0 and 60"):
            AdviserConfig(
                "openai-compatible",
                "https://example.test",
                "m",
                timeout_seconds=60.1,
            )


class LocalPlannerTests(unittest.TestCase):
    def test_enemy_core_pressure_raises_only_the_military_target(self) -> None:
        result = plan_local_strategy(
            context(visible_enemy_cores=1, vanguards=4, rangers=5)
        )
        self.assertEqual(result.posture, StrategicPosture.PRESSURE)
        self.assertEqual(result.economic_target, 24)
        self.assertGreater(result.military_target, result.economic_target)
        self.assertEqual(result.production_ceiling, result.military_target)

    def test_no_evidence_preserves_consolidating_profile(self) -> None:
        result = plan_local_strategy(context())
        self.assertEqual(result.production_ceiling, 24)
        self.assertEqual(result.worker_target, 17)
        self.assertEqual(result.source, "local")

    def test_saturated_storage_alone_does_not_open_growth(self) -> None:
        result = plan_local_strategy(context(resources=120))
        self.assertEqual(result.posture, StrategicPosture.CONSOLIDATE)
        self.assertEqual(result.production_ceiling, 24)

    def test_sustained_throughput_opens_economic_growth(self) -> None:
        result = plan_local_strategy(
            context(deposits_window=20, blocked_ticks=0, known_resources=2)
        )
        self.assertEqual(result.posture, StrategicPosture.EXPAND)
        self.assertGreater(result.economic_target, BASELINE_PARAMETERS.production_ceiling)
        self.assertGreater(result.territory_weight, BASELINE_PARAMETERS.territory_weight)

    def test_population_40_is_overextended_and_freezes_discretionary_growth(self) -> None:
        result = plan_local_strategy(
            context(
                resources=200,
                resource_capacity=200,
                population=40,
                workers=18,
                vanguards=10,
                rangers=12,
                known_resources=1,
            )
        )
        self.assertEqual(result.state, StrategicState.OVEREXTENDED)
        self.assertEqual(result.population_health, PopulationHealth.OVEREXTENDED)
        self.assertLess(result.production_ceiling, 40)
        self.assertNotEqual(result.production_ceiling, 48)

    def test_emergency_state_overrides_growth_evidence(self) -> None:
        result = plan_local_strategy(
            context(resources=120, threat_level="ENGAGED", visible_enemies=2)
        )
        self.assertEqual(result.posture, StrategicPosture.CONSOLIDATE)
        self.assertEqual(result.source, "local-safety")

    def test_beacon_value_accounts_for_distance_and_opportunity_cost(self) -> None:
        self.assertEqual(
            beacon_priority_for_context(
                context(beacon_distance=20, beacon_contest_enabled=True)
            ).value,
            "PRIMARY",
        )
        self.assertEqual(
            beacon_priority_for_context(
                context(
                    beacon_distance=20,
                    beacon_contest_enabled=True,
                    resources=20,
                )
            ).value,
            "DEFERRED",
        )
        self.assertEqual(
            beacon_priority_for_context(
                context(
                    beacon_distance=80,
                    beacon_contest_enabled=True,
                )
            ).value,
            "SECONDARY",
        )

    def test_beacon_contest_requires_explicit_policy(self) -> None:
        held = plan_local_strategy(context(beacon_distance=20))
        pursuing = plan_local_strategy(
            context(beacon_distance=20, beacon_contest_enabled=True)
        )
        self.assertNotEqual(held.posture, StrategicPosture.CONTEST)
        self.assertEqual(pursuing.posture, StrategicPosture.CONTEST)

    def test_marginal_selection_prices_deficits_and_utility(self) -> None:
        expanding = plan_local_strategy(
            context(resources=120, deposits_window=20, known_resources=2)
        )
        selected = select_marginal_unit(
            workers=17,
            vanguards=3,
            rangers=4,
            worker_cost=7,
            vanguard_cost=13,
            ranger_cost=16,
            parameters=expanding,
        )
        self.assertEqual(selected, "WORKER")
        pressure = replace(
            expanding,
            worker_target=17,
            vanguard_target=6,
            ranger_target=9,
            economy_weight=2,
            combat_weight=10,
        )
        self.assertIn(
            select_marginal_unit(
                workers=17,
                vanguards=3,
                rangers=4,
                worker_cost=7,
                vanguard_cost=13,
                ranger_cost=16,
                parameters=pressure,
            ),
            {"VANGUARD", "RANGER"},
        )

    def test_influence_cost_keeps_path_primary(self) -> None:
        parameters = plan_local_strategy(context(resources=120))
        near = resource_assignment_cost(
            path_cost=2,
            resource_age=0,
            resource_quota=2,
            core_distance=8,
            sticky=False,
            parameters=parameters,
        )
        far_rich = resource_assignment_cost(
            path_cost=10,
            resource_age=0,
            resource_quota=16,
            core_distance=8,
            sticky=False,
            parameters=parameters,
        )
        self.assertLess(near, far_rich)


class _FakeResponse:
    def __init__(self, data: object) -> None:
        self.data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.data


class _FakeClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, *, json: object) -> _FakeResponse:
        self.calls.append((url, json))
        return _FakeResponse(self.response)


class AdviceClientTests(unittest.TestCase):
    def test_openai_compatible_transport_accepts_local_endpoint_without_key(self) -> None:
        fake = _FakeClient(
            {"choices": [{"message": {"content": json.dumps(candidate())}}]}
        )
        with patch("arena_strategy.httpx.Client", return_value=fake) as factory:
            result = StrategicAdviceClient(
                AdviserConfig(
                    "openai-compatible", "http://127.0.0.1:11434/v1", "local"
                )
            ).request(context(), BASELINE_PARAMETERS)

        self.assertEqual(result.source, "model:openai-compatible")
        self.assertEqual(fake.calls[0][0], "http://127.0.0.1:11434/v1/chat/completions")
        self.assertNotIn("Authorization", factory.call_args.kwargs["headers"])

    def test_anthropic_transport_reads_key_file_without_putting_key_in_body(self) -> None:
        fake = _FakeClient(
            {"content": [{"type": "text", "text": json.dumps(candidate())}]}
        )
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "model.key"
            key_file.write_text("test-model-secret", encoding="utf-8")
            with patch("arena_strategy.httpx.Client", return_value=fake) as factory:
                result = StrategicAdviceClient(
                    AdviserConfig(
                        "anthropic",
                        "https://api.anthropic.com",
                        "claude-test",
                        key_file,
                    )
                ).request(context(), BASELINE_PARAMETERS)

        self.assertEqual(result.source, "model:anthropic")
        self.assertEqual(fake.calls[0][0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(factory.call_args.kwargs["headers"]["x-api-key"], "test-model-secret")
        self.assertNotIn("test-model-secret", json.dumps(fake.calls[0][1]))


class AsyncAdviserTests(unittest.TestCase):
    def test_advice_is_applied_then_expires(self) -> None:
        called = threading.Event()
        advice = validate_strategic_advice(candidate(ttl_ticks=128), current_tick=1000)

        def request(_context: StrategicContext, _local: object):
            called.set()
            return advice

        adviser = AsyncStrategicAdviser(
            AdviserConfig("openai-compatible", "http://localhost:11434", "local"),
            requester=request,
        )
        try:
            controller = StrategicController(adviser)
            first_context = context()
            controller.update(first_context)
            controller.observe_accepted(first_context)
            self.assertTrue(called.wait(1.0))
            for _ in range(50):
                result = controller.update(context(tick=1001))
                if adviser.last_outcome == "applied":
                    result = controller.update(context(tick=1001))
                    break
                time.sleep(0.01)
            self.assertEqual(result.source, "model")
            telemetry = adviser.telemetry(1001, "model")
            self.assertEqual(telemetry["requests"], 1)
            self.assertEqual(telemetry["applied"], 1)
            self.assertEqual(telemetry["failures"], 0)
            self.assertEqual(telemetry["ttl_remaining_ticks"], 127)
            self.assertFalse(telemetry["overridden"])
            expired = controller.update(context(tick=1129))
            self.assertNotEqual(expired.source, "model")
        finally:
            adviser.close()

    def test_request_failure_retains_deterministic_strategy(self) -> None:
        called = threading.Event()

        def fail(_context: StrategicContext, _local: object):
            called.set()
            raise TimeoutError

        adviser = AsyncStrategicAdviser(
            AdviserConfig("openai-compatible", "http://localhost:11434", "local"),
            requester=fail,
        )
        try:
            controller = StrategicController(adviser)
            first_context = context(resources=120)
            result = controller.update(first_context)
            controller.observe_accepted(first_context)
            self.assertTrue(called.wait(1.0))
            for _ in range(20):
                if adviser.last_outcome.startswith("failed"):
                    break
                time.sleep(0.01)
            self.assertNotEqual(result.source, "model")
            self.assertEqual(adviser.last_outcome, "failed:TimeoutError")
            telemetry = adviser.telemetry(first_context.tick, result.source)
            self.assertEqual(telemetry["requests"], 1)
            self.assertEqual(telemetry["failures"], 1)
        finally:
            adviser.close()

    def test_emergency_ignores_live_model_advice(self) -> None:
        advised = validate_strategic_advice(candidate(), current_tick=1000)
        adviser = AsyncStrategicAdviser(
            AdviserConfig("openai-compatible", "http://localhost:11434", "local"),
            requester=lambda _context, _local: advised,
        )
        try:
            controller = StrategicController(adviser)
            first_context = context()
            controller.update(first_context)
            controller.observe_accepted(first_context)
            for _ in range(20):
                result = controller.update(context(tick=1001))
                if result.source == "model":
                    break
                time.sleep(0.01)
            emergency = controller.update(context(tick=1002, threat_level="BREAKOUT"))
            self.assertEqual(emergency.source, "local-safety")
        finally:
            adviser.close()

    def test_model_advice_cannot_raise_the_local_production_ceiling(self) -> None:
        advice = validate_strategic_advice(candidate(), current_tick=1000)
        adviser = AsyncStrategicAdviser(
            AdviserConfig("openai-compatible", "http://localhost:11434", "local"),
            requester=lambda _context, _local: advice,
        )
        try:
            controller = StrategicController(adviser)
            initial = context(
                population=40,
                workers=18,
                vanguards=10,
                rangers=12,
                resources=200,
                resource_capacity=200,
                known_resources=1,
            )
            controller.update(initial)
            controller.observe_accepted(initial)
            for _ in range(50):
                result = controller.update(context(
                    tick=1001,
                    population=40,
                    workers=18,
                    vanguards=10,
                    rangers=12,
                    resources=200,
                    resource_capacity=200,
                    known_resources=1,
                ))
                if adviser.last_outcome == "applied":
                    result = controller.update(context(
                        tick=1001,
                        population=40,
                        workers=18,
                        vanguards=10,
                        rangers=12,
                        resources=200,
                        resource_capacity=200,
                        known_resources=1,
                    ))
                    break
                time.sleep(0.01)
            self.assertEqual(result.source, "model")
            self.assertEqual(result.production_ceiling, 24)
            self.assertEqual(result.population_health, PopulationHealth.OVEREXTENDED)
        finally:
            adviser.close()


if __name__ == "__main__":
    unittest.main()
