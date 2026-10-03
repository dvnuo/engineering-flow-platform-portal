"""The image bundles the Appium Inspector web build with curl."""
from __future__ import annotations

from pathlib import Path

DOCKERFILE = Path("Dockerfile")


def test_the_image_fetches_the_inspector_with_curl_alone():
    text = DOCKERFILE.read_text(encoding="utf-8")
    # A curl stage downloads the npm package tarball (from a registry mirror
    # or any URL, through the proxy build arguments, with an optional .netrc
    # and CA certificate) and the Python image copies the web build out of
    # it. An empty version opts out; anything else fails the build.
    assert "FROM curlimages/curl:" in text and " AS inspector" in text
    assert "npm pack " not in text and "npm install" not in text and "FROM node:" not in text
    assert "/appium-inspector-plugin/-/appium-inspector-plugin-$APPIUM_INSPECTOR_VERSION.tgz" in text
    assert 'url="${APPIUM_INSPECTOR_URL:-${NPM_REGISTRY%/}/' in text
    assert "curl -fsSL --netrc-optional --retry 3 $ca -o package.tgz" in text
    assert "--mount=type=secret,id=netrc,target=/root/.netrc" in text
    assert "--mount=type=secret,id=cacert,target=/inspector/cacert.pem" in text
    assert "test -f dist/index.html" in text
    assert "COPY --from=inspector /inspector/dist /opt/appium-inspector" in text
    assert "ENV EFP_APPIUM_INSPECTOR_DIR=/opt/appium-inspector" in text
    assert "|| echo" not in text
