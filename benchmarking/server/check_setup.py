"""Check benchmark configuration and execution environment without model calls."""
import argparse
import json
import shutil
import sys

from benchmark_config import load_config, preflight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--agent', choices=['oceanx', 'claude', 'both'], default='both')
    args = parser.parse_args()
    config = load_config(args.config)
    if args.agent in ('oceanx', 'both'):
        config.endpoint(config.oceanx_api)
    if args.agent in ('claude', 'both'):
        config.endpoint('anthropic')
        if not shutil.which('claude'):
            raise ValueError('Claude Code is not on PATH; install the CLI first')
    preflight(require_sandbox=args.agent in ('oceanx', 'both'))
    print(json.dumps({'environment_ready': True, 'python': sys.executable,
                      'config': config.public(), 'api_live_tested': False}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Preflight failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
