---
icon: clipboard-list
---
## What this is for

Reading tickets, posting comments, and creating issues. The assistant acts as
you, so anything it writes shows your name.

It can only see projects your own account can see.

## Getting the token right

The username is your Atlassian account email, not your display name.

The token is an API token from your Atlassian account, not your password.
Passwords are rejected by Atlassian for API access.

Your administrator has already filled in the site URL and API version.

## If it stops working

Atlassian tokens can be revoked or expire. A task that fails with a credentials
error is usually this.

A permissions error instead means the token is fine but your account cannot
reach that project — ask the project administrator.
