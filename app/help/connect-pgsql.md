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
not an extract. They are bounded by a size cap and their own timeout.

Two things decide what the assistant can do here. **Access**, which applies
to every instance in the connector, decides whether its pgsql tool writes at
all. The **role** you enter on each row decides what the database itself
allows that tool, and any other client, to do.

## What applies to every instance

- **Access** is Read-only unless you change it. Read-only means the
  assistant's pgsql tool refuses, before it even connects, every command and
  statement that changes data or server state: `exec`, bulk import, any
  statement that is not a plain read, and reads that call functions such as
  `pg_terminate_backend`. It also asks the server for read-only transactions
  on every connection. The assistant reports the refusal and describes the
  statement for a human to run. Read-write lets the tool run whatever the
  role allows; choose it only for a connector you intend the assistant to
  change things through.
- **SSL mode** is `require` unless the servers present a certificate the
  runtime can verify, in which case `verify-ca` or `verify-full` is safer.
  `prefer` is for databases that do not offer TLS at all.
- **Statement timeout** and **Max rows per query** bound every query the
  assistant runs. Blank means the runtime's defaults, 30 seconds and 5000
  rows.

## What to enter per instance

- **Host**, **port** (5432 unless your DBA says otherwise) and **database**
  name a single database; add one row per database.
- **Username** and **password** are the database's own control. For
  troubleshooting, use a role granted `SELECT` on the schemas you want the
  assistant to see and nothing else: that keeps the instance read-only for
  any client, whatever Access says. Use a role with write privileges only for
  an instance you intend the assistant to change things through, together
  with Read-write access.
- **Name** is how the assistant addresses this instance with `--instance`.

The connection leaves the runtime the way the Proxy connector routes
PostgreSQL: through the proxy it assigns to PostgreSQL unless that proxy's
**No proxy** list exempts the host. A database the runtime reaches directly
goes on that list. Proxy credentials live in the Proxy connector.

## Testing the connection

Test connection checks that the host and port answer, taking the path the
runtime would take: through the Proxy connector's proxy unless `NO_PROXY`
says otherwise, so a host the Portal cannot resolve itself is still checked.
The sign-in is verified inside the runtime with `pgsql auth test`, which
also reports `access` and why the session is read-only (the connector, a
replica, or the server's own default). A `Name or service not known` on a
direct connection means the proxy was not used: check the Proxy connector
and its `NO_PROXY`.

## If it stops working

A `permission denied` on a table means the role lacks `SELECT` on it. A
`read_only_mode` answer from the assistant means the connector's Access is
Read-only, which is working as intended. A connection that times out
usually means a firewall between the runtime and the database rather than a
wrong password.
