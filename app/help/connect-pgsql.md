---
icon: database
---
## What this is for

Letting the assistant look inside a PostgreSQL database while it
troubleshoots: which tables and columns exist, and what a query returns. It
uses the `pgsql` CLI, and every query runs inside a read-only transaction
with a row limit, so nothing it does can change data.

It does not create, alter or drop anything, and it does not run
administrative commands.

## What to enter

- **Host**, **port** (5432 unless your DBA says otherwise) and **database**
  name a single database; add one row per database.
- **Username** and **password** should be a read-only role: a login granted
  `SELECT` on the schemas you want the assistant to see and nothing else.
  Even though every query is wrapped in a read-only transaction, the role
  is the guarantee that holds if that ever fails.
- **SSL mode** is `require` unless the server presents a certificate you can
  verify, in which case `verify-ca` or `verify-full` is safer. `prefer` is
  for databases that do not offer TLS at all.
- **Name** is how the assistant addresses this instance with `--instance`.

## Testing the connection

Test connection here only checks that the host and port answer from the
Portal; the sign-in itself is checked inside the runtime with
`pgsql auth test`, because the database is often reachable from the cluster
but not from the Portal.

## If it stops working

A `permission denied` on a table means the role lacks `SELECT` on it. A
connection that times out usually means a firewall between the runtime and
the database rather than a wrong password.
