---
icon: laptop
---
## What this is for

The EFP local bridge is one program on your PC, `efp-bridge`, with two uses:

- **Browser automation**: the assistant drives a separate Chrome window that
  belongs to EFP. It uses that window's logins, so internal sites work
  without sharing credentials. Nothing runs unless a chat is open in this
  browser tab and the Browser toggle is on.
- **Mobile recording**: the Mobile testing panel in an assistant's chat starts a
  BrowserStack device from this computer and records what you do in Appium
  Inspector. The bridge runs `mobile-auto` from its own folder; see
  *Mobile scenario testing*.
