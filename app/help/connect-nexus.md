---
icon: package
---
## What this is for

Answering "which version of this artifact is in the repository, and when was
it published?" without leaving the conversation. The assistant lists
repositories and searches components by repository, name and version through
the `nexus` CLI.

It reads; it never uploads, deletes or re-tags an artifact.

## What to enter

- **URL** is the Nexus base address, for example `https://nexus.example.com`,
  without `/service/rest` on the end.
- **Username** and **token** are a read-only account and its user token
  (Nexus lets you generate one under your profile). A password works in the
  token's place. Leave both blank when the repositories allow anonymous
  reads.
- **Name** is how the assistant addresses this instance with `--instance`.
  With several instances, the **default instance** is the one used when a
  request does not name one.

## If it stops working

An `auth_failed` result means the token or password was rejected: Nexus user
tokens can be reset by an administrator, which invalidates the old one. A
`404` on a repository usually means the account cannot see it rather than
that it does not exist.
