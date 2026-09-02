import type { ReactNode } from 'react';

/**
 * Wrap every case-insensitive occurrence of each token in a <mark>. Used by the
 * feed cards and the map search dropdown so both highlight the same way.
 */
export function highlightText(
  text: string,
  tokens: readonly string[],
  className = 'feed-highlight',
): ReactNode {
  if (tokens.length === 0) return text;

  const lower = text.toLowerCase();
  const marks: boolean[] = new Array(text.length).fill(false);

  for (const tok of tokens) {
    if (!tok) continue;
    let start = 0;
    for (;;) {
      const idx = lower.indexOf(tok, start);
      if (idx === -1) break;
      for (let i = idx; i < idx + tok.length; i++) marks[i] = true;
      start = idx + 1;
    }
  }

  if (!marks.some(Boolean)) return text;

  const parts: ReactNode[] = [];
  let i = 0;
  while (i < text.length) {
    const marked = marks[i];
    let j = i;
    while (j < text.length && marks[j] === marked) j++;
    const slice = text.slice(i, j);
    parts.push(
      marked ? (
        <mark key={i} className={className}>
          {slice}
        </mark>
      ) : (
        slice
      ),
    );
    i = j;
  }
  return <>{parts}</>;
}

export type ExcerptOptions = {
  /** Target length of the excerpt in characters. */
  maxLen?: number;
  /** Marker for trimmed text at either end. */
  ellipsis?: string;
};

/**
 * A window of roughly `maxLen` characters around the earliest token match,
 * cut on word boundaries. With no match (or an early one) it is a plain prefix.
 */
export function excerptAround(
  text: string,
  tokens: readonly string[],
  { maxLen = 120, ellipsis = '…' }: ExcerptOptions = {},
): string {
  if (text.length <= maxLen) return text;

  const lower = text.toLowerCase();
  let earliest = -1;
  for (const tok of tokens) {
    if (!tok) continue;
    const idx = lower.indexOf(tok);
    if (idx !== -1 && (earliest === -1 || idx < earliest)) earliest = idx;
  }

  if (earliest === -1 || earliest <= maxLen / 2) {
    const end = text.lastIndexOf(' ', maxLen);
    return text.slice(0, end > 0 ? end : maxLen) + ellipsis;
  }

  const start = Math.max(0, earliest - Math.floor(maxLen / 3));
  const wordStart = start === 0 ? 0 : text.indexOf(' ', start) + 1;
  const end = Math.min(text.length, wordStart + maxLen);
  const wordEnd = end >= text.length ? text.length : text.lastIndexOf(' ', end);
  const slice = text.slice(wordStart, wordEnd > wordStart ? wordEnd : end);
  return (wordStart > 0 ? ellipsis : '') + slice + (wordEnd < text.length ? ellipsis : '');
}
