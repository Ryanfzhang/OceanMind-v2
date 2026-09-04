import {useEffect, useState} from 'react';
import {CloudDownload, Cog, ShieldCheck, X} from 'lucide-react';

import type {DesktopUpdateStatus, ModelProviderSetup, ModelProviderStatus, ModelRoleProviderSetup, ModelRoleProviderStatus} from '../../../shared/bridge.js';
import {useUiLanguage} from '../../i18n.js';
import {editableModelId, isConcreteModelId, isModelProviderReady} from '../../model-provider.js';
import type {AppearanceTheme, DesktopRuntimeCapabilities, DisplayDensity} from '../../types.js';
import {ModalDialog} from './ModalDialog.js';

function ModelProviderSettings({
  status,
  saving,
  onSave,
}: {
  status: ModelProviderStatus | null;
  saving: boolean;
  onSave: (setup: ModelProviderSetup) => void;
}): React.JSX.Element {
  const {text} = useUiLanguage();
  type RoleKey = keyof Pick<ModelProviderSetup, 'coordinator' | 'expert' | 'skillCurator'>;
  type EditableRole = ModelRoleProviderSetup & {baseUrl: string; apiKey: string};
  const editable = (value?: ModelRoleProviderStatus): EditableRole => ({
    provider: value?.provider === 'anthropic' ? 'anthropic' : 'openai',
    model: editableModelId(value?.model),
    baseUrl: value?.baseUrl ?? '',
    apiKey: '',
  });
  const [roles, setRoles] = useState<Record<RoleKey, EditableRole>>({
    coordinator: editable(status?.coordinator), expert: editable(status?.expert), skillCurator: editable(status?.skillCurator),
  });
  useEffect(() => {
    setRoles({coordinator: editable(status?.coordinator), expert: editable(status?.expert), skillCurator: editable(status?.skillCurator)});
  }, [status]);
  const modelReady = isModelProviderReady(status);
  const roleStatus = (key: RoleKey) => status?.[key];
  const readyToSave = !saving && (Object.keys(roles) as RoleKey[]).every((key) => isConcreteModelId(roles[key].model) && (roles[key].apiKey.trim() || roleStatus(key)?.configured));
  const updateRole = (key: RoleKey, patch: Partial<EditableRole>) => setRoles((current) => ({...current, [key]: {...current[key], ...patch}}));
  const specs: Array<{key: RoleKey; title: string; description: string}> = [
    {key: 'coordinator', title: text('Coordinator', '协调者'), description: text('Strong planning, delegation, and synthesis', '负责高质量规划、委派与综合')},
    {key: 'expert', title: text('Experts', '专家'), description: text('Agentic analysis, tools, and code execution', '负责工具调用、分析与代码执行')},
    {key: 'skillCurator', title: text('Skill Curator', '技能策展者'), description: text('Independent review of reusable experience', '独立审批可复用经验')},
  ];
  return <section className="settings-section settings-model-provider" aria-label="Model provider">
    <div className="settings-section-heading"><div><h2>{text('Role APIs', '角色 API')}</h2><p className="settings-section-summary">{text('Assign a separate provider, endpoint, key, and model to each role.', '为每个角色分别指定服务商、地址、密钥与模型。')}</p></div><span className={modelReady ? 'settings-state ready' : 'settings-state'}>{modelReady ? text('Ready', '已就绪') : text('Setup needed', '需要配置')}</span></div>
    <form className="role-api-form" onSubmit={(event) => {event.preventDefault(); if (readyToSave) onSave({
      coordinator: {...roles.coordinator, model: roles.coordinator.model.trim(), baseUrl: roles.coordinator.baseUrl.trim() || undefined, apiKey: roles.coordinator.apiKey || undefined},
      expert: {...roles.expert, model: roles.expert.model.trim(), baseUrl: roles.expert.baseUrl.trim() || undefined, apiKey: roles.expert.apiKey || undefined},
      skillCurator: {...roles.skillCurator, model: roles.skillCurator.model.trim(), baseUrl: roles.skillCurator.baseUrl.trim() || undefined, apiKey: roles.skillCurator.apiKey || undefined},
    });}}>
      <div className="role-api-grid">{specs.map(({key, title, description}) => {
        const role = roles[key]; const configured = roleStatus(key)?.configured;
        return <div className="role-api-card" key={key}>
          <header><strong>{title}</strong><span>{description}</span></header>
          <div className="role-api-fields">
            <label>{text('Provider', '服务商')}<select value={role.provider} disabled={saving} onChange={(event) => updateRole(key, {provider: event.target.value as ModelRoleProviderSetup['provider']})}><option value="openai">OpenAI-compatible</option><option value="anthropic">Anthropic-compatible</option></select></label>
            <label>{text('Model', '模型')}<input value={role.model} disabled={saving} placeholder={role.provider === 'openai' ? 'gpt-5.4' : 'claude-sonnet-4-6'} onChange={(event) => updateRole(key, {model: event.target.value})} /></label>
            <label><span>{text('Endpoint', '接口地址')}<small>{text('Optional', '可选')}</small></span><input value={role.baseUrl} disabled={saving} inputMode="url" placeholder={role.provider === 'openai' ? 'https://api.openai.com/v1' : 'https://api.anthropic.com'} onChange={(event) => updateRole(key, {baseUrl: event.target.value})} /></label>
            <label><span>{text('API key', 'API 密钥')}<small>{configured ? text('Blank keeps current key', '留空保留当前密钥') : text('Required', '必填')}</small></span><input type="password" autoComplete="new-password" value={role.apiKey} disabled={saving} onChange={(event) => updateRole(key, {apiKey: event.target.value})} /></label>
          </div>
        </div>;
      })}</div>
      <button className="settings-disclosure-button role-api-save" type="submit" disabled={!readyToSave}><ShieldCheck size={14} />{saving ? text('Saving APIs...', '正在保存 API…') : text('Save role APIs', '保存角色 API')}</button>
    </form>
  </section>;
}

