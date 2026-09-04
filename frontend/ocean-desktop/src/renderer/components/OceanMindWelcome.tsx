import {BookOpen, Database, FolderOpen, RefreshCw, SendHorizontal} from 'lucide-react';

import coreLogoUrl from '../assets/core-hkust-logo.png';
import {useUiLanguage} from '../i18n.js';

const CORE_HOME_URL = 'https://core-hkmacau.hkust.edu.hk/';

export function OceanMindWelcome({
  hasProject,
  title,
  onTitle,
  onCreateTask,
  onChooseProject,
}: {
  projectName: string;
  hasProject: boolean;
  title: string;
  onTitle: (value: string) => void;
  onCreateTask: () => void;
  onChooseProject: () => void;
}): React.JSX.Element {
  const {text} = useUiLanguage();
  const entryPoints = [
    {
      kind: 'papers',
      label: text('Find and review papers', '检索与阅读论文'),
      icon: <BookOpen size={20} aria-hidden="true" />,
    },
    {
      kind: 'data',
      label: text('Analyze data', '分析数据'),
      icon: <Database size={20} aria-hidden="true" />,
    },
    {
      kind: 'autoresearch',
      label: text('Run Autoresearch', '开展 Autoresearch'),
      icon: <RefreshCw size={20} aria-hidden="true" />,
    },
  ] as const;
  return <section className="ocean-welcome" aria-label="Welcome to OceanMind">
    <a
      className="welcome-ack"
      href={CORE_HOME_URL}
      target="_blank"
      rel="noreferrer noopener"
      title={text('Center for Ocean Research in Hong Kong and Macau (CORE), HKUST', '港澳海洋研究中心（CORE）· 香港科技大学')}
    >
      <img src={coreLogoUrl} alt={text('CORE · HKUST', '港澳海洋研究中心 · 香港科技大学')} />
      <span>{text('Supported by CORE, HKUST', '感谢港澳海洋研究中心（CORE）支持')}</span>
    </a>
    <h1 className="ocean-welcome-question">{text('What would you like to do with OceanMind?', '你想用 OceanMind 做什么？')}</h1>
    {hasProject ? <>
      <div className="welcome-entry-points" aria-label={text('Research starting points', '研究入口')}>
        {entryPoints.map((entry) => <article
          className={`welcome-entry-point ${entry.kind}`}
          key={entry.kind}
        >
          {entry.icon}
          <span>{entry.label}</span>
        </article>)}
      </div>
      <div className="welcome-task-entry">
        <form onSubmit={(event) => {event.preventDefault(); onCreateTask();}}>
          <input
            id="welcome-task-title"
            value={title}
            onChange={(event) => onTitle(event.target.value)}
            placeholder={text('Name this research task', '给这项研究起个名字')}
            aria-label={text('Research task name', '研究任务名称')}
            autoFocus
          />
          <button type="submit" disabled={!title.trim()} title="Create research task"><SendHorizontal size={18} /></button>
        </form>
      </div>
    </> : <button type="button" className="welcome-open-project" onClick={onChooseProject}><FolderOpen size={18} />{text('Choose a local project', '选择一个本地项目')}</button>}
  </section>;
}
