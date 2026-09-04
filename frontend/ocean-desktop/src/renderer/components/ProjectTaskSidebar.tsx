import {Folder, Plus, Settings, Trash2} from 'lucide-react';

import type {ProjectCatalogEntry} from '../project-catalog.js';
import {useUiLanguage} from '../i18n.js';

export function ProjectTaskSidebar({
  projects,
  activeProjectPath,
  activeTaskId,
  onNewTask,
  onChooseProject,
  onOpenProject,
  onRemoveProject,
  onOpenTask,
  onDeleteTask,
  onOpenSettings,
}: {
  projects: ProjectCatalogEntry[];
  activeProjectPath: string | null;
  activeTaskId: string | null;
  onNewTask: (projectPath: string) => void;
  onChooseProject: () => void;
  onOpenProject: (projectPath: string) => void;
  onRemoveProject: (project: ProjectCatalogEntry) => void;
  onOpenTask: (projectPath: string, taskId: string) => void;
  onDeleteTask: (projectPath: string, taskId: string) => void;
  onOpenSettings: () => void;
}): React.JSX.Element {
  const {text} = useUiLanguage();
  return <aside className="task-sidebar">
    <div className="window-drag" />
    <div className="sidebar-brand"><span className="sidebar-brand-mark"><span /><span /><span /></span><strong>OceanMind</strong></div>
    <div className="project-list-heading">
      <span>{text('Projects', '项目')}</span>
      <button className="add-project" type="button" onClick={onChooseProject} title={text('Add local project', '添加本地项目')} aria-label={text('Add local project', '添加本地项目')}>
        <Plus size={14} />
      </button>
    </div>
    <nav className="project-list" aria-label="Projects and research tasks">
      {projects.length ? projects.map((project) => <section className={`project-group${project.path === activeProjectPath ? ' active' : ''}`} key={project.path}>
        <div className="project-heading-row">
          <button className="project-row" type="button" onClick={() => onOpenProject(project.path)} title={project.path}>
            <Folder size={16} />
            <strong>{project.name}</strong>
          </button>
          <div className="project-actions">
            <button
              className="project-new-task"
              type="button"
              onClick={() => onNewTask(project.path)}
              title={text(`New task in ${project.name}`, `在 ${project.name} 中新建任务`)}
              aria-label={text(`New task in ${project.name}`, `在 ${project.name} 中新建任务`)}
            >
              <Plus size={14} />
            </button>
            <button
              className="project-delete"
              type="button"
              onClick={() => onRemoveProject(project)}
              title={text(`Remove ${project.name}`, `移除 ${project.name}`)}
              aria-label={text(`Remove project ${project.name}`, `移除项目 ${project.name}`)}
            ><Trash2 size={12} /></button>
          </div>
        </div>
        <div className="project-tasks">
          {project.tasks.length ? project.tasks.map((task) => <div className={`task-row${task.task_id === activeTaskId && project.path === activeProjectPath ? ' active' : ''}`} key={task.task_id}>
            <button type="button" onClick={() => onOpenTask(project.path, task.task_id)} title={task.title}>{task.title}</button>
            <button type="button" className="task-delete" onClick={() => onDeleteTask(project.path, task.task_id)} title={text('Delete task', '删除任务')} aria-label={`${text('Delete', '删除')} ${task.title}`}><Trash2 size={14} /></button>
          </div>) : <small className="project-empty">{text('No research tasks yet', '还没有研究任务')}</small>}
        </div>
      </section>) : <div className="project-list-empty"><Folder size={20} /><p>{text('Add a local project to keep its research tasks here.', '添加一个本地项目后，研究任务会显示在这里。')}</p></div>}
    </nav>
    <footer><button type="button" onClick={onOpenSettings}><Settings size={16} />{text('Settings', '设置')}</button></footer>
  </aside>;
}
