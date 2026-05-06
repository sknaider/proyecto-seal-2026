"""Pytest configuration: load SEAL credentials into env before tests run."""
import os


def pytest_configure(config):
    creds = os.path.expanduser("~/.config/seal/credentials.env")
    if os.path.exists(creds):
        with open(creds) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())
