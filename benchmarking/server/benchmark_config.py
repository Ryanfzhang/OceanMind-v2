"""One explicit benchmark configuration; never inherit credentials from shell settings."""
from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / 'benchmark.yaml'


@dataclass(frozen=True)
class Endpoint:
    url: str
    api_key: str = field(repr=False)


@dataclass(frozen=True)
class Config:
    model: str
    oceanx_api: str
    endpoints: dict[str, Endpoint] = field(repr=False)
    max_tokens: int = 32768

    def endpoint(self, protocol):
        e = self.endpoints.get(protocol)
        if not e or not e.url or not e.api_key or e.api_key.startswith('REPLACE_'):
            raise ValueError(f'benchmark.yaml: fill {protocol}.url and {protocol}.api_key')
        u = urlsplit(e.url)
        if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError(f'benchmark.yaml: invalid {protocol}.url')
        return e

    def public(self):
        return {'model': self.model, 'oceanx_api': self.oceanx_api, 'max_tokens': self.max_tokens,
                'endpoints': {k: {'url': v.url} for k, v in self.endpoints.items()}}


def load_config(path=None):
    path = Path(path or os.environ.get('OCEAN_BENCH_CONFIG', DEFAULT_CONFIG)).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f'Missing {path}; copy benchmark.example.yaml to benchmark.yaml and fill it')
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        location = f' at line {mark.line + 1}, column {mark.column + 1}' if mark else ''
        raise ValueError('Invalid benchmark.yaml syntax' + location) from None
    if not isinstance(raw, dict):
        raise ValueError('benchmark.yaml must contain a mapping of configuration fields')
    model = raw.get('model')
    if not isinstance(model, str) or not model.strip() or model.strip().startswith('REPLACE_'):
        raise ValueError('benchmark.yaml: replace model placeholder with the actual provider model ID')
    protocol = raw.get('oceanx_api')
    if protocol not in ('openai', 'anthropic'):
        raise ValueError('benchmark.yaml: oceanx_api must be openai or anthropic')
    tokens = raw.get('max_tokens', 32768)
    if type(tokens) is not int or tokens < 1:
        raise ValueError('benchmark.yaml: max_tokens must be a positive integer')
    endpoints = {}
    for name in ('openai', 'anthropic'):
        item = raw.get(name, {})
        if not isinstance(item, dict):
            raise ValueError(f'benchmark.yaml: {name} must contain url and api_key fields')
        url, key = item.get('url', ''), item.get('api_key', '')
        if not isinstance(url, str) or not isinstance(key, str):
            raise ValueError(f'benchmark.yaml: {name}.url and {name}.api_key must be strings')
        endpoints[name] = Endpoint(url.strip(), key.strip())
    return Config(model.strip(), protocol, endpoints, tokens)


def configure_runtime():
    """The benchmark's active interpreter is also its scientific runtime."""
    import sys
    if sys.version_info < (3, 11):
        raise ValueError('Activate oceanx-bench with Python 3.11 or newer')
    os.environ['OCEAN_SANDBOX_PYTHON'] = sys.executable
    os.environ['OCEAN_BENCH_RENDER_PYTHON'] = sys.executable


def preflight(require_sandbox=False):
    from oceanx.sandbox.execution import current_python_runtime
    configure_runtime()
    current_python_runtime()
    if require_sandbox:
        import asyncio
        from oceanx.sandbox_self_check import run_sandbox_self_check
        report = asyncio.run(run_sandbox_self_check())
        if not report.get('passed'):
            raise ValueError('Benchmark sandbox check failed before model calls: ' + str(report))


def claude_environment(config):
    endpoint = config.endpoint('anthropic')
    env = dict(os.environ)
    # Remove old routing, aliases and cloud-provider modes from the inherited shell.
    for name in list(env):
        if name.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_', 'CLAUDE_CODE_SUBAGENT_MODEL')):
            env.pop(name)
    env.update(ANTHROPIC_BASE_URL=endpoint.url, ANTHROPIC_AUTH_TOKEN=endpoint.api_key,
               ANTHROPIC_API_KEY=endpoint.api_key, ANTHROPIC_MODEL=config.model,
               ANTHROPIC_DEFAULT_OPUS_MODEL=config.model,
               ANTHROPIC_DEFAULT_SONNET_MODEL=config.model,
               ANTHROPIC_DEFAULT_HAIKU_MODEL=config.model,
               CLAUDE_CODE_SUBAGENT_MODEL=config.model)
    return env
