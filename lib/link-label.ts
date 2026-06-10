export function linkLabel(url: string): { label: string; icon: string } {
  try {
    const host = new URL(url).hostname.replace(/^www\./, '');
    if (host.includes('eventbrite.com')) return { label: 'Eventbrite', icon: '🎟' };
    if (host.includes('facebook.com')) return { label: 'Facebook', icon: '📘' };
    if (host.includes('instagram.com')) return { label: 'Instagram', icon: '📷' };
    if (host.includes('tickeri.com')) return { label: 'Tickeri', icon: '🎫' };
    if (host.includes('humanitix.com')) return { label: 'Humanitix', icon: '🎟' };
    if (host.includes('resy.com')) return { label: 'Resy', icon: '🍽' };
    if (host.includes('danceplace.com')) return { label: 'DancePlace', icon: '💃' };
    if (host.includes('metamovements.com')) return { label: 'MetaMovements', icon: '🌀' };
    if (host.includes('listerevents.com')) return { label: 'Lister Events', icon: '📅' };
    if (host.includes('tunehatch.com')) return { label: 'TuneHatch', icon: '🎵' };
    const short = host.length > 20 ? host.slice(0, 18) + '...' : host;
    return { label: short, icon: '🔗' };
  } catch {
    return { label: 'Event Link', icon: '🔗' };
  }
}

export function collectEventLinks(event: { url: string | null; urls?: string[] }): { url: string; label: string; icon: string }[] {
  const links: { url: string; label: string; icon: string }[] = [];
  const seenHosts = new Set<string>();

  function addLink(u: string) {
    try {
      const host = new URL(u).hostname.replace(/^www\./, '');
      if (seenHosts.has(host)) return;
      seenHosts.add(host);
    } catch { /* keep link even if URL parsing fails */ }
    links.push({ url: u, ...linkLabel(u) });
  }

  if (event.url) addLink(event.url);
  for (const extra of event.urls ?? []) {
    if (extra && extra !== event.url) addLink(extra);
  }
  return links;
}
