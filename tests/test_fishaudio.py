import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gui_settings import normalize_platform, validate_config
from platforms import fishaudio
import grok_register_ttk as app


class FishAuthPayloadTests(unittest.TestCase):
    def test_build_auth_payload_matches_expected_shape(self):
        now = datetime(2026, 9, 6, 13, 41, 39, 747143, tzinfo=timezone.utc)
        payload = fishaudio.build_auth_payload(
            email="demo@example.com",
            token="07b35b95-0832-4897-b4dd-70d5768dee7f",
            user_id="c3e015b0779e4939b3e73dde638bbc9c",
            active_team_id="c3e015b0779e4939b3e73dde638bbc9c",
            active_workspace_id="c3e015b0779e4939b3e73dde638bbc9c",
            session_ttl_sec=7 * 24 * 3600,
            now=now,
        )
        self.assertEqual(payload["type"], "fishaudio")
        self.assertEqual(payload["access_token"], payload["refresh_token"])
        self.assertEqual(payload["project_id"], "c3e015b0779e4939b3e73dde638bbc9c")
        self.assertEqual(payload["device_id"], payload["project_id"])
        self.assertEqual(payload["sub"], payload["project_id"])
        self.assertEqual(payload["auth_kind"], "session")
        self.assertEqual(payload["email"], "demo@example.com")
        self.assertTrue(payload["expired"].endswith("Z"))
        self.assertTrue(payload["last_refresh"].endswith("Z"))
        self.assertNotIn("active_team_id", payload)
        self.assertNotIn("active_workspace_id", payload)

    def test_write_auth_file_uses_sanitized_name(self):
        payload = fishaudio.build_auth_payload(
            email="a+b@example.com",
            token="token-1",
            user_id="user-1",
        )
        with tempfile.TemporaryDirectory() as temp:
            path = fishaudio.write_auth_file(temp, payload)
            self.assertTrue(path.endswith("fishaudio-a-b@example.com.json"))
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(loaded["access_token"], "token-1")


class FishOtpExtractionTests(unittest.TestCase):
    def test_extract_chinese_otp(self):
        self.assertEqual(
            fishaudio.extract_fish_otp("您的验证码是 482913，10 分钟内有效"),
            "482913",
        )

    def test_prefer_fish_otp_in_shared_extractor(self):
        code = app.extract_verification_code(
            "Fish Audio verification code: 123456",
            prefer_fish_otp=True,
        )
        self.assertEqual(code, "123456")

    def test_xai_code_still_preferred_for_grok(self):
        code = app.extract_verification_code(
            "AAA-BBB is not used",
            subject="Q1W-E2R xAI verification",
            prefer_fish_otp=False,
        )
        self.assertEqual(code, "Q1W-E2R")


class FishConfigValidationTests(unittest.TestCase):
    def test_normalize_platform(self):
        self.assertEqual(normalize_platform("Fish Audio"), "fishaudio")
        self.assertEqual(normalize_platform("fish_audio"), "fishaudio")
        self.assertEqual(normalize_platform("grok"), "grok")

    def test_fish_platform_skips_cpa_requirements(self):
        values = dict(
            app.DEFAULT_CONFIG,
            platform="fishaudio",
            fish_auth_dir="fish_auths",
            register_count=1,
            concurrent_count=1,
            cpa_export_enabled=True,
            cpa_auth_dir="",
            cpa_base_url="",
            email_provider="duckmail",
        )
        validate_config(values)
        self.assertEqual(values["platform"], "fishaudio")

    def test_fish_platform_requires_auth_dir(self):
        values = dict(
            app.DEFAULT_CONFIG,
            platform="fishaudio",
            fish_auth_dir="",
            register_count=1,
            concurrent_count=1,
            email_provider="duckmail",
        )
        with self.assertRaises(ValueError):
            validate_config(values)


if __name__ == "__main__":
    unittest.main()
