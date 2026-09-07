import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gui_settings import is_local_auth_platform, normalize_platform, validate_config
from platforms import elevenlabs
import grok_register_ttk as app


class ElevenAuthPayloadTests(unittest.TestCase):
    def test_build_auth_payload_uses_web_firebase_token(self):
        now = datetime(2026, 9, 8, 1, 0, 0, tzinfo=timezone.utc)
        payload = elevenlabs.build_auth_payload(
            email="demo@example.com",
            id_token="id-token-1",
            refresh_token="refresh-1",
            user_id="user_abc",
            workspace_id="workspace_abc",
            auth_account_id="firebaseUid",
            session_ttl_sec=3600,
            now=now,
        )
        self.assertEqual(payload["type"], "elevenlabs")
        self.assertEqual(payload["auth_kind"], "web_firebase_id_token")
        self.assertEqual(payload["access_token"], "id-token-1")
        self.assertEqual(payload["id_token"], "id-token-1")
        self.assertEqual(payload["refresh_token"], "refresh-1")
        self.assertEqual(payload["project_id"], "workspace_abc")
        self.assertEqual(payload["api_base"], elevenlabs.API_BASE)
        self.assertNotIn("xi_api_key", payload)
        self.assertTrue(payload["expired"].endswith("Z"))

    def test_build_auth_payload_requires_id_token(self):
        with self.assertRaises(ValueError):
            elevenlabs.build_auth_payload(
                email="demo@example.com",
                workspace_id="ws",
            )

    def test_write_auth_file_uses_sanitized_name(self):
        payload = elevenlabs.build_auth_payload(
            email="a+b@example.com",
            id_token="token-1",
            user_id="user-1",
        )
        with tempfile.TemporaryDirectory() as temp:
            path = elevenlabs.write_auth_file(temp, payload)
            self.assertTrue(path.endswith("elevenlabs-a-b@example.com.json"))
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(loaded["access_token"], "token-1")
            self.assertEqual(loaded["auth_kind"], "web_firebase_id_token")


class ElevenMailExtractionTests(unittest.TestCase):
    def test_extract_verification_link_action_url(self):
        body = (
            "Verify https://elevenlabs.io/app/action?mode=verifyEmail"
            "&oobCode=abc123&apiKey=AIzaSyTest&lang=en&newUser=true"
        )
        link = elevenlabs.extract_verification_link(body) or ""
        self.assertIn("mode=verifyEmail", link)
        self.assertIn("oobCode=abc123", link)

    def test_extract_otp(self):
        self.assertEqual(
            elevenlabs.extract_eleven_otp("Your verification code is 482913"),
            "482913",
        )


class ElevenConfigValidationTests(unittest.TestCase):
    def test_normalize_platform(self):
        self.assertEqual(normalize_platform("ElevenLabs"), "elevenlabs")
        self.assertEqual(normalize_platform("11labs"), "elevenlabs")
        self.assertTrue(is_local_auth_platform("elevenlabs"))

    def test_eleven_platform_skips_cpa_requirements(self):
        values = dict(
            app.DEFAULT_CONFIG,
            platform="elevenlabs",
            eleven_auth_dir="eleven_auths",
            register_count=1,
            concurrent_count=1,
            cpa_export_enabled=True,
            cpa_auth_dir="",
            cpa_base_url="",
            email_provider="duckmail",
        )
        validate_config(values)
        self.assertEqual(values["platform"], "elevenlabs")

    def test_eleven_platform_requires_auth_dir(self):
        values = dict(
            app.DEFAULT_CONFIG,
            platform="elevenlabs",
            eleven_auth_dir="",
            register_count=1,
            concurrent_count=1,
            email_provider="duckmail",
        )
        with self.assertRaises(ValueError):
            validate_config(values)


if __name__ == "__main__":
    unittest.main()
