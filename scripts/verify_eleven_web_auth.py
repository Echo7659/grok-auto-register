#!/usr/bin/env python3
"""Verify newest ElevenLabs web auth JSON: refresh, user, optional TTS probe."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from platforms import elevenlabs  # noqa: E402
import proxy_bridge  # noqa: E402


def newest_auths(auth_dir: Path, limit: int) -> list[Path]:
    files = sorted(auth_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth-dir", default="eleven_auths")
    ap.add_argument("--limit", type=int, default=2)
    ap.add_argument("--proxy", default="")
    ap.add_argument(
        "--min-mtime",
        type=float,
        default=0.0,
        help="Only files newer than this unix mtime",
    )
    args = ap.parse_args()

    auth_dir = Path(args.auth_dir)
    if not auth_dir.is_absolute():
        auth_dir = ROOT / auth_dir
    candidates = newest_auths(auth_dir, max(args.limit * 3, args.limit))
    files = [p for p in candidates if p.stat().st_mtime >= args.min_mtime][: args.limit]
    if not files:
        print("NO_FILES")
        return 2

    proxies = {}
    if args.proxy.strip():
        proxy = proxy_bridge.normalize_proxy(args.proxy.strip())
        proxies = {"http": proxy, "https": proxy}

    rc = 0
    for path in files:
        payload = json.loads(path.read_text())
        email = payload.get("email")
        print(f"\n== {path.name} ==")
        print(f"email={email}")
        print(f"is_onboarding_completed={payload.get('is_onboarding_completed')}")
        print(f"has_request_headers={'request_headers' in payload}")
        try:
            refreshed = elevenlabs.refresh_id_token(
                payload.get("refresh_token", ""),
                proxies=proxies or None,
            )
            id_token = refreshed["id_token"]
            print("refresh=ok")
            user = elevenlabs.fetch_user_info(id_token, proxies=proxies or None)
            sub = user.get("subscription") if isinstance(user.get("subscription"), dict) else {}
            print(
                "user="
                f"onboarding={user.get('is_onboarding_completed')} "
                f"first_name={user.get('first_name')!r} "
                f"subscription={sub}"
            )
            probe = elevenlabs.probe_web_tts(id_token, proxies=proxies or None)
            print(
                "tts="
                f"ok={probe.get('ok')} status_code={probe.get('status_code')} "
                f"status={probe.get('status')!r} detail={probe.get('detail')!r} "
                f"bytes={probe.get('bytes')}"
            )
            if not probe.get("ok"):
                rc = 1
        except Exception as exc:
            print(f"ERROR: {exc}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
