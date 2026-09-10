"""Use the root YAML for every benchmark role; leave production settings untouched."""
from benchmark_config import load_config


def install_oceanx_models(config=None):
    from oceanx import model_config
    config = config or load_config()
    endpoint = config.endpoint(config.oceanx_api)
    slot = 'benchmark-yaml'
    original_key = model_config._stored_api_key

    def profile(settings, *, role='coordinator'):
        return slot, {'provider': config.oceanx_api, 'last_model': config.model,
                      'base_url': endpoint.url, 'credential_slot': slot}

    def key(*, provider, slot):
        if slot == 'benchmark-yaml':
            return endpoint.api_key
        return original_key(provider=provider, slot=slot)

    model_config._profile_payload = profile
    model_config._stored_api_key = key
    model_config._load_settings_payload = lambda: {'max_tokens': config.max_tokens}
    return {**config.public(), 'roles': ['coordinator', 'expert', 'skill_curator'],
            'api_profile': 'benchmark.yaml', 'scope': 'benchmark_process_only'}
