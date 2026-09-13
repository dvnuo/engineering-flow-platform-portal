# Downloads served by Portal

CI places `efp-browser-bridge.zip` here (built from `engineering-flow-platform-tools`
`scripts/browser-bridge/`: the Windows package is `browser.exe`, `install-bridge.cmd`,
`README.md`; macOS and Linux packages carry `browser` and `install-bridge.sh` instead).
The Connectors → Local browser panel links to it when `LOCAL_BROWSER_CLI_DOWNLOAD_URL`
is not set; point that variable at a release page when more than one platform package
is offered.

Do not commit the binary; this directory only carries this note.
