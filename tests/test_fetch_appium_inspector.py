"""The image build step that bundles the Appium Inspector web build."""
from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path("scripts/fetch_appium_inspector.py")


def _module():
    spec = importlib.util.spec_from_file_location("fetch_appium_inspector", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_extracts_only_dist_browser_files(tmp_path):
    archive = _archive({
        "package/package.json": b"{}",
        "package/dist-browser/index.html": b"<html></html>",
        "package/dist-browser/assets/index-1.js": b"x",
        "package/dist-browser/../../escape.txt": b"no",
        "package/dist-browser//etc/passwd": b"no",
    })
    count = _module().extract_dist_browser(archive, tmp_path / "out")
    assert count == 2
    assert (tmp_path / "out" / "index.html").read_bytes() == b"<html></html>"
    assert (tmp_path / "out" / "assets" / "index-1.js").exists()
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "out" / "package.json").exists()


def test_a_package_without_the_web_build_fails(tmp_path):
    with pytest.raises(RuntimeError, match="index.html"):
        _module().extract_dist_browser(_archive({"package/index.mjs": b""}), tmp_path / "out")
