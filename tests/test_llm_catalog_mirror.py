"""The picker in the browser must offer exactly the catalog's models.

llm_catalog calls itself the single source of truth, but the model list is
mirrored by hand into chat_ui.js -- and the one test that looked at that block
needs node, so it skips wherever node is absent and had already drifted to
asserting on an `openai` provider that no longer exists. A member picking a
model the backend then coerces away, or never being offered one the backend
accepts, both look like bugs in the model itself.

Values only: the labels are UI copy and the catalog has no opinion about them.
"""
import json
import re
from pathlib import Path

import pytest

from app.contracts.llm_catalog import PROVIDER_MODELS


def _managed_provider_models() -> dict[str, list[str]]:
    """Read the `const managedProviderModels = {...}` table out of chat_ui.js."""
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    marker = "const managedProviderModels = "
    start = source.index(marker) + len(marker)
    assert source[start] == "{", "managedProviderModels is no longer an object literal"

    depth = 0
    for end in range(start, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                break
    else:  # pragma: no cover - unbalanced literal
        pytest.fail("managedProviderModels literal is unbalanced")

    literal = source[start : end + 1]
    # JS object literal -> JSON: quote every bare key (an identifier that opens
    # an object or follows a comma), then drop trailing commas.
    literal = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', literal)
    literal = re.sub(r",(\s*[\]}])", r"\1", literal)
    table = json.loads(literal)
    return {provider: [entry["value"] for entry in entries] for provider, entries in table.items()}


def test_the_browser_picker_offers_exactly_the_catalog_models():
    assert _managed_provider_models() == {
        provider: list(models) for provider, models in PROVIDER_MODELS.items()
    }


def test_ai_platform_offers_the_gpt_5_6_line():
    # The gateway fronts the same models as Copilot's 5.6 line; offering only
    # 5.4 there meant switching provider quietly downgraded the model.
    ai_platform = PROVIDER_MODELS["ai_platform"]

    assert [model for model in ai_platform if model.startswith("gpt-5.6")] == [
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    ]


def test_every_model_carries_a_label_in_the_picker():
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    for models in PROVIDER_MODELS.values():
        for model in models:
            assert f'value: "{model}", label: "' in source, model
