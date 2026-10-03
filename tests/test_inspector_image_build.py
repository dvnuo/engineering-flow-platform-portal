"""The image bundles the Appium Inspector web build with npm."""
from __future__ import annotations

from pathlib import Path

DOCKERFILE = Path("Dockerfile")


def test_the_image_fetches_the_inspector_with_npm():
    text = DOCKERFILE.read_text(encoding="utf-8")
    # A node stage packs the npm package, so registry mirrors, proxies, and an
    # .npmrc apply as they do for any npm install; the Python image copies the
    # web build out of it. An empty version opts out; anything else fails.
    assert "FROM node:" in text and " AS inspector" in text
    assert 'npm pack "appium-inspector-plugin@$APPIUM_INSPECTOR_VERSION"' in text
    assert 'export npm_config_registry="$NPM_REGISTRY"' in text
    assert "--mount=type=secret,id=npmrc,target=/root/.npmrc" in text
    assert "test -f dist/index.html" in text
    assert "COPY --from=inspector /inspector/dist /opt/appium-inspector" in text
    assert "ENV EFP_APPIUM_INSPECTOR_DIR=/opt/appium-inspector" in text
    assert "|| echo" not in text
