import { describe, expect, it } from 'vitest';
import { displayStartIso, hasStartDate } from '@/lib/dates';

// Tue 1 Sep 2026, 16:00 EDT (20:00 UTC).
const NOW = Date.UTC(2026, 8, 1, 20);

describe('hasStartDate', () => {
  it('rejects the empty string search-only records ship', () => {
    expect(hasStartDate({ startDate: '' })).toBe(false);
  });

  it('rejects a missing or null startDate', () => {
    expect(hasStartDate({})).toBe(false);
    expect(hasStartDate({ startDate: null })).toBe(false);
  });

  it('accepts a non-empty ISO string', () => {
    expect(hasStartDate({ startDate: '2026-09-05T23:00:00Z' })).toBe(true);
  });
});

describe('displayStartIso', () => {
  it('returns startDate for a one-off event, even one in the past', () => {
    expect(displayStartIso({ startDate: '2026-08-15T23:00:00Z' }, NOW)).toBe(
      '2026-08-15T23:00:00Z',
    );
    expect(displayStartIso({ startDate: '2026-09-20T23:00:00Z' }, NOW)).toBe(
      '2026-09-20T23:00:00Z',
    );
  });

  it('rolls a recurring series forward to its next occurrence', () => {
    const event = {
      startDate: '2026-08-15T23:00:00Z',
      recurrences: ['2026-08-15T23:00:00Z', '2026-09-05T23:00:00Z', '2026-09-12T23:00:00Z'],
    };
    expect(displayStartIso(event, NOW)).toBe('2026-09-05T23:00:00Z');
  });

  it('picks the earliest upcoming occurrence regardless of list order', () => {
    const event = {
      startDate: '2026-08-15T23:00:00Z',
      recurrences: ['2026-09-12T23:00:00Z', '2026-09-05T23:00:00Z', '2026-08-15T23:00:00Z'],
    };
    expect(displayStartIso(event, NOW)).toBe('2026-09-05T23:00:00Z');
  });

  it('counts an occurrence earlier today (Boston) as still upcoming', () => {
    // 10:00 EDT on 1 Sep — six hours before NOW, but the same Boston day.
    const event = { startDate: '2026-08-25T14:00:00Z', recurrences: ['2026-09-01T14:00:00Z'] };
    expect(displayStartIso(event, NOW)).toBe('2026-09-01T14:00:00Z');
  });

  it('falls back to startDate when every occurrence has passed', () => {
    const event = {
      startDate: '2026-08-01T23:00:00Z',
      recurrences: ['2026-08-01T23:00:00Z', '2026-08-08T23:00:00Z'],
    };
    expect(displayStartIso(event, NOW)).toBe('2026-08-01T23:00:00Z');
  });

  it('ignores unparseable entries', () => {
    const event = { startDate: '2026-09-20T23:00:00Z', recurrences: ['not a date'] };
    expect(displayStartIso(event, NOW)).toBe('2026-09-20T23:00:00Z');
  });
});
