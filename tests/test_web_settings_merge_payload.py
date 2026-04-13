from app.web import _settings_merge_payload


def test_settings_merge_github_base_url_blank_removes_existing_value():
    config_payload = {
        "github": {
            "enabled": True,
            "api_token": "keep-token",
            "base_url": "https://github.company.com/api/v3",
        }
    }
    form = {
        "github_enabled": "on",
        "github_api_token": "",
        "github_base_url": "",
    }

    merged, error = _settings_merge_payload(config_payload, form)

    assert error is None
    assert merged["github"]["api_token"] == "keep-token"
    assert "base_url" not in merged["github"]


def test_settings_merge_github_base_url_non_empty_overrides_value():
    config_payload = {
        "github": {
            "enabled": True,
            "base_url": "https://github.company.com/api/v3",
        }
    }
    form = {
        "github_enabled": "on",
        "github_base_url": " https://api.github.com ",
    }

    merged, error = _settings_merge_payload(config_payload, form)

    assert error is None
    assert merged["github"]["base_url"] == "https://api.github.com"
