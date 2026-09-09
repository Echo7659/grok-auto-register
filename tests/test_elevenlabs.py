import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

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
            is_onboarding_completed=True,
            first_name="Alex",
            now=now,
        )
        self.assertEqual(payload["type"], "elevenlabs")
        self.assertEqual(payload["auth_kind"], "web_firebase_id_token")
        self.assertEqual(payload["access_token"], "id-token-1")
        self.assertEqual(payload["id_token"], "id-token-1")
        self.assertEqual(payload["refresh_token"], "refresh-1")
        self.assertEqual(payload["project_id"], "workspace_abc")
        self.assertEqual(payload["api_base"], elevenlabs.API_BASE)
        self.assertTrue(payload["is_onboarding_completed"])
        self.assertEqual(payload["first_name"], "Alex")
        self.assertEqual(payload["request_headers"]["x-generation-surface"], "Speech Synthesis")
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


class ElevenOnboardingHelpersTests(unittest.TestCase):
    def test_detect_hcaptcha_challenge_reads_visible_flag(self):
        class FakePage:
            def run_js(self, script):
                return {
                    "visible": True,
                    "checkbox": False,
                    "challenge_frames": 1,
                    "prompt": True,
                    "title": "含有飞机的图片",
                }

        state = elevenlabs.detect_hcaptcha_challenge(FakePage())
        self.assertTrue(state["visible"])
        self.assertTrue(state["prompt"])

    def test_submit_signup_raises_when_visible_hcaptcha(self):
        class FakePage:
            def __init__(self):
                self.calls = 0

            def run_js(self, script, *args):
                text = str(script or "")
                if "hcaptcha" in text.lower() or "challengeFrames" in text or "challenge_frames" in text:
                    return {"visible": True, "prompt": True, "challenge_frames": 1, "checkbox": False, "title": "x"}
                if "detect_step" in text or "verification link" in text or "sign-up" in text:
                    return "signup"
                # First form fill reports submitted; next loop hits captcha.
                self.calls += 1
                if "sign\\s*up" in text or "Sign up" in text or "submitted" in text or "terms" in text:
                    return {"state": "submitted"}
                return {"state": "submitted"}

        # Force detect_step via URL-less page: patch helpers.
        with mock.patch.object(elevenlabs, "detect_step", return_value="signup"), mock.patch.object(
            elevenlabs, "detect_hcaptcha_challenge", side_effect=[{"visible": False}, {"visible": True}]
        ):
            page = FakePage()
            with self.assertRaises(elevenlabs.ElevenHCaptchaBlocked):
                elevenlabs.submit_signup_form(page, "a@b.com", "Nabcd1234!a7#zzzz", timeout=5)

    def test_web_api_headers_use_bearer_not_xi_api_key(self):
        headers = elevenlabs._web_api_headers("tok-1")
        self.assertEqual(headers["Authorization"], "Bearer tok-1")
        self.assertNotIn("xi-api-key", {k.lower() for k in headers})
        self.assertEqual(headers["x-generation-actor"], "User")

    def test_complete_onboarding_posts_survey_body(self):
        calls = []

        class FakeResp:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.text = json.dumps(payload)
                self.content = self.text.encode()

            def json(self):
                return self._payload

        def fake_get(url, **kwargs):
            calls.append(("GET", url, kwargs.get("json")))
            if len([c for c in calls if c[0] == "GET"]) == 1:
                return FakeResp(200, {"is_onboarding_completed": False})
            return FakeResp(200, {"is_onboarding_completed": True, "first_name": "Alex", "user_id": "u1"})

        def fake_post(url, **kwargs):
            calls.append(("POST", url, kwargs.get("json")))
            return FakeResp(200, {"status": "ok"})

        with mock.patch("curl_cffi.requests.get", side_effect=fake_get), mock.patch(
            "curl_cffi.requests.post", side_effect=fake_post
        ):
            result = elevenlabs.complete_onboarding("id-token", first_name="alex")

        self.assertTrue(result["ok"])
        self.assertFalse(result["skipped"])
        post_calls = [c for c in calls if c[0] == "POST"]
        self.assertEqual(len(post_calls), 1)
        self.assertIn("/v1/user/onboarding-survey-complete", post_calls[0][1])
        body = post_calls[0][2]
        self.assertEqual(body["first_name"], "Alex")
        self.assertEqual(body["platform"], "creative_ui")
        self.assertEqual(body["role"], "personal_use")
        self.assertEqual(body["usecases"], ["text-to-speech"])


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