export function DesktopSettingsDialog({
  open,
  status,
  projectName,
  runtime,
  modelProvider,
  modelProviderSaving,
  displayDensity,
  onDisplayDensity,
  appearanceTheme,
  onAppearanceTheme,
  onConfigureModelProvider,
  update,
  onCheckForUpdate,
  onInstallUpdate,
  onClose,
}: {
  open: boolean;
  status: string;
  projectName: string;
  runtime: DesktopRuntimeCapabilities | null;
  modelProvider: ModelProviderStatus | null;
  modelProviderSaving: boolean;
  displayDensity: DisplayDensity;
  onDisplayDensity: (density: DisplayDensity) => void;
  appearanceTheme: AppearanceTheme;
  onAppearanceTheme: (theme: AppearanceTheme) => void;
  onConfigureModelProvider: (setup: ModelProviderSetup) => void;
  update: DesktopUpdateStatus;
  onCheckForUpdate: () => void;
  onInstallUpdate: () => void;
  onClose: () => void;
}): React.JSX.Element | null {
  const {language, setLanguage, text} = useUiLanguage();
  if (!open) return null;
  const availableConnections = runtime?.connections.filter((connection) => connection.available).length ?? 0;
  const updateState = update.state === 'checking'
    ? text('Checking and downloading', '正在检查并下载')
    : update.state === 'prepared'
      ? text(`Version ${update.version ?? 'update'} is ready`, `版本 ${update.version ?? 'update'} 已准备就绪`)
      : update.state === 'installing'
        ? text('Restarting to install', '正在重启并安装')
        : update.state === 'failed'
          ? text('Update check failed', '更新检查失败')
          : text('No update checked yet', '尚未检查更新');
  const updateHandoffState = update.lastInstall?.state === 'applied'
    ? text(`Version ${update.lastInstall.version ?? 'update'} started after the last update.`, `上次更新后已启动版本 ${update.lastInstall.version ?? 'update'}。`)
    : update.lastInstall?.state === 'previous_runtime_resumed'
      ? text(`Previous version ${update.lastInstall.version ?? 'app'} resumed after the last update handoff.`, `更新交接后恢复了旧版本 ${update.lastInstall.version ?? 'app'}。`)
      : update.lastInstall?.state === 'unexpected_runtime'
        ? text('The last update handoff started an unexpected runtime.', '上次更新交接启动了非预期运行时。')
        : update.lastInstall?.state === 'discarded'
          ? text('An invalid pending update record was discarded.', '已丢弃无效的待安装更新记录。')
          : null;
  return <ModalDialog ariaLabel={text('Settings', '设置')} className="desktop-settings-dialog" onClose={onClose}>
    <header><div><Cog size={17} /><strong>{text('Settings', '设置')}</strong></div><button data-dialog-dismiss onClick={onClose} title={text('Close settings', '关闭设置')} aria-label={text('Close settings', '关闭设置')}><X size={15} /></button></header>
    <section className="settings-section" aria-label="Connection"><h2>{text('Connection', '连接')}</h2><dl className="settings-facts"><div><dt>{text('Backend', '后端')}</dt><dd><i className={status === 'Ready' || status === '已就绪' ? 'ready' : ''} aria-hidden="true" />{status}</dd></div><div><dt>{text('Project', '项目')}</dt><dd title={projectName}>{projectName}</dd></div></dl></section>
    <ModelProviderSettings status={modelProvider} saving={modelProviderSaving} onSave={onConfigureModelProvider} />
    <section className="settings-section" aria-label="Research runtime"><h2>{text('Research runtime', '研究运行时')}</h2>{runtime ? <><p className="settings-section-summary">{text(`${availableConnections} of ${runtime.connections.length} connections available`, `${runtime.connections.length} 个连接中有 ${availableConnections} 个可用`)}</p><ul className="settings-runtime-list" aria-label="Research runtime connections">{runtime.connections.map((connection) => <li key={connection.id}><span><i className={connection.available ? 'ready' : ''} aria-hidden="true" />{connection.label}</span><small>{connection.available ? text('Available', '可用') : text('Unavailable', '不可用')}</small></li>)}</ul></> : <p className="settings-section-summary">{text('Awaiting backend capabilities', '正在等待后端能力信息')}</p>}</section>
    <section className="settings-section" aria-label="Appearance"><h2>{text('Appearance and language', '外观与语言')}</h2><div className="settings-appearance-control"><span>{text('Interface language', '界面语言')}</span><div className="settings-density" role="group" aria-label="Interface language"><button className={language === 'en' ? 'active' : ''} aria-pressed={language === 'en'} onClick={() => setLanguage('en')}>English</button><button className={language === 'zh' ? 'active' : ''} aria-pressed={language === 'zh'} onClick={() => setLanguage('zh')}>中文</button></div></div><div className="settings-appearance-control"><span>{text('Color theme', '颜色主题')}</span><div className="settings-density" role="group" aria-label="Color theme"><button className={appearanceTheme === 'system' ? 'active' : ''} aria-pressed={appearanceTheme === 'system'} onClick={() => onAppearanceTheme('system')}>{text('System', '跟随系统')}</button><button className={appearanceTheme === 'light' ? 'active' : ''} aria-pressed={appearanceTheme === 'light'} onClick={() => onAppearanceTheme('light')}>{text('Light', '浅色')}</button><button className={appearanceTheme === 'dark' ? 'active' : ''} aria-pressed={appearanceTheme === 'dark'} onClick={() => onAppearanceTheme('dark')}>{text('Dark', '深色')}</button></div></div><div className="settings-appearance-control"><span>{text('Interface density', '界面密度')}</span><div className="settings-density" role="group" aria-label="Interface density"><button className={displayDensity === 'comfortable' ? 'active' : ''} aria-pressed={displayDensity === 'comfortable'} onClick={() => onDisplayDensity('comfortable')}>{text('Comfortable', '舒适')}</button><button className={displayDensity === 'compact' ? 'active' : ''} aria-pressed={displayDensity === 'compact'} onClick={() => onDisplayDensity('compact')}>{text('Compact', '紧凑')}</button></div></div></section>
    {update.configured ? <section className="settings-section settings-update" aria-label="Desktop update"><h2>{text('Desktop update', '桌面端更新')}</h2><p className="settings-section-summary">{updateState}</p>{updateHandoffState ? <p className="settings-section-summary">{updateHandoffState}</p> : null}{update.state === 'prepared' ? <button className="settings-disclosure-button" onClick={onInstallUpdate}><CloudDownload size={14} />{text('Restart and install', '重启并安装')}</button> : <button className="settings-disclosure-button" onClick={onCheckForUpdate} disabled={update.state === 'checking' || update.state === 'installing'}><CloudDownload size={14} />{text('Check for update', '检查更新')}</button>}</section> : null}
  </ModalDialog>;
}
