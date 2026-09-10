import json
from types import SimpleNamespace

import pytest

from oceanx.agent_tools import ToolExecutionContext
from oceanx.backend.store import RequestStore
from oceanx.exploration_legacy import ExplorationInput, _statistics, exploration_action
from oceanx.exploration_beliefs import belief_shift, sample_belief_reward
from oceanx.research_learning import ResearchObservationDraft
from oceanx.tools import OceanExplorationTool, OceanToolServices


def test_paper_reward_counts_directional_shift_not_support_or_confidence():
    prior = [True] * 20 + [False] * 10
    result = belief_shift(prior, [False] * 30)
    assert result['prior_beta'] == [21, 11]
    assert result['posterior_beta'] == [21, 41]
    assert result['reward'] == 1
    assert belief_shift([True] * 30, [True] * 30)['reward'] == 0
    assert belief_shift([False] * 30, [False] * 30)['reward'] == 0
    assert belief_shift([True] * 15 + [False] * 15, [True] * 30)['reward'] == 1
    with pytest.raises(ValueError):
        belief_shift([1], [True])


async def test_sampler_isolates_prior_and_records_usage(monkeypatch):
    from oceanx import model_config
    calls = []
    class Model:
        def bind(self, **kw):
            assert kw == {'temperature': 0.7}
            return self
        async def ainvoke(self, messages):
            text = messages[-1].content
            calls.append(text)
            return SimpleNamespace(content='false' if 'Experimental evidence:' in text else 'true',
                                   usage_metadata={'input_tokens': 5, 'output_tokens': 1, 'total_tokens': 6})
    profile = model_config.OceanModelProfile('test', 'test', 'openai', 'mock', None, 'test', 'unused')
    monkeypatch.setattr(model_config, 'load_model_profile', lambda role: profile)
    monkeypatch.setattr(model_config, 'create_chat_model', lambda profile: Model())
    result = await sample_belief_reward('Hypothesis H', 'SECRET_EVIDENCE')
    assert len(calls) == 60
    assert all('SECRET_EVIDENCE' not in text for text in calls[:30])
    assert all('SECRET_EVIDENCE' in text for text in calls[30:])
    assert result['reward'] == 1
    assert result['usage']['total_tokens'] == 360


async def test_record_never_samples_and_replays_durable_receipt(tmp_path):
    store = RequestStore(tmp_path/'state.sqlite3')
    try:
        store.create_research_task(workspace_id='ws', task_id='task', title='Research')
        def call(action, **args):
            return exploration_action(store, 'ws', 'task', ExplorationInput(action=action, **args))
        call('start', expected_revision=0, goal='Question', mode='iterative')
        call('propose', expected_revision=1, candidates=[{'idea':'Hypothesis H','rationale':'Why','test':'How'}])
        obs = store.record_research_observation(ResearchObservationDraft(
            workspace_id='ws', task_id='task', request_id='req', kind='result',
            statement='Observed evidence', outcome='supported')).observation_id
        samples = []
        async def sampler(hypothesis, evidence):
            samples.append((hypothesis, evidence))
            return belief_shift([True]*30, [False]*30)
        tool = OceanExplorationTool(OceanToolServices(
            workspace_id='ws', provider_id='test', store=store, task_id='task',
            skill_role='coordinator', exploration_belief_sampler=sampler))
        args = ExplorationInput(action='record', expected_revision=2, node_id='idea_1', feedback={
            'outcome':'contradicted', 'summary':'Coordinator prose is not sampled as evidence',
            'evidence_ids':[obs], 'information_gain':0, 'branch_status':'solved'})
        context = ToolExecutionContext(cwd=tmp_path, request_id='req', turn_id='turn',
                                       tool_call_id='record', operation_id='record-operation')
        first = await tool.execute(args, context)
        assert not first.is_error, first.output
        second = await tool.execute(args, context)
        assert second.metadata['replayed']
        assert len(samples) == 0
        assert call('read')['children'][0]['reward_sum'] == 0
        # The same observation synthesized into a second node is not a second experiment.
        call('propose', expected_revision=3, candidates=[{'idea':'Synthesis','rationale':'Why','test':'How'}])
        exploration_action(store,'ws','task',ExplorationInput(action='record',expected_revision=4,
            node_id='idea_2',feedback=args.feedback),belief_reward={'reward':0})
        tree=json.loads(store._connection.execute('SELECT tree_json FROM research_exploration_trees').fetchone()[0])
        assert _statistics(tree)['root'] == [1, 0.0]
        # Stale writes are rejected before API work.
        stale = await tool.execute(args, ToolExecutionContext(cwd=tmp_path))
        assert stale.is_error
        assert len(samples) == 0
        # A status/summary correction with unchanged evidence reuses its samples.
        revised = args.model_copy(update={"expected_revision": 5})
        updated = await tool.execute(revised, ToolExecutionContext(cwd=tmp_path))
        assert not updated.is_error, updated.output
        assert len(samples) == 0
    finally:
        store.close()


async def test_unavailable_sampler_cannot_block_research(tmp_path):
    store = RequestStore(tmp_path / "failed.sqlite3")
    try:
        store.create_research_task(workspace_id="ws", task_id="task", title="Research")
        exploration_action(store, "ws", "task", ExplorationInput(action="start",
            expected_revision=0, goal="Question", mode="iterative"))
        exploration_action(store, "ws", "task", ExplorationInput(action="propose",
            expected_revision=1, candidates=[{"idea":"H", "rationale":"R", "test":"T"}]))
        obs = store.record_research_observation(ResearchObservationDraft(
            workspace_id="ws", task_id="task", request_id="req", kind="result",
            statement="Evidence", outcome="supported")).observation_id
        async def fail(hypothesis, evidence):
            raise RuntimeError("Belief sampling unavailable")
        tool = OceanExplorationTool(OceanToolServices(workspace_id="ws", provider_id="test",
            store=store, task_id="task", skill_role="coordinator", exploration_belief_sampler=fail))
        result = await tool.execute(ExplorationInput(action="record", expected_revision=2,
            node_id="idea_1", feedback={"outcome":"supported", "summary":"Result",
                                       "evidence_ids":[obs]}), ToolExecutionContext(cwd=tmp_path))
        assert not result.is_error
        tree = exploration_action(store, "ws", "task", ExplorationInput(action="read"))
        assert tree["revision"] == 3
        assert tree["children"][0]["attempts"] == 1
    finally:
        store.close()
