"""Launch an isolated three-arm rerun using the established Campeche protocol."""
import concurrent.futures
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[2]


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    if root.is_relative_to(REPO):
        raise ValueError('Use an output directory outside the repository for Claude isolation')
    protocol = {
        'query': 'Using the provided one-year GLORYS12 reanalysis, autonomously investigate the mechanisms that form and sustain seasonal warm anomalies in the Bay of Campeche, and assess which processes are supported by the data.',
        'datasets': [str(REPO.parent / 'CMEMS_oceanmind' / f'CMEMS_{v}.zarr')
                     for v in ('chlorophyll', 'oxygen', 'salt', 'temp', 'u', 'v')],
        'timeout_seconds': 7200, 'literature_mode': 'search_only',
        'claude_token_accounting': 'result.modelUsage across all models; input, output, cache read and cache write separately; raw events retained',
    }
    for path in protocol['datasets']:
        if not Path(path).is_dir():
            raise FileNotFoundError(path)
    case = dict(id='CAMPECHE', query=protocol['query'], datasets=protocol['datasets'],
                timeout_seconds=7200, literature_mode='search_only')
    (root / 'query.jsonl').write_text(json.dumps(case) + '\n')
    for arm in ('tree', 'no_tree'):
        shutil.copytree(REPO / 'src', root / arm / 'src',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    replacements = {
        'runtime.py': [('if services.task_id and services.skill_role == "coordinator"\n                else ()',
                        'if False  # Isolated benchmark: no tree policy.\n                else ()')],
        'tools.py': [
            ('if services.task_id is not None and services.skill_role == "coordinator":\n        registry.register(OceanExplorationTool(services))',
             '# Isolated benchmark: no tree tool.'),
            ('research = arguments.research_question is not None', 'research = False  # Isolated no-tree arm.'),
            ('"wave. For investigations set research_question and maintain hypotheses with ocean_exploration; "', '"wave. "'),
            ('"For an investigation, supply its root research question on EVERY wave, including "\n            "supporting data checks and follow-ups. This initializes a missing research tree and "\n            "returns its context with Expert results. The user need not explicitly request a tree. "\n            "Leave null only for direct tasks such as a specified plot, download or calculation."',
             '"Optional research question; use plan_goal for the scientific objective."'),
        ],
    }
    patches = []
    for filename, pairs in replacements.items():
        path = root / 'no_tree/src/oceanx' / filename
        before = path.read_text()
        after = before
        for old, new in pairs:
            if after.count(old) != 1:
                raise ValueError(f'Cannot safely apply ablation: {filename}: {old[:60]}')
            after = after.replace(old, new)
        path.write_text(after)
        patches.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                           fromfile='tree/' + filename, tofile='no_tree/' + filename))
    (root / 'ablation.patch').write_text(''.join(patches))
    shutil.copytree(REPO / 'benchmarking/server', root / 'runners',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    claude_runner = root / 'runners/run_claude.py'
    source = claude_runner.read_text()
    source = source.replace('REPO = Path(__file__).resolve().parents[2]', f'REPO = Path({str(REPO)!r})')
    source = source.replace('"python_executable": sys.executable',
                            '"python_executable": "/Users/ryanzhang/miniconda3/envs/ocean/bin/python"')
    old = '"--no-session-persistence", "--permission-mode", "default"]'
    if source.count(old) != 1:
        raise ValueError('Claude settings isolation anchor changed')
    source = source.replace(old, '"--no-session-persistence", "--permission-mode", "default", "--setting-sources", ""]')
    claude_runner.write_text(source)
    protocol.update(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
                    oceanx_models={"coordinator": "deepseek-v4-pro", "expert": "deepseek-v4-pro"},
                    claude_model="deepseek-v4-pro",
                    started_at=datetime.now(timezone.utc).isoformat(),
                    delivery='same benchmark file adapter for both OceanX arms',
                    rerun=True, output=str(root))
    (root / 'protocol.json').write_text(json.dumps(protocol, indent=2))
    (root / 'source_hashes.json').write_text(json.dumps({
        str(p.relative_to(root / 'tree')): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root / 'tree/src').rglob('*') if p.is_file()}, indent=2))

    def run(arm):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(root / ('tree' if arm == 'claude' else arm) / 'src')
        env['OCEAN_SANDBOX_PYTHON'] = '/Users/ryanzhang/miniconda3/envs/ocean/bin/python'
        if arm == 'claude':
            for key in ('ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
                        'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
                        'ANTHROPIC_SMALL_FAST_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL'):
                env[key] = 'deepseek-v4-pro'
        command = [sys.executable, str(root / 'runners' / ('run_claude.py' if arm == 'claude' else 'run_oceanx.py')),
                   '--queries', str(root / 'query.jsonl'), '--output', str(root / ('results_' + arm))]
        if arm == 'claude':
            command += ['--model', 'deepseek-v4-pro', '--model-label', 'DeepSeek V4 Pro via Claude Code', '--allow-tools',
                        'Read', 'Glob', 'Grep', 'Bash', 'Write', 'Edit', 'NotebookEdit', 'WebSearch', 'WebFetch']
        with (root / (arm + '.runner.log')).open('xb') as log:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            (root / (arm + '.launch.json')).write_text(json.dumps(dict(pid=process.pid, command=command)))
            code = process.wait()
        outcome = dict(arm=arm, exit_code=code, ended_at=datetime.now(timezone.utc).isoformat())
        (root / (arm + '.exit.json')).write_text(json.dumps(outcome))
        return outcome

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(run, ['tree', 'no_tree', 'claude']))
    (root / 'finished.json').write_text(json.dumps(outcomes, indent=2))


if __name__ == '__main__':
    main()
