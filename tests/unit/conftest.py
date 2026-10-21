"""Unit tests run in a clean configuration: no MTP_* variables and no `.env` from the developer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.startswith("MTP_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
