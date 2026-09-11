"""Research-track routing changes the submitted prompt, not shared scientific inputs."""
import pytest
from oceanx import batch
from run_oceanx import BenchmarkClient


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', [*(f'Q{i:02}' for i in range(1, 31)), 'QUERY', 'Q31'])
async def test_submitted_tree_instruction_is_scoped_and_recorded(tmp_path, monkeypatch, case_id):
    sent = []

    async def capture(self, kind, payload):
        sent.append((kind, payload))
        return 'request'

    monkeypatch.setattr(batch.BatchClient, 'send', capture)
    case = batch.QueryCase(id=case_id, query='Investigate the supplied data.')
    client = BenchmarkClient(tmp_path, case)
    payload = {'text': case.query, 'literature_acquisition_mode': 'search_only'}
    await client.send('session.submit', payload)
    actual = sent[-1][1]['text']
    required = case_id in {f'Q{i:02}' for i in range(7, 31)}
    assert ('Use Research Tree' in actual) is required
    assert actual.startswith(case.query)
    assert case.query == payload['text'] == 'Investigate the supplied data.'
    assert sent[-1][1]['literature_acquisition_mode'] == 'search_only'
    assert (tmp_path / 'submitted_prompt.txt').read_text() == actual
    followup = {'answer': 'Continue'}
    await client.send('interaction.respond', followup)
    assert sent[-1] == ('interaction.respond', followup)
