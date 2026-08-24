from __future__ import annotations

import re

import fusion_core


def test_version_is_string():
    assert isinstance(fusion_core.__version__, str)


def test_version_single_source_from_metadata():
    from importlib.metadata import PackageNotFoundError, version

    try:
        dist_version = version("fusion-core")
    except PackageNotFoundError:
        return
    assert fusion_core.__version__ == dist_version, (
        "__version__ must derive from package metadata, not a hardcoded literal (I9)"
    )


def test_version_not_unknown_when_installed():
    from importlib.metadata import PackageNotFoundError, version

    try:
        version("fusion-core")
    except PackageNotFoundError:
        return
    assert fusion_core.__version__ != "0.0.0+unknown", (
        "installed package must resolve a real version, not the fallback sentinel"
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", fusion_core.__version__), "resolved version must look like a semver"
