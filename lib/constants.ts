import type { DanceStyle } from '@/types/event';

export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://bostonlatindance.com';
export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://api.bostonsalsa.org';

export const STYLE_COLORS: Record<DanceStyle, string> = {
  bachata:  '#e11d48',
  salsa:    '#f59e0b',
  kizomba:  '#8b5cf6',
  zouk:     '#0ea5e9',
  merengue: '#10b981',
  other:    '#6b7280',
};

export const STYLE_LABELS: Record<DanceStyle, string> = {
  bachata:  'Bachata',
  salsa:    'Salsa',
  kizomba:  'Kizomba',
  zouk:     'Zouk',
  merengue: 'Merengue',
  other:    'Other',
};

export const STYLE_PILL_CLASS: Record<DanceStyle, string> = {
  bachata:  'pretty-pill-rose',
  salsa:    'pretty-pill-amber',
  kizomba:  'pretty-pill-violet',
  zouk:     'pretty-pill-sky',
  merengue: 'pretty-pill-emerald',
  other:    'pretty-pill-slate',
};

export const STYLE_SLUGS = Object.keys(STYLE_LABELS) as DanceStyle[];

export const STYLE_DESCRIPTIONS: Record<DanceStyle, string> = {
  bachata:  'Find bachata socials, classes, and events happening around Boston. Updated weekly.',
  salsa:    'Find salsa socials, classes, and events happening around Boston. Updated weekly.',
  kizomba:  'Find kizomba socials, classes, and events happening around Boston. Updated weekly.',
  zouk:     'Find Brazilian zouk socials, classes, and events happening around Boston. Updated weekly.',
  merengue: 'Find merengue socials, classes, and events happening around Boston. Updated weekly.',
  other:    'Find Latin dance socials, classes, and events happening around Boston. Updated weekly.',
};
