"""Pytest defaults: allow Settings() to load without a production SECRET_KEY."""

from __future__ import annotations

import os

os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")
