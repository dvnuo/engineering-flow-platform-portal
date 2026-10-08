---
icon: database
---
## What this is for

Letting the assistant work with a PostgreSQL database: which tables and
columns exist, what a query returns, and, where you allow it, applying a
change you asked for. It uses the `pgsql` CLI, with a row limit and a
statement timeout on every query. Bulk export and import are the exception:
they stream between the database and a file inside the assistant's own
container, past the row limit, because an extract that stops at 200 rows is
not an extract. They are bounded by a size cap and their own timeout, and a
role that cannot write still cannot import.

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
- **Proxy** is how the connection leaves the runtime. Blank follows the Proxy
  connector the way every other tool does: through its proxy unless its
  `NO_PROXY` exempts the host. Enter `none` for a database the runtime
  reaches directly, or an `http://host:port` proxy for this database alone.
  Proxy credentials go in the Proxy connector, not here.

## Testing the connection

Test connection checks that the host and port answer, taking the path the
runtime would take: through the Proxy connector's proxy unless `NO_PROXY`
or the row's **Proxy** says otherwise, so a host the Portal cannot resolve
itself is still checked. The sign-in is verified inside the runtime with
`pgsql auth test`. A `Name or service not known` on a direct connection
means the proxy was not used: check the Proxy connector and its `NO_PROXY`.

## If it stops working

A `permission denied` on a table means the role lacks `SELECT` on it. A
connection that times out usually means a firewall between the runtime and
the database rather than a wrong password.
