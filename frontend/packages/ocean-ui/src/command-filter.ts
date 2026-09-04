/** Case-insensitive substring filter for command-palette action labels. */
export function filterCommandActions<T extends {label: string}>(actions: T[], query: string): T[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return actions;
  return actions.filter((action) => action.label.toLocaleLowerCase().includes(needle));
}
