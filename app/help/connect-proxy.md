---
icon: shield
---
## When you need this

Only if your network requires a proxy to reach the internet. If assistants work
without it, leave it off.

Your administrator will tell you the URL if it is needed. Most members never
touch this page: the Proxy connector follows the administrator's Default
connectors unless you customize it.

## Several proxies

A network sometimes offers more than one way out: one proxy for the internet,
another for a partner network, none at all for a database the assistant can
reach directly. The Proxy connector lists every proxy by name, and a table
says which one each connector goes through.

- **Proxies**: one card per proxy. The **name** is what the dropdowns show
  (letters, digits, `-` and `_`); the **URL** is the proxy as your network
  team wrote it, `http://proxy.example.com:3128` or `https://...`; the
  **login** is optional; **No proxy** lists the hosts this proxy must not be
  used for, comma separated, like `localhost,.svc.cluster.local`.
- **Default proxy**: the proxy the assistant's environment gets. Tools that
  only read the environment (the AWS CLI, `kubectl`, `gh`, `git`) always use
  it, and so does every connector left on *Default*.
- **Which proxy each connector uses**: pick a proxy or *None* for each
  connector. *None* means the assistant connects to that service directly,
  whatever the environment says. AWS and GitHub show *Default* only: their
  tools cannot be given a proxy of their own yet.

**Test** on a card checks that the proxy itself answers. The other
connectors' Test buttons probe along the path you chose here, so a wrong
assignment shows up on the connector it affects.

## One proxy

If your network has one proxy, add one card, leave it as the default and
leave the table on *Default*. That is exactly what the single URL used to do.
