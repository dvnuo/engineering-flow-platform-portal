---
icon: database
---
## What this is for

Letting the assistant work with a PostgreSQL database: which tables and
columns exist, what a query returns, and, where you allow it, applying a
change you asked for. It uses the `pgsql` CLI, with a row limit and a
statement timeout on every call.

What the assistant can do here is decided by what you enter below, not by
the tool. Give it a read-only role, or point it at a read replica, and it
can only read: a write is refused by the server itself. Give it a role that
may write, and it can write. Configure one instance per level of access you
want, and name them so the difference is obvious.

## What to enter

- **Host**, **port** (5432 unless your DBA says otherwise) and **database**
  name a single database; add one row per database.
- **Username** and **password** are the control. For troubleshooting, use a
  role granted `SELECT` on the schemas you want the assistant to see and
  nothing else; that role is what makes the instance read-only, and it holds
  whatever the assistant is asked to do. Use a role with write privileges
  only for an instance you intend the assistant to change things through.
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
