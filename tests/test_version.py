from __future__ import annotations

import importlib
import importlib.metadata

import pytest

import moxtral


def test_version_is_a_nonempty_version_string() -> None:
    assert moxtral.__version__
    assert moxtral.__version__[0].isdigit()


def test_version_falls_back_when_distribution_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        module = importlib.reload(moxtral)
        assert module.__version__ == "0.0.0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(moxtral)
