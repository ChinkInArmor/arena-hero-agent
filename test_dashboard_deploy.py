from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class DashboardDeploymentContractTests(unittest.TestCase):
    def test_dashboard_unit_is_loopback_and_separate_from_agent_credentials(self) -> None:
        unit = (ROOT / "deploy" / "arena-hero-dashboard.service").read_text(encoding="utf-8")
        self.assertIn("User=arena-hero-dashboard", unit)
        self.assertIn("Group=arena-hero-observe", unit)
        self.assertIn("--host 127.0.0.1 --port 8765", unit)
        self.assertIn("ReadWritePaths=/var/lib/arena-hero-dashboard", unit)
        self.assertNotIn("ARENA_HERO_API_KEY", unit)
        self.assertNotIn("ARENA_STRATEGY_API_KEY", unit)

    def test_caddy_contract_has_auth_and_does_not_log_authorization(self) -> None:
        caddy = (ROOT / "deploy" / "arena-hero-dashboard.caddy").read_text(encoding="utf-8")
        self.assertIn("arena.911439925.xyz", caddy)
        self.assertIn("basic_auth", caddy)
        self.assertIn("request>headers>Authorization delete", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:8765", caddy)

    def test_installer_requires_hash_file_and_has_no_plaintext_password_option(self) -> None:
        script = (ROOT / "scripts" / "install-dashboard.sh").read_text(encoding="utf-8")
        self.assertIn("--password-hash-file", script)
        self.assertIn("caddy validate", script)
        self.assertNotIn("--plaintext", script)
        self.assertNotIn("password_hash=\"$2", script)


if __name__ == "__main__":
    unittest.main()
