import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { excerptAround, highlightText } from '@/lib/highlight';

const render = (node: ReturnType<typeof highlightText>) => renderToStaticMarkup(<>{node}</>);

describe('highlightText', () => {
  it('returns the text untouched with no tokens or no match', () => {
    expect(highlightText('Salsa Night', [])).toBe('Salsa Night');
    expect(highlightText('Salsa Night', ['kizomba'])).toBe('Salsa Night');
  });

  it('wraps each case-insensitive match in a mark', () => {
    expect(render(highlightText('Salsa Night in Boston', ['salsa', 'boston']))).toBe(
      '<mark class="feed-highlight">Salsa</mark> Night in <mark class="feed-highlight">Boston</mark>',
    );
  });

  it('merges overlapping token matches into one mark', () => {
    expect(render(highlightText('bachata', ['bach', 'chata']))).toBe(
      '<mark class="feed-highlight">bachata</mark>',
    );
  });

  it('marks every occurrence of a token', () => {
    expect(render(highlightText('salsa salsa', ['salsa']))).toBe(
      '<mark class="feed-highlight">salsa</mark> <mark class="feed-highlight">salsa</mark>',
    );
  });

  it('takes a custom class name', () => {
    expect(render(highlightText('Salsa', ['salsa'], 'hit'))).toBe('<mark class="hit">Salsa</mark>');
  });
});

describe('excerptAround', () => {
  const long =
    `${'lorem ipsum '.repeat(30)}needle in the haystack ${'dolor sit '.repeat(30)}`.trim();

  it('returns short text as-is', () => {
    expect(excerptAround('Short text', ['needle'])).toBe('Short text');
  });

  it('cuts a plain prefix on a word boundary when nothing matches', () => {
    const out = excerptAround(long, ['zzz'], { maxLen: 40 });
    expect(out.endsWith('…')).toBe(true);
    expect(out.length).toBeLessThanOrEqual(41);
    expect(out).not.toContain('needle');
    expect(long.startsWith(out.slice(0, -1))).toBe(true);
  });

  it('windows around a late match and marks both trimmed ends', () => {
    const out = excerptAround(long, ['needle'], { maxLen: 60 });
    expect(out).toContain('needle');
    expect(out.startsWith('…')).toBe(true);
    expect(out.endsWith('…')).toBe(true);
    expect(out.length).toBeLessThanOrEqual(62);
  });

  it('honours the ellipsis option', () => {
    expect(excerptAround(long, ['zzz'], { maxLen: 40, ellipsis: '...' }).endsWith('...')).toBe(
      true,
    );
    expect(excerptAround(long, ['needle'], { maxLen: 60, ellipsis: '...' }).startsWith('...')).toBe(
      true,
    );
  });

  it('defaults to a 120-character window', () => {
    expect(excerptAround(long, ['zzz']).length).toBeLessThanOrEqual(121);
  });
});
