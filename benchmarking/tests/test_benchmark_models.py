from copy import deepcopy

from benchmark_models import BENCHMARK_MODEL, install_oceanx_models

from oceanx import model_config
from oceanx.agent import load_model_profile as imported_loader


def test_all_roles_use_pro_without_changing_settings(monkeypatch):
    settings = {"role_profiles": {"coordinator": "pro", "expert": "flash"},
                "profiles": {
                    "pro": {"provider": "openai", "last_model": "deepseek-v4-pro",
                            "base_url": "https://example.invalid/v1", "credential_slot": "main"},
                    "flash": {"provider": "openai", "last_model": "deepseek-v4-flash"}}}
    before = deepcopy(settings)
    original = model_config._profile_payload
    monkeypatch.setattr(model_config, "_profile_payload", original)
    monkeypatch.setattr(model_config, "_load_settings_payload", lambda: settings)
    monkeypatch.setattr(model_config, "_stored_api_key", lambda **kwargs: "test-only")
    assert imported_loader("expert").model == "deepseek-v4-flash"
    policy = install_oceanx_models()
    for role in policy["roles"]:
        profile = imported_loader(role)
        assert profile.model == BENCHMARK_MODEL
        assert profile.credential_slot == "main"
        assert profile.base_url == "https://example.invalid/v1"
    assert settings == before
    monkeypatch.setattr(model_config, "_profile_payload", original)
    assert imported_loader("expert").model == "deepseek-v4-flash"
