import { describe, expect, it } from 'vitest';
import { cleanDisplayText } from '@/lib/display-text';

const rueda = `RUEDA IN THE PAHK IS BACK! 🎉☀️💃🏽🕺🏽

Join us Sundays for this season of outdoor dancing, community, chaos, laughter, and nonstop rueda magic in Central Square!

🕕 6 PM — FREE Rueda de Casino lesson for all levels
No partner? No experience? No problem! Just show up ready to learn and have fun 😎

🕖 7 PM — Social dancing in the sunshine ‘til sundown

📍 Jill Brown Rhone Park — Central Square, Cambridge

Let’s make the park spin again! ✨

Source: https://www.instagram.com/p/DY5afzhhsK7/`;

describe('cleanDisplayText', () => {
  const cleaned = cleanDisplayText(rueda);

  it('drops the shouty ALL-CAPS opener', () => {
    expect(cleaned).not.toContain('RUEDA IN THE PAHK IS BACK');
  });

  it('drops the Source crumb and its URL', () => {
    expect(cleaned).not.toContain('Source:');
    expect(cleaned).not.toContain('instagram.com');
  });

  it('strips pictographs', () => {
    expect(cleaned).not.toMatch(/[\u{1F300}-\u{1FAFF}]/u);
  });

  it('keeps the body paragraphs and schedule lines', () => {
    expect(cleaned).toContain('Join us Sundays');
    expect(cleaned).toContain('6 PM');
  });

  it('keeps a shout line when it is the only content', () => {
    const onlyShout = cleanDisplayText('SALSA NIGHT IS BACK!!! 🎉🎉🎉');
    expect(onlyShout.toLowerCase()).toContain('salsa night');
  });

  it('is idempotent', () => {
    expect(cleanDisplayText(cleaned)).toBe(cleaned);
  });
});
