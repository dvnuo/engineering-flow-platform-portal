"""Apps: members' web apps, registered here and run by the app host.

- ``settings``: the APPS_* configuration, read at call time.
- ``names``: slugs, visibilities, app types and other shared names.
- ``errors``: what can go wrong, each with a code and a message a member can read.
- ``manifest``: ``efp-app.json`` validation and the ownership rule.
- ``source``: the repository read through the GitHub REST API.
- ``host_client``: the app host's manager API.
- ``host_state``: what the host last reported, kept in memory.
- ``access``: who may open or manage an app, and the sign-in hand-off codes.
- ``tokens``: app tokens.
- ``audit``: ``app_audit_events``.
- ``app_service``: create, change, delete and run apps.
- ``deploy``: deployments and rollbacks.
- ``templates``: versions of a template app, apps created from one, upgrades.
- ``visitor``, ``rate_limit``, ``budget``, ``executions``: what the SDK API
  (app/api/app_sdk.py) checks and writes down for each call of an app.
- ``usage``: the figures of an app's usage page and of the administration's list.
- ``error_events``: failed deployments and crashes in the Portal's error log.
- ``purge_worker``: removes the data of apps deleted long enough ago.
- ``member_hooks``: what the rest of the Portal tells this package.

See APPS_PLAN.md and the contracts in the app repository (docs/CONTRACTS.md).
"""
