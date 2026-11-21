"""What an app may ask the Portal to do with its owner's own settings.

Two libraries, both free of apps, tokens and the database: the caller hands in
the owner's effective settings (``RuntimeProfileService.effective_config_for``)
and gets plain results back.

- ``llm_gateway``: a text completion on the owner's model provider (GitHub
  Copilot or AI Platform).
- ``connector_proxy``: one HTTP request to one of the owner's connectors (Jira,
  Confluence, GitHub, Jenkins, Nexus, Splunk) with the connector's credentials.

The endpoints, authentication, budgets and usage records are built on top of
these, elsewhere.
"""
