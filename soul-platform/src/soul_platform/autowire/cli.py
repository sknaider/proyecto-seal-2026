from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from soul_platform.autowire.manager import AutoWireManager
from soul_platform.autowire.service import install_autowire_autostart
from soul_platform.bootstrap import default_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soul-autowire")
    parser.add_argument("--root", type=Path, default=default_root())
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("reconcile")
    actions.add_parser("status")
    install = actions.add_parser("install-autostart")
    install.add_argument("--interval", type=float, default=30.0)
    watch = actions.add_parser("watch")
    watch.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args(argv)
    manager = AutoWireManager(args.root)
    if args.action == "reconcile":
        payload = manager.reconcile()
    elif args.action == "status":
        payload = manager.status()
    elif args.action == "install-autostart":
        target = install_autowire_autostart(
            root=args.root, interval=args.interval
        )
        payload = {"installed": True, "target": str(target)}
    else:
        if not 5 <= args.interval <= 3600:
            parser.error("watch interval must be between 5 and 3600 seconds")
        while True:
            try:
                manager.reconcile()
            except Exception:
                # Fail closed: discovery failures never alter the active brain.
                pass
            time.sleep(args.interval)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
