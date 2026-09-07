#!/usr/bin/env python3
"""Static safety contracts for NEXUS launch ownership."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NexusLauncherContractTest(unittest.TestCase):
    def test_headless_launcher_is_detached_and_single_owner(self):
        source = (ROOT / "nexus.sh").read_text(encoding="utf-8")
        self.assertIn('has-session -t "seal-nexus"', source)
        self.assertIn('new-session -d -s "seal-nexus"', source)
        self.assertIn("/tmp/seal_nexus_runtime.lock", source)
        self.assertNotIn('kill-session -t "seal-nexus"', source)
        self.assertNotIn("rm -f /tmp/seal_monitor_connect_NEXUS.lock", source)
        self.assertNotIn("[AUTO-BOOT NEXUS", source)

    def test_visible_launcher_is_single_owner(self):
        source = (ROOT / "nexus_fresh.sh").read_text(encoding="utf-8")
        self.assertIn('has-session -t "seal-nexus"', source)
        self.assertIn('new-session -d -s "seal-nexus"', source)
        self.assertIn("/tmp/seal_nexus_runtime.lock", source)
        self.assertNotIn('kill-session -t "seal-nexus"', source)
        self.assertNotIn("[AUTO-BOOT NEXUS", source)


if __name__ == "__main__":
    unittest.main()
