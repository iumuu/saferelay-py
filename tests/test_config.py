import os
import sys
import types
import unittest
from unittest.mock import patch


class ConfigTests(unittest.TestCase):
    def _load_config(self, env):
        dotenv_stub = types.SimpleNamespace(load_dotenv=lambda: None)
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, {"dotenv": dotenv_stub}):
            import importlib
            import config

            return importlib.reload(config).Config()

    def test_admin_ids_are_parsed_and_deduplicated(self):
        cfg = self._load_config(
            {
                "BOT_TOKEN": "123456:ABC",
                "ADMIN_IDS": " 42, 42;100, invalid, -7 ",
                "GROUP_ID": "-1001234567890",
                "API_ID": "1",
            }
        )

        self.assertEqual(cfg.admin_ids, [42, 100, -7])
        self.assertEqual(cfg.admin_uid, 42)

    def test_validate_reports_required_fields(self):
        cfg = self._load_config({})

        errors = cfg.validate()

        self.assertIn("BOT_TOKEN is required", errors)
        self.assertIn("ADMIN_IDS is required", errors)
        self.assertIn("GROUP_ID is required", errors)

    def test_hcaptcha_requires_keys_and_webapp_url(self):
        cfg = self._load_config(
            {
                "BOT_TOKEN": "123456:ABC",
                "ADMIN_IDS": "42",
                "GROUP_ID": "-1001234567890",
                "API_ID": "1",
                "VERIFY_PROVIDER": "hcaptcha",
            }
        )

        errors = cfg.validate()

        self.assertIn("HCAPTCHA_SITE_KEY is required", errors)
        self.assertIn("HCAPTCHA_SECRET is required", errors)
        self.assertIn("HCAPTCHA_WEBAPP_URL is required", errors)

    def test_hcaptcha_config_can_validate(self):
        cfg = self._load_config(
            {
                "BOT_TOKEN": "123456:ABC",
                "ADMIN_IDS": "42",
                "GROUP_ID": "-1001234567890",
                "API_ID": "1",
                "VERIFY_PROVIDER": "hcaptcha",
                "HCAPTCHA_SITE_KEY": "site-key",
                "HCAPTCHA_SECRET": "secret",
                "HCAPTCHA_WEBAPP_URL": "https://example.com/hcaptcha.html",
            }
        )

        self.assertEqual(cfg.validate(), "")
        self.assertEqual(cfg.verify_provider, "hcaptcha")
        self.assertTrue(cfg.protect_user_content)


if __name__ == "__main__":
    unittest.main()
