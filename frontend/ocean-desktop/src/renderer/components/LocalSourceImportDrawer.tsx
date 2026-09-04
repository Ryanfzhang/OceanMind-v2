import {Database, FileText, X} from 'lucide-react';
import {useUiLanguage} from '../i18n.js';

type PendingLocalImport = {kind: 'dataset' | 'paper'; relativePath?: string; localPath?: string};

export function LocalSourceImportDrawer({
  pending,
  title,
  authors,
  year,
  doi,
  busy,
  onTitle,
  onAuthors,
  onYear,
  onDoi,
  onCancel,
  onConfirm,
}: {
  pending: PendingLocalImport | null;
  title: string;
  authors: string;
  year: string;
  doi: string;
  busy: boolean;
  onTitle: (value: string) => void;
  onAuthors: (value: string) => void;
  onYear: (value: string) => void;
  onDoi: (value: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}): React.JSX.Element | null {
  const {text} = useUiLanguage();
  if (!pending) return null;
  const isPaper = pending.kind === 'paper';
  const label = isPaper ? text('paper', '论文') : text('dataset', '数据集');
  const sourcePath = pending.relativePath ?? pending.localPath ?? '';
  const sourceName = sourcePath.split(/[\\/]/).filter(Boolean).at(-1) ?? sourcePath;

  return <form
    className="local-source-drawer"
    data-kind={pending.kind}
    aria-label={`Import local ${label}`}
    onSubmit={(event) => {event.preventDefault(); onConfirm();}}
  >
    <header className="local-source-summary">
      <span className="local-source-drawer-icon">{isPaper ? <FileText size={17} /> : <Database size={17} />}</span>
      <span className="local-source-identity"><strong title={sourcePath}>{sourceName}</strong></span>
      <button type="button" className="local-source-drawer-close" onClick={onCancel} disabled={busy} title={text('Cancel import', '取消导入')} aria-label={text('Cancel import', '取消导入')}><X size={15} /></button>
    </header>
    <div className="local-source-drawer-body">
      <div className="local-source-fields">
        <label className="local-source-title"><input autoFocus value={title} aria-label={text('Display name', '显示名称')} onChange={(event) => onTitle(event.target.value)} /></label>
        {isPaper ? <>
          <label><span>{text('Authors', '作者')}</span><input value={authors} onChange={(event) => onAuthors(event.target.value)} placeholder={text('Comma-separated', '用逗号分隔')} /></label>
          <label><span>{text('Year', '年份')}</span><input value={year} inputMode="numeric" onChange={(event) => onYear(event.target.value)} /></label>
          <label><span>DOI</span><input value={doi} onChange={(event) => onDoi(event.target.value)} /></label>
        </> : null}
      </div>
      <footer>
        <button type="button" className="local-source-cancel" onClick={onCancel} disabled={busy}>{text('Cancel', '取消')}</button>
        <button type="submit" className="local-source-confirm" disabled={busy || !title.trim()}>{busy ? text('Adding…', '正在添加…') : text('Add to task', '添加到任务')}</button>
      </footer>
    </div>
  </form>;
}
