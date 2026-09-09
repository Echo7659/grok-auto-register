import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import proxy_bridge


class NormalizeProxyTests(unittest.TestCase):
    def test_host_port_user_pass(self):
        raw = "dc.decodo.com:10001:user-sp31umh5d4-country-us-city-los_angeles:Y7rCePqf~b9f5f8Npm"
        got = proxy_bridge.normalize_proxy(raw)
        parsed = urlparse(got)
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.hostname, "dc.decodo.com")
        self.assertEqual(parsed.port, 10001)
        self.assertEqual(parsed.username, "user-sp31umh5d4-country-us-city-los_angeles")
        self.assertEqual(parsed.password, "Y7rCePqf~b9f5f8Npm")

    def test_url_with_auth(self):
        raw = "http://alice:s3cret@127.0.0.1:7890"
        self.assertEqual(proxy_bridge.normalize_proxy(raw), raw)

    def test_host_port_only(self):
        self.assertEqual(proxy_bridge.normalize_proxy("127.0.0.1:7897"), "http://127.0.0.1:7897")

    def test_empty(self):
        self.assertEqual(proxy_bridge.normalize_proxy(""), "")
        self.assertEqual(proxy_bridge.normalize_proxy(None), "")


class ChromiumBridgeTests(unittest.TestCase):
    def test_no_auth_passthrough(self):
        self.assertEqual(
            proxy_bridge.proxy_for_chromium("http://127.0.0.1:7897"),
            "http://127.0.0.1:7897",
        )

    def test_auth_uses_local_bridge(self):
        raw = "dc.example.com:10001:user1:pass1"
        local = proxy_bridge.proxy_for_chromium(raw)
        parsed = urlparse(local)
        self.assertEqual(parsed.hostname, "127.0.0.1")
        self.assertTrue(parsed.port and parsed.port > 0)
        # Reuse same bridge
        again = proxy_bridge.proxy_for_chromium(raw)
        self.assertEqual(local, again)


class ProxyPoolTests(unittest.TestCase):
    def tearDown(self):
        proxy_bridge.clear_thread_proxy()

    def test_parse_pool_text_skips_comments_and_dupes(self):
        text = """
        # comment
        1.1.1.1:8000:u1:p1
        http://u2:p2@2.2.2.2:9000
        1.1.1.1:8000:u1:p1
        """
        items = proxy_bridge.parse_proxy_pool_text(text)
        self.assertEqual(len(items), 2)

    def test_load_and_pick_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.txt"
            path.write_text(
                "a.example.com:10001:userA:passA\n"
                "b.example.com:10002:userB:passB\n",
                encoding="utf-8",
            )
            items = proxy_bridge.load_proxy_pool(str(path), force=True)
            self.assertEqual(len(items), 2)
            chosen = proxy_bridge.pick_pool_proxy(str(path))
            self.assertIn(chosen, items)

    def test_resolve_fixed_and_pool_modes(self):
        fixed_cfg = {"proxy_mode": "fixed", "proxy": "127.0.0.1:7890"}
        self.assertEqual(
            proxy_bridge.resolve_proxy_from_config(fixed_cfg, assign=True),
            "http://127.0.0.1:7890",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pool.txt"
            path.write_text("pool.example.com:10001:u:p\n", encoding="utf-8")
            pool_cfg = {"proxy_mode": "pool", "proxy_pool_file": str(path)}
            with patch("proxy_bridge.random.choice", side_effect=lambda items: items[0]):
                got = proxy_bridge.resolve_proxy_from_config(pool_cfg, assign=True)
            self.assertIn("pool.example.com", got)
            self.assertEqual(proxy_bridge.get_thread_proxy(), got)


if __name__ == "__main__":
    unittest.main()
