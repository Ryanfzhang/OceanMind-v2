import {BrainCircuit, ChevronDown} from 'lucide-react';

import {researchSkillLabel} from '../app-utils.js';
import {type DesktopRuntimeCapabilities} from '../types.js';

export function ResearchGuidance({runtime}: {runtime: DesktopRuntimeCapabilities | null}): React.JSX.Element | null {
  if (!runtime) return null;
  const availableConnections = runtime.connections.filter((connection) => connection.available).length;
  return <details className="research-guidance"><summary><span><BrainCircuit size={13} />Research guidance</span><em>{runtime.skills.length + ' manuals · ' + availableConnections + '/' + runtime.connections.length}</em><ChevronDown size={13} /></summary><div className="research-guidance-content"><div className="research-connections" role="group" aria-label="Research connections"><span>Connections</span><div>{runtime.connections.map((connection) => <span key={connection.id} className={connection.available ? 'available' : 'unavailable'} aria-label={connection.label + (connection.available ? ' available' : ' unavailable')} title={connection.label + (connection.available ? ' available' : ' unavailable')}><i aria-hidden="true" />{connection.label}</span>)}</div></div>{runtime.skills.length ? <ul className="research-skill-list" aria-label="Research skills">{runtime.skills.map((skill) => <li key={skill.name} title={skill.version}><strong>{researchSkillLabel(skill.name)}</strong><small>{skill.description}</small></li>)}</ul> : <p className="research-guidance-empty">No research manuals are available from this backend.</p>}</div></details>;
}
