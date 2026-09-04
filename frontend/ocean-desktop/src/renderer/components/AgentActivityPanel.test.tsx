import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';

import type {TeamAgent, TeamAgentTranscript} from '../types.js';
import {AgentActivityPanel} from './AgentActivityPanel.js';

const expert: TeamAgent = {
  agent_id: 'agent-1',
  profile_id: 'ocean_process_expert',
  semantic_role: 'Ocean Process & Mechanism Expert',
  authority: 'expert',
  status: 'completed',
  activity: '## Annual Oceanographic Baseline — Gulf of Mexico\n\nCompleted the baseline.',
  work_order_id: 'wo-1',
};

function transcriptWith(text: string): TeamAgentTranscript {
  return {
    agent_id: 'agent-1',
    work_order_id: 'wo-1',
    compaction_generation: 0,
    messages: [
      {message_id: 'm1', role: 'coordinator', blocks: [{type: 'text', text}]},
    ],
  };
}

function renderPanel(transcript: TeamAgentTranscript): string {
  return renderToStaticMarkup(<AgentActivityPanel
    agent={expert}
    profiles={[]}
    todos={[]}
    transcript={transcript}
    loading={false}
    error={null}
  />);
}

describe('AgentActivityPanel', () => {
  it('renders a work-order JSON envelope as a structured assignment card', () => {
    const text = 'Accept this assignment and return one compact candidate answer.\n\n' + JSON.stringify({
      task_goal: '基于 CMEMS_oceanmind 数据集分析温盐结构',
      done_when: '已核实数据集变量、空间与时间覆盖并写入报告',
      outcome_intents: ['answer', 'report'],
      sources: [{handle: 'source_1', kind: 'dataset', title: 'CMEMS_oceanmind'}],
    });
    const markup = renderPanel(transcriptWith(text));

    expect(markup).toContain('class="agent-assignment-card"');
    expect(markup).toContain('基于 CMEMS_oceanmind 数据集分析温盐结构');
    expect(markup).toContain('Done when');
    expect(markup).toContain('已核实数据集变量、空间与时间覆盖并写入报告');
    expect(markup).toContain('CMEMS_oceanmind (dataset)');
    expect(markup).toContain('Full instructions');
    // The raw JSON envelope must not be dumped into the conversation.
    expect(markup).not.toContain('"task_goal"');
  });

  it('folds unrecognized JSON payloads into a collapsible block', () => {
    const markup = renderPanel(transcriptWith(JSON.stringify({findings: 'some structured note'})));

    expect(markup).toContain('Structured payload');
    expect(markup).not.toContain('agent-assignment-card');
  });

  it('keeps plain prose messages on the markdown path', () => {
    const markup = renderPanel(transcriptWith('分析已完成，结论如下。'));

    expect(markup).toContain('分析已完成，结论如下。');
    expect(markup).not.toContain('agent-assignment-card');
    expect(markup).not.toContain('Structured payload');
  });

  it('strips markdown noise from the header activity line', () => {
    const markup = renderPanel(transcriptWith('hello'));

    expect(markup).toContain('Annual Oceanographic Baseline');
    expect(markup).not.toContain('##');
  });
});
