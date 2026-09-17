# Downloads served by Portal

This directory contains documentation only. Bridge packages must be supplied by the deployment operator; the current Portal Docker build workflow does not download them automatically. A default link can therefore return 404 until packages are provided.

The Connectors → Local browser panel links to one bridge package per system
from this directory when `LOCAL_BROWSER_CLI_DOWNLOAD_URL` is not set:

    efp-browser-bridge-windows-amd64.zip   browser.exe, install-bridge.cmd, README.md
    efp-browser-bridge-windows-arm64.zip
    efp-browser-bridge-darwin-arm64.zip    browser, install-bridge.sh, README.md
    efp-browser-bridge-darwin-amd64.zip
    efp-browser-bridge-linux-amd64.zip
    efp-browser-bridge-linux-arm64.zip

The companion `engineering-flow-platform-tools` repository builds these packages with `scripts/browser-bridge/package.sh`. Use a tested release containing the matching binary, installer, and package README. Copy the packages here before building the Portal image, mount them into this served directory, or point
`LOCAL_BROWSER_CLI_DOWNLOAD_URL` at the artifact store with `{platform}` in
the URL (for example
`https://github.com/<org>/engineering-flow-platform-tools/releases/download/v0.1.0/efp-browser-bridge-{platform}.zip`).

Do not commit the binaries; this directory only carries this note.

For a git-clone deployment that mounts the cloned `app/` over `/app/app`, archives included only in the image may be hidden by the mount. An explicit artifact-store URL avoids that mismatch. If the URL has no `{platform}` placeholder, every system receives the same file, so use that form only for a deliberately platform-specific deployment.

After publishing, open **Connectors → Local browser**, select each supported system, and check that its download returns a ZIP containing the expected files. Set `LOCAL_BROWSER_CLI_VERSION` to the version you actually published. The application does not verify that this label matches archive contents.

See the [Beginner Guide](../../../docs/BEGINNER_GUIDE.md) for member setup, and the [Connectors Contract](../../../docs/CONNECTORS_CONTRACT.md) for the transport and supported platform identifiers.
