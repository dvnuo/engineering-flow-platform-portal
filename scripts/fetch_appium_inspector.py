"""Download the Appium Inspector web build into a directory.

Portal serves it at /inspector/ for recording mobile test steps (see
app/api/mobile.py). The build is the dist-browser folder of the
appium-inspector-plugin npm package, which hard-codes the /inspector/ prefix.

    python scripts/fetch_appium_inspector.py 2026.9.2 /opt/appium-inspector [registry-url]
"""
from __future__ import annotations

import io
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

PREFIX = "package/dist-browser/"


def extract_dist_browser(archive: bytes, dest: Path) -> int:
    """Write the build's files under dest; returns how many were written.

    Only regular files inside dist-browser are taken, and none may leave dest.
    """
    written = 0
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile() or not member.name.startswith(PREFIX):
                continue
            relative = PurePosixPath(member.name[len(PREFIX):])
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                continue
            target = (root / Path(*relative.parts)).resolve()
            if root not in target.parents:
                continue
            source = tf.extractfile(member)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            written += 1
    if not (root / "index.html").is_file():
        raise RuntimeError("the package has no dist-browser/index.html")
    return written


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    version, dest = argv[1], Path(argv[2])
    registry = (argv[3] if len(argv) > 3 else "https://registry.npmjs.org").rstrip("/")
    url = f"{registry}/appium-inspector-plugin/-/appium-inspector-plugin-{version}.tgz"
    with urllib.request.urlopen(url, timeout=180) as response:
        archive = response.read()
    count = extract_dist_browser(archive, dest)
    print(f"Appium Inspector {version}: {count} files in {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
