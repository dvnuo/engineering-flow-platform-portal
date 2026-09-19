---
icon: activity
---
## What this is for

Reading AppDynamics while the assistant works out why something is slow or
failing: the applications the controller knows, slow and error transaction
snapshots over a time window, and health-rule violations. It uses the `appd`
CLI, read-only.

It never changes a health rule, an application or an alert.

## What to enter

- **URL** is the controller, for example
  `https://appd-controller.example.com`.
- **Account** is the account name shown on the controller's login page
  (`customer1` on many on-premise controllers).
- **API client** (the default): create an API Client in the controller under
  Administration, API Clients, give it a read-only role, and enter its name
  as the username and its client secret as the token. The assistant
  exchanges the pair for a short-lived access token on each run.
- **Basic**: enter a read-only user's name and password instead. The
  assistant signs in as `username@account`.
- **Name** is how the assistant addresses this instance with `--instance`.

## If it stops working

A client secret can be regenerated in the controller, which invalidates the
old one; an `auth_failed` result after that means the stored secret is stale.
A `403` on snapshots means the role can sign in but is not allowed to read
that application.
