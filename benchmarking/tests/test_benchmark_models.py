import json

import pytest
from benchmark_config import Config, Endpoint, load_config, claude_environment
from benchmark_models import install_oceanx_models
from oceanx import model_config
from oceanx.agent import load_model_profile as imported_loader


def config():
    return Config('test-model', 'openai', {
        'openai': Endpoint('https://openai.example/v1', 'private-test-key'),
        'anthropic': Endpoint('https://anthropic.example', 'other-private-key')})


def test_yaml_overrides_all_roles_and_stored_credentials_without_saving(monkeypatch):
    for name in ['_profile_payload', '_stored_api_key', '_load_settings_payload']:
        monkeypatch.setattr(model_config, name, getattr(model_config, name))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'wrong-old-key')
    c = config()
    policy = install_oceanx_models(c)
    for role in policy['roles']:
        p = imported_loader(role)
        assert (p.model, p.provider, p.base_url, p.api_key) == (
            'test-model', 'openai', 'https://openai.example/v1', 'private-test-key')
    assert 'private' not in json.dumps(policy)
    assert 'private' not in repr(c)


def test_claude_environment_replaces_old_routing(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://wrong.example')
    monkeypatch.setenv('CLAUDE_CODE_USE_BEDROCK', '1')
    monkeypatch.setenv('ANTHROPIC_DEFAULT_HAIKU_MODEL', 'wrong-model')
    env = claude_environment(config())
    assert env['ANTHROPIC_BASE_URL'] == 'https://anthropic.example'
    assert env['ANTHROPIC_AUTH_TOKEN'] == 'other-private-key'
    assert env['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'test-model'
    assert 'CLAUDE_CODE_USE_BEDROCK' not in env


def test_config_errors_do_not_expose_keys(tmp_path):
    p = tmp_path/'config.yaml'
    p.write_text('model: [\napi_key: secret-do-not-print')
    with pytest.raises(ValueError) as exc:
        load_config(p)
    assert 'secret-do-not-print' not in str(exc.value)
    p.write_text('model: test\noceanx_api: openai\nopenai:\n  url: https://example/v1\n  api_key: key\n')
    c = load_config(p)
    assert c.endpoint('openai').api_key == 'key'
    with pytest.raises(ValueError, match='anthropic'):
        c.endpoint('anthropic')
