---
icon: cloud
---
## What this is for

Inspecting AWS accounts and EKS clusters: instances, AMIs, CloudWatch logs,
and what is running in a cluster.

Read-oriented: an assistant reports what it finds rather than changing
infrastructure. It signs in through the `aws-auth` CLI, keeps one AWS CLI
profile per account row, and uses kubectl read-only (get, describe, logs,
events).

## One row per account

Each row is one AWS account the assistant may sign in to:

- **Name** becomes the AWS CLI profile the assistant passes as `--profile`, so
  keep it short and unique, for example `cps-dev`.
- **Account id** is the 12-digit AWS account number.
- **Role** is the IAM role to assume there. Choose a read-only one; the
  assistant can only do what the role allows.
- **Regions** are the regions it should look in, comma separated.

Switch a row off to keep it without offering it to assistants. The default
account is the one used when a request does not name an account.

## Providers

- **adfs-assume** (the default) signs in to ADFS with the domain, username
  and password above and assumes the listed role in each account.
- **saml2aws** uses the same directory account through the ADFS IdP URL.
- **assume-role** needs no password: it assumes each role from the
  credentials of the source profile already present in the runtime.

## When something fails

An expired or missing token shows up as an aws or kubectl error, and the
assistant signs in again with `aws-auth login`. A row is only usable when
its role can be assumed, so check the account id and role name first.
