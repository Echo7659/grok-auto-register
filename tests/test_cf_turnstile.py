import unittest

import cf_turnstile as cf


class ClassifyCloudflareTests(unittest.TestCase):
    def test_embedded_widget_copy_is_challenge(self):
        self.assertEqual(
            cf.classify_cloudflare_text("请确认您是真人 Cloudflare Ray ID: 123"),
            "challenge",
        )
        self.assertEqual(
            cf.classify_cloudflare_text("Verify you are human"),
            "challenge",
        )
        self.assertEqual(
            cf.classify_cloudflare_text("Checking your browser before accessing accounts.x.ai"),
            "challenge",
        )

    def test_ray_id_alone_is_not_hard_block(self):
        self.assertEqual(
            cf.classify_cloudflare_text("Cloudflare Ray ID: abcdef Performance & security by Cloudflare"),
            "none",
        )

    def test_hard_block_codes(self):
        self.assertEqual(
            cf.classify_cloudflare_text("Sorry, you have been blocked Error code 1020"),
            "hard-block",
        )
        self.assertEqual(
            cf.classify_cloudflare_text("故障排除 cf-error-details"),
            "hard-block",
        )

    def test_blocked_wins_over_generic_challenge_copy(self):
        self.assertEqual(
            cf.classify_cloudflare_text("Verify you are human. Sorry, you have been blocked. Error code 1020"),
            "hard-block",
        )


class CookieFilterTests(unittest.TestCase):
    def test_keeps_cloudflare_cookies_only(self):
        cookies = [
            {"name": "cf_clearance", "value": "a", "domain": "accounts.x.ai", "path": "/"},
            {"name": "__cf_bm", "value": "b", "domain": ".x.ai", "path": "/"},
            {"name": "sso", "value": "secret", "domain": ".x.ai", "path": "/"},
            {"name": "sso-rw", "value": "secret2", "domain": "grok.com", "path": "/"},
            {"name": "session", "value": "nope", "domain": "accounts.x.ai", "path": "/"},
        ]
        kept = cf.filter_cf_cookies(cookies)
        names = [item["name"] for item in kept]
        self.assertEqual(names, ["cf_clearance", "__cf_bm"])
        self.assertTrue(all(not n.startswith("sso") for n in names))

    def test_cookie_name_helpers(self):
        self.assertTrue(cf.is_cf_cookie_name("cf_clearance"))
        self.assertTrue(cf.is_cf_cookie_name("__cf_bm"))
        self.assertTrue(cf.is_cf_cookie_name("cf_chl_rc_i"))
        self.assertFalse(cf.is_cf_cookie_name("sso"))
        self.assertFalse(cf.is_cf_cookie_name(""))


class ClickGeometryTests(unittest.TestCase):
    def test_checkbox_offset_is_left_of_widget(self):
        points = cf.turnstile_click_offsets(300, 65)
        self.assertGreaterEqual(len(points), 2)
        first_x, first_y = points[0]
        self.assertLess(first_x, 40)
        self.assertGreater(first_x, 10)
        self.assertAlmostEqual(first_y, 32.5)

    def test_viewport_to_screen_adds_window_chrome(self):
        loc = {
            "screenX": 100,
            "screenY": 80,
            "outerWidth": 1280,
            "outerHeight": 900,
            "innerWidth": 1280,
            "innerHeight": 820,
        }
        sx, sy = cf.viewport_to_screen(loc, 26, 32)
        self.assertEqual(sx, 126)
        self.assertEqual(sy, 192)


class TokenReadyTests(unittest.TestCase):
    def test_token_length_and_passed_sentinel(self):
        self.assertFalse(cf.is_turnstile_token_ready(""))
        self.assertFalse(cf.is_turnstile_token_ready("short"))
        self.assertTrue(cf.is_turnstile_token_ready("passed"))
        self.assertTrue(cf.is_turnstile_token_ready("x" * 80))


if __name__ == "__main__":
    unittest.main()
