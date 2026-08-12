#!/usr/bin/env python3
"""Source-checkout launcher; installed users run the ``soul-tray`` command."""

from soul_platform.tray import main

if __name__ == "__main__":
    raise SystemExit(main())
