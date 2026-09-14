# Downloads served by Portal

The Connectors → Local browser panel links to one bridge package per system
from this directory when `LOCAL_BROWSER_CLI_DOWNLOAD_URL` is not set:

    efp-browser-bridge-windows-amd64.zip   browser.exe, install-bridge.cmd, README.md
    efp-browser-bridge-windows-arm64.zip
    efp-browser-bridge-darwin-arm64.zip    browser, install-bridge.sh, README.md
    efp-browser-bridge-darwin-amd64.zip
    efp-browser-bridge-linux-amd64.zip
    efp-browser-bridge-linux-arm64.zip

They are built by `scripts/browser-bridge/package.sh` in
`engineering-flow-platform-tools` (the release workflow attaches them to the
tools release) and contain only the binary, the installer for that system,
and a short README. Copy them here for the Portal image, or point
`LOCAL_BROWSER_CLI_DOWNLOAD_URL` at the artifact store with `{platform}` in
the URL (for example
`https://github.com/<org>/engineering-flow-platform-tools/releases/download/v0.1.0/efp-browser-bridge-{platform}.zip`).

Do not commit the binaries; this directory only carries this note.
