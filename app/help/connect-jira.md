---
icon: clipboard-list
---
## What this is for

Reading tickets, posting comments, and creating issues. The assistant acts as
you, so anything it writes shows your name.

It can only see projects your own account can see.

## Getting the token right

Which token you need depends on where your Jira runs.

On Atlassian Cloud (the address ends in atlassian.net), the token is an API
token from your Atlassian account, not your password, and the username is your
Atlassian account email, not your display name. Passwords are rejected by
Atlassian for API access.

On a Jira your company hosts (Data Center), the token is a personal access
token you create from your profile in Jira. It needs no username: the
assistant signs in with the token alone.

Your administrator has already filled in the site URL and API version.

## If it stops working

Atlassian tokens can be revoked or expire. A task that fails with a credentials
error is usually this.

A permissions error instead means the token is fine but your account cannot
reach that project — ask the project administrator.
