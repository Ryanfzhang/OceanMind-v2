import {FolderMinus, X} from 'lucide-react';

import {useUiLanguage} from '../../i18n.js';
import type {ProjectCatalogEntry} from '../../project-catalog.js';
import {ModalDialog} from './ModalDialog.js';

export function ProjectRemoveDialog({
  project,
  onCancel,
  onConfirm,
}: {
  project: ProjectCatalogEntry | null;
  onCancel: () => void;
  onConfirm: () => void;
}): React.JSX.Element | null {
  const {text} = useUiLanguage();
  if (!project) return null;
  return <ModalDialog ariaLabel={text('Remove project', '移除项目')} onClose={onCancel}>
    <header>
      <div><FolderMinus size={17} /><strong>{text('Remove this project?', '移除这个项目？')}</strong></div>
      <button data-dialog-dismiss onClick={onCancel} title={text('Keep project', '保留项目')} aria-label={text('Keep project', '保留项目')}><X size={15} /></button>
    </header>
    <p className="import-path">{project.name}</p>
    <p className="task-delete-copy">{text(
      'This only removes the project from the OceanMind sidebar. The local folder, research tasks, data, and results stay on disk.',
      '这只会将项目从 OceanMind 侧栏移除。本地文件夹、研究任务、数据和结果仍会保留在磁盘上。',
    )}</p>
    <footer>
      <button data-dialog-dismiss onClick={onCancel}>{text('Cancel', '取消')}</button>
      <button className="primary danger" onClick={onConfirm}>{text('Remove project', '移除项目')}</button>
    </footer>
  </ModalDialog>;
}
