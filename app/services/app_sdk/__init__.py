"""What an app may ask the Portal to do with its owner's own settings.

Three libraries, all free of apps, tokens and the database: the caller hands
in the owner's effective settings
(``RuntimeProfileService.effective_config_for``) and gets plain results back.

- ``llm_gateway``: a text completion on the owner's model provider (GitHub
  Copilot or AI Platform).
- ``connector_proxy``: one HTTP request to one of the owner's connectors (Jira,
  Confluence, GitHub, Jenkins, Nexus, Splunk) with the connector's credentials.
- ``image_analysis``: a question about one to four images, answered by the
  model of the owner's Image analysis connector (AI Platform).

The endpoints, authentication, budgets and usage records are built on top of
these, elsewhere.
"""
