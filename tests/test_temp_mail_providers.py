import unittest
from unittest.mock import Mock

import temp_mail_providers as tmp
from gui_settings import validate_config
import grok_register_ttk as app


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else ("" if payload is None else str(payload))

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class MailtmProviderTests(unittest.TestCase):
    def test_create_email_and_token(self):
        calls = {"get": 0, "post": 0}

        def http_get(url, **kwargs):
            calls["get"] += 1
            self.assertIn("/domains", url)
            return _Resp(200, [{"domain": "example.com", "isActive": True}])

        def http_post(url, **kwargs):
            calls["post"] += 1
            if url.endswith("/accounts"):
                return _Resp(201, {"address": kwargs["json"]["address"]})
            if url.endswith("/token"):
                return _Resp(200, {"token": "jwt-token"})
            raise AssertionError(url)

        email, token = tmp.mailtm_create_email_and_token(http_get, http_post)
        self.assertTrue(email.endswith("@example.com"))
        self.assertEqual(token, "jwt-token")
        self.assertEqual(calls["get"], 1)
        self.assertEqual(calls["post"], 2)

    def test_poll_code_from_message(self):
        detail = {
            "subject": "Your code",
            "text": "verification code: 654321",
            "html": [],
        }
        messages = [{"id": "m1", "to": [{"address": "a@example.com"}], "subject": "Your code"}]

        def http_get(url, **kwargs):
            if url.endswith("/messages"):
                return _Resp(200, messages)
            if url.endswith("/messages/m1"):
                return _Resp(200, detail)
            raise AssertionError(url)

        sleeps = []

        code = tmp.mailtm_get_oai_code(
            http_get=http_get,
            token="t",
            email="a@example.com",
            extract_code=lambda text, subject="": "654321" if "654321" in text else None,
            sleep_with_cancel=lambda s, c: sleeps.append(s),
            raise_if_cancelled=lambda c: None,
            timeout=5,
            poll_interval=1,
        )
        self.assertEqual(code, "654321")


class OneSecMailProviderTests(unittest.TestCase):
    def test_create_and_token_roundtrip(self):
        def http_get(url, **kwargs):
            params = kwargs.get("params") or {}
            self.assertEqual(params.get("action"), "genRandomMailbox")
            return _Resp(200, ["abc123@1secmail.com"])

        email, token = tmp.onesecmail_create_email_and_token(http_get)
        self.assertEqual(email, "abc123@1secmail.com")
        self.assertEqual(token, "1secmail:abc123@1secmail.com")
        login, domain = tmp.onesecmail_decode_token(token, email=email)
        self.assertEqual((login, domain), ("abc123", "1secmail.com"))

    def test_403_gives_actionable_error(self):
        def http_get(url, **kwargs):
            return _Resp(403, text="Forbidden")

        with self.assertRaisesRegex(Exception, "403"):
            tmp.onesecmail_create_email_and_token(http_get)

    def test_poll_code(self):
        def http_get(url, **kwargs):
            params = kwargs.get("params") or {}
            if params.get("action") == "getMessages":
                return _Resp(200, [{"id": 9, "subject": "OTP"}])
            if params.get("action") == "readMessage":
                return _Resp(200, {"subject": "OTP", "textBody": "code 112233"})
            raise AssertionError(params)

        code = tmp.onesecmail_get_oai_code(
            http_get=http_get,
            token="1secmail:u@1secmail.com",
            email="u@1secmail.com",
            extract_code=lambda text, subject="": "112233" if "112233" in text else None,
            sleep_with_cancel=lambda s, c: None,
            raise_if_cancelled=lambda c: None,
            timeout=5,
            poll_interval=1,
        )
        self.assertEqual(code, "112233")


class ConfigValidationTests(unittest.TestCase):
    def test_mailtm_and_onesecmail_validate(self):
        base = dict(app.DEFAULT_CONFIG, register_count=1, concurrent_count=1, platform="fishaudio", fish_auth_dir="fish_auths")
        values = dict(base, email_provider="mailtm", mailtm_api_base="https://api.mail.tm")
        validate_config(values)
        self.assertEqual(values["email_provider"], "mailtm")

        values = dict(base, email_provider="1secmail", onesecmail_api_base="https://www.1secmail.com/api/v1")
        validate_config(values)
        self.assertEqual(values["email_provider"], "onesecmail")
        self.assertTrue(values["onesecmail_api_base"].endswith("/"))


if __name__ == "__main__":
    unittest.main()
