export function slugify(name: string, id: string): string {
  const base = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 60);
  const suffix = id.slice(0, 8).toLowerCase();
  return `${base}-${suffix}`;
}
