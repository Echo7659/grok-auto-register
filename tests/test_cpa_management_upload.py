import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cpa_export


class DummyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self):
        return self.status

    def read(self, _limit=-1):
        return b'{"status":"ok"}'


class DummyOpener:
    def __init__(self):
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return DummyResponse()


class CpaManagementUploadTests(unittest.TestCase):
    def test_uploads_raw_json_without_logging_management_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "xai-user@example.com.json"
            auth_file.write_text(
                json.dumps({"type": "xai", "email": "user@example.com"}),
                encoding="utf-8",
            )
            opener = DummyOpener()
            logs = []
            cfg = {
                "cpa_management_base": "http://cpa.example.com/",
                "cpa_management_key": "management-secret",
                "cpa_management_timeout_sec": 12,
            }

            with patch.object(cpa_export.urllib.request, "build_opener", return_value=opener):
                cpa_export.upload_cpa_auth_to_management(auth_file, cfg, logs.append)

            self.assertEqual(
                opener.request.full_url,
                "http://cpa.example.com/v0/management/auth-files?name=xai-user%40example.com.json",
            )
            self.assertEqual(opener.request.data, auth_file.read_bytes())
            self.assertEqual(
                opener.request.get_header("X-management-key"),
                "management-secret",
            )
            self.assertEqual(opener.timeout, 12)
            self.assertNotIn("management-secret", "\n".join(logs))

    def test_rejects_invalid_json_before_network_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "broken.json"
            auth_file.write_text("{", encoding="utf-8")
            cfg = {
                "cpa_management_base": "http://cpa.example.com",
                "cpa_management_key": "management-secret",
            }

            with patch.object(cpa_export.urllib.request, "build_opener") as build_opener:
                with self.assertRaisesRegex(RuntimeError, "不是有效 JSON"):
                    cpa_export.upload_cpa_auth_to_management(auth_file, cfg)

            build_opener.assert_not_called()

    def test_export_marks_successful_management_upload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_file = Path(temp_dir) / "xai-user@example.com.json"
            auth_file.write_text(
                json.dumps({"type": "xai", "email": "user@example.com"}),
                encoding="utf-8",
            )
            cfg = {
                "cpa_export_enabled": True,
                "cpa_auth_dir": temp_dir,
                "cpa_management_auto_upload": True,
                "cpa_management_upload_required": True,
                "cpa_management_base": "http://cpa.example.com",
                "cpa_management_key": "management-secret",
            }
            minted = {"ok": True, "path": str(auth_file)}

            with patch("cpa_xai.mint_and_export", return_value=minted), patch.object(
                cpa_export,
                "upload_cpa_auth_to_management",
            ) as upload:
                result = cpa_export.export_cpa_xai_for_account(
                    "user@example.com",
                    "password",
                    config=cfg,
                )

            upload.assert_called_once()
            self.assertTrue(result["ok"])
            self.assertTrue(result["cpa_management_uploaded"])


if __name__ == "__main__":
    unittest.main()
