import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it, vi} from 'vitest';

import {UiLanguageProvider} from '../i18n.js';
import type {ProjectCatalogEntry} from '../project-catalog.js';
import {OceanMindWelcome} from './OceanMindWelcome.js';
import {ProjectTaskSidebar} from './ProjectTaskSidebar.js';
import {ProjectRemoveDialog} from './dialogs/ProjectRemoveDialog.js';

const projects: ProjectCatalogEntry[] = [{
  path: '/research/gulf',
  name: 'gulf',
  lastOpenedAt: 1,
  tasks: [{
    task_id: 'task_1', workspace_id: 'ws_desktop', title: '分析温盐结构', status: 'active',
    task_revision: 2, conversation_generation: 0, active_request_id: 'request_1',
  }],
}];

describe('OceanMind entry layout', () => {
  it('offers three concise research starting points before a task is created', () => {
    const markup = renderToStaticMarkup(<OceanMindWelcome
      projectName="gulf"
      hasProject
      title=""
      onTitle={() => undefined}
      onCreateTask={() => undefined}
      onChooseProject={() => undefined}
    />);

    expect(markup).toContain('What would you like to do with OceanMind?');
    expect(markup).toContain('Find and review papers');
    expect(markup).toContain('Analyze data');
    expect(markup).toContain('Run Autoresearch');
    expect(markup.match(/welcome-entry-point /g)).toHaveLength(3);
    expect(markup).toContain('<article class="welcome-entry-point papers"');
    expect(markup).not.toContain('aria-pressed');
    expect(markup).toContain('placeholder="Name this research task"');
    expect(markup).not.toContain('From ocean data to');
    expect(markup).not.toContain('Start new research in gulf');
    expect(markup).toContain('core-hkust-logo');
    expect(markup).toContain('https://core-hkmacau.hkust.edu.hk/');
    expect(markup).toContain('Supported by CORE, HKUST');
    expect(markup).toContain('<form');
    expect(markup).toContain('type="submit"');
  });

  it('provides the same three starting points in Chinese', () => {
    vi.stubGlobal('window', {
      localStorage: {
        getItem: () => 'zh',
        setItem: () => undefined,
      },
    });
    try {
      const markup = renderToStaticMarkup(<UiLanguageProvider><OceanMindWelcome
        projectName="gulf"
        hasProject
        title=""
        onTitle={() => undefined}
        onCreateTask={() => undefined}
        onChooseProject={() => undefined}
      /></UiLanguageProvider>);

      expect(markup).toContain('你想用 OceanMind 做什么？');
      expect(markup).toContain('检索与阅读论文');
      expect(markup).toContain('分析数据');
      expect(markup).toContain('开展 Autoresearch');
      expect(markup).toContain('placeholder="给这项研究起个名字"');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('shows project-task hierarchy without task runtime status labels', () => {
    const markup = renderToStaticMarkup(<ProjectTaskSidebar
      projects={projects}
      activeProjectPath="/research/gulf"
      activeTaskId="task_1"
      onNewTask={() => undefined}
      onChooseProject={() => undefined}
      onOpenProject={() => undefined}
      onRemoveProject={() => undefined}
      onOpenTask={() => undefined}
      onDeleteTask={() => undefined}
      onOpenSettings={() => undefined}
    />);

    expect(markup).toContain('gulf');
    expect(markup).toContain('分析温盐结构');
    expect(markup).toContain('Add local project');
    expect(markup).toContain('New task in gulf');
    expect(markup).toContain('Remove project gulf');
    expect(markup).not.toContain('<span>Project</span>');
    expect(markup).not.toContain('<span>Task</span>');
    expect(markup).toContain('aria-label="Delete 分析温盐结构"');
    expect(markup).not.toContain('New research');
    expect(markup).not.toContain('Working');
    expect(markup).not.toContain('>active<');
  });

  it('explains that removing a project leaves local research files intact', () => {
    const markup = renderToStaticMarkup(<ProjectRemoveDialog
      project={projects[0]!}
      onCancel={() => undefined}
      onConfirm={() => undefined}
    />);

    expect(markup).toContain('Remove this project?');
    expect(markup).toContain('local folder, research tasks, data, and results stay on disk');
  });
});
