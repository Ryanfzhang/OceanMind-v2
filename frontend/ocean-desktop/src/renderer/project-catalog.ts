import {projectNameFromPath} from './app-utils.js';
import type {ResearchTask} from './types.js';

export type ProjectCatalogEntry = {
  path: string;
  name: string;
  tasks: ResearchTask[];
  lastOpenedAt: number;
};

export type RememberedProject = Pick<ProjectCatalogEntry, 'path' | 'name' | 'lastOpenedAt'>;

/** Refresh task metadata without turning an opened task into a pinned task. */
export function upsertTaskPreservingOrder(
  tasks: ResearchTask[],
  incoming: ResearchTask,
): ResearchTask[] {
  const index = tasks.findIndex((task) => task.task_id === incoming.task_id);
  if (index < 0) return [incoming, ...tasks];
  return tasks.map((task, taskIndex) => taskIndex === index ? incoming : task);
}

function isTask(value: unknown): value is ResearchTask {
  if (!value || typeof value !== 'object') return false;
  const task = value as Partial<ResearchTask>;
  return typeof task.task_id === 'string'
    && typeof task.workspace_id === 'string'
    && typeof task.title === 'string'
    && (task.status === 'active' || task.status === 'completed' || task.status === 'archived')
    && typeof task.task_revision === 'number'
    && typeof task.conversation_generation === 'number';
}

export function parseProjectCatalog(raw: string | null): ProjectCatalogEntry[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((value) => {
      if (!value || typeof value !== 'object') return [];
      const entry = value as Partial<ProjectCatalogEntry>;
      if (typeof entry.path !== 'string' || !entry.path.trim()) return [];
      return [{
        path: entry.path,
        name: typeof entry.name === 'string' && entry.name.trim() ? entry.name : projectNameFromPath(entry.path),
        tasks: Array.isArray(entry.tasks) ? entry.tasks.filter(isTask).filter((task) => task.status !== 'archived') : [],
        lastOpenedAt: typeof entry.lastOpenedAt === 'number' ? entry.lastOpenedAt : 0,
      }];
    });
  } catch {
    return [];
  }
}

export function rememberProject(
  catalog: ProjectCatalogEntry[],
  path: string,
  tasks?: ResearchTask[],
  openedAt = Date.now(),
): ProjectCatalogEntry[] {
  const existing = catalog.find((entry) => entry.path === path);
  const next: ProjectCatalogEntry = {
    path,
    name: projectNameFromPath(path),
    tasks: (tasks ?? existing?.tasks ?? []).filter((task) => task.status !== 'archived'),
    lastOpenedAt: openedAt,
  };
  if (!existing) return [next, ...catalog];
  return catalog.map((entry) => entry.path === path ? next : entry);
}

export function forgetProject(catalog: ProjectCatalogEntry[], path: string): ProjectCatalogEntry[] {
  return catalog.filter((entry) => entry.path !== path);
}

export function mergeRememberedProjects(
  catalog: ProjectCatalogEntry[],
  remembered: RememberedProject[],
): ProjectCatalogEntry[] {
  const rememberedByPath = new Map(remembered.map((entry) => [entry.path, entry]));
  const next = catalog.map((entry) => {
    const project = rememberedByPath.get(entry.path);
    if (!project) return entry;
    rememberedByPath.delete(entry.path);
    return {
      ...entry,
      name: project.name.trim() || entry.name,
      lastOpenedAt: Math.max(entry.lastOpenedAt, project.lastOpenedAt),
    };
  });
  for (const project of remembered) {
    if (!project.path.trim() || !rememberedByPath.has(project.path)) continue;
    next.push({
      path: project.path,
      name: project.name.trim() || projectNameFromPath(project.path),
      tasks: [],
      lastOpenedAt: project.lastOpenedAt,
    });
    rememberedByPath.delete(project.path);
  }
  return next;
}
