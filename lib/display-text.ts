import { stripHtml } from './strip-html';

/** Pictographs, variation selectors, and ZWJ — not useful as UI chrome. */
const PICTOGRAPH = /[\p{Extended_Pictographic}\p{Emoji_Presentation}\uFE0F\u200D]/gu;

const LEADING_DECOR =
  /^(?:[\s*#_~\-•·.]+|[\p{Extended_Pictographic}\p{Emoji_Presentation}\uFE0F\u200D]+)+/u;
const TRAILING_DECOR =
  /(?:[\s*#_~\-•·.]+|[\p{Extended_Pictographic}\p{Emoji_Presentation}\uFE0F\u200D]+)+$/u;

function stripLineDecor(line: string): string {
  return line
    .replace(LEADING_DECOR, '')
    .replace(TRAILING_DECOR, '')
    .replace(PICTOGRAPH, '')
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
}

/** ALL-CAPS promo lines (emoji walls already stripped). */
function isShoutLine(line: string): boolean {
  const letters = (line.match(/[A-Za-z]/g) || []).length;
  if (letters < 8) return false;
  const upper = (line.match(/[A-Z]/g) || []).length;
  return upper / letters > 0.65;
}

/**
 * Reader-facing copy: drop Source/Website crumbs, leading pictographs, and
 * shouty ALL-CAPS openers. Search matching still uses the raw description.
 */
export function cleanDisplayText(raw: string): string {
  let text = stripHtml(raw);
  text = text.replace(/(?:^|\n)\s*Source:\s*\S+[^\n]*/gi, '\n');
  text = text.replace(/(?:^|\n)\s*Website:\s*/gi, '\n');
  text = text.replace(/(?:^|\n)\s*Organized by\s+\S+[^\n]*/gi, '\n');

  const processed: string[] = [];
  for (const rawLine of text.split('\n')) {
    if (!rawLine.trim()) {
      if (processed.length > 0 && processed[processed.length - 1] !== '') {
        processed.push('');
      }
      continue;
    }
    const line = stripLineDecor(rawLine);
    if (line) processed.push(line);
  }

  const kept = processed.filter(line => line === '' || !isShoutLine(line));
  const usable = kept.some(line => line !== '') ? kept : processed;

  return usable
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
