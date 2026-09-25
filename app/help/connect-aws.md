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
- **Regions** are the regions it is used in, chosen from `ap-east-1`,
  `eu-west-1` and `us-east-1`; the first chosen is the default for cluster
  commands.

Switch a row off to keep it without offering it to assistants. The default
account is the one used when a request does not name an account.

## Clusters behind PrivateLink

An EKS cluster whose API server endpoint is private cannot be reached at the
address AWS reports for it; from another VPC it is reached through a
PrivateLink interface endpoint. List each such cluster here, one row per
cluster, and the assistant's `kubectl` context is pointed at that address
automatically:

- **Account** is chosen from the account rows above.
- **Cluster** is the EKS cluster name.
- **Region** is optional, chosen from the same three regions, for a cluster
  name that exists in several regions of the same account.
- **Private endpoint** is the PrivateLink address, for example
  `https://vpce-0ab12cd.vpce-svc-0123.eu-west-1.vpce.amazonaws.com`.
- **Certificate authority** says whose certificate answers at that address.
  *Cluster CA* is for an endpoint that passes TLS straight through to the API
  server: the certificate is the cluster's own, and the assistant keeps the
  cluster CA and verifies it under the cluster's hostname.
  *System trust store* is for an endpoint that terminates TLS with a
  certificate of its own, issued for the endpoint's name by a CA the runtime
  already trusts:
  the assistant then drops the embedded cluster CA and verifies the way any
  HTTPS client would. Verification is never switched off in either case.
- **TLS server name** can stay empty: the certificate is verified under the
  cluster's hostname (cluster CA) or the endpoint's hostname (system trust
  store). Set it only if the platform team tells you the certificate carries
  another name.

Before adding a row, an assistant (or you, in the runtime) can check the
address with `aws-auth eks endpoint --account <name> --cluster <cluster>
--private-endpoint <address> --json`. It reports whether the address answers,
whether the certificate it presents is the cluster's own or one the runtime
trusts, and which certificate authority setting that calls for. A
certificate nothing trusts means the platform's CA is missing from the
runtime image; the check says so rather than guessing.

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
