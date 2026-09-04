import {Trash2, X} from 'lucide-react';

import type {ResearchTask} from '../../types.js';
import {useUiLanguage} from '../../i18n.js';
import {ModalDialog} from './ModalDialog.js';

export function TaskDeleteDialog({
  task,
  onCancel,
  onConfirm,
}: {
  task: ResearchTask | null;
  onCancel: () => void;
  onConfirm: () => void;
}): React.JSX.Element | null {
  const {text} = useUiLanguage();
  if (!task) return null;
  return <ModalDialog ariaLabel={text('Delete research task', '删除研究任务')} onClose={onCancel}><header><div><Trash2 size={17} /><strong>{text('Delete this research task?', '删除这个研究任务？')}</strong></div><button data-dialog-dismiss onClick={onCancel} title={text('Keep task', '保留任务')} aria-label={text('Keep task', '保留任务')}><X size={15} /></button></header><p className="import-path">{task.title}</p><p className="task-delete-copy">{text("Deleting removes this task's conversation and checkpoints. Workspace sources and published reports or views are kept.", '删除会移除该任务的对话和检查点，但保留项目来源以及已发布的报告和视图。')}</p><footer><button data-dialog-dismiss onClick={onCancel}>{text('Cancel', '取消')}</button><button className="primary danger" onClick={onConfirm}>{text('Delete task', '删除任务')}</button></footer></ModalDialog>;
}
