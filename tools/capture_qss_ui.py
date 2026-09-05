"""Capture QSS response bodies, rendered forms, DOM, and screenshots using optional Playwright."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
from pathlib import Path

from qsw_l2110.client import QswL2110Client
from tools.capture_support import private_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default=os.environ.get("QSW_HOST"), required=not os.environ.get("QSW_HOST")
    )
    parser.add_argument("--username", default=os.environ.get("QSW_USER", "admin"))
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument(
        "--page", action="append", help="reviewed QSS HTML page; default all reviewed pages"
    )
    parser.add_argument("--output", type=Path, required=True, help="new private directory")
    parser.add_argument("--node", default="node")
    parser.add_argument(
        "--playwright-module", default="playwright", help="Node module name or absolute path"
    )
    parser.add_argument("--browser-executable", help="optional existing Chromium/Chrome executable")
    args = parser.parse_args(argv)
    try:
        private_directory(args.output)
    except OSError as exc:
        parser.error(str(exc))
    password = os.environ.get("QSW_PASSWORD")
    if password is None:
        password = getpass.getpass("Switch password: ")
    with QswL2110Client(args.host, verify=not args.insecure) as client:
        client.authenticate(args.username, password)
        # Session goes to the browser over stdin; never argv, logs, or an HAR.
        cookies = [
            {"name": c.name, "value": c.value, "url": args.host} for c in client._client.cookies.jar
        ]
        config = {
            "host": args.host,
            "cookies": cookies,
            "insecure": args.insecure,
            "pages": args.page,
            "output": str(args.output.resolve()),
            "playwrightModule": args.playwright_module,
            "browserExecutable": args.browser_executable,
        }
        env = {key: value for key, value in os.environ.items() if key != "QSW_PASSWORD"}
        result = subprocess.run(
            [args.node, str(Path(__file__).with_suffix(".cjs"))],
            input=json.dumps(config).encode(),
            env=env,
            check=False,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
