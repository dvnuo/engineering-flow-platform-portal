"""Bundling the Appium Inspector web build: the image with npm, a local Portal
with the script."""
from __future__ import annotations

import importlib.util
import io
import tarfile
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path("scripts/fetch_appium_inspector.py")
DOCKERFILE = Path("Dockerfile")


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


def test_a_download_failure_is_one_line(monkeypatch, capsys, tmp_path):
    module = _module()

    def refuse(url, timeout):
        raise urllib.error.URLError("Tunnel connection failed: 503 Service Unavailable")

    monkeypatch.setattr(module.urllib.request, "urlopen", refuse)
    assert module.main(["fetch", "2026.9.2", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert err == "Could not download https://registry.npmjs.org/appium-inspector-plugin/-/appium-inspector-plugin-2026.9.2.tgz: Tunnel connection failed: 503 Service Unavailable\n"
    assert not (tmp_path / "out").exists()


def test_the_image_fetches_the_inspector_with_npm():
    text = DOCKERFILE.read_text(encoding="utf-8")
    # A node stage packs the npm package, so registry mirrors, proxies, and an
    # .npmrc apply as they do for any npm install; the Python image copies the
    # web build out of it. An empty version opts out; anything else fails.
    assert "FROM node:" in text and " AS inspector" in text
    assert 'npm pack "appium-inspector-plugin@$APPIUM_INSPECTOR_VERSION"' in text
    assert 'export npm_config_registry="$NPM_REGISTRY"' in text
    assert "--mount=type=secret,id=npmrc,target=/root/.npmrc" in text
    assert "COPY --from=inspector /inspector/dist /opt/appium-inspector" in text
    assert "ENV EFP_APPIUM_INSPECTOR_DIR=/opt/appium-inspector" in text
    assert "fetch_appium_inspector" not in text and "not bundled" not in text
