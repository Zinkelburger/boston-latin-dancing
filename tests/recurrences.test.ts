import { afterEach, describe, expect, it, vi } from 'vitest';
import { matchesDay, occurrenceMatchesDays } from '@/lib/recurrences';

// Boston midnight, Tue 1 Sep 2026 (EDT is UTC-4) through the end of Mon 14 Sep.
const FROM = Date.UTC(2026, 8, 1, 4);
const TO = Date.UTC(2026, 8, 15, 4) - 1;
const range = { fromMs: FROM, toMs: TO };

const FRI_SEP_4 = '2026-09-04T23:00:00Z';
const SAT_SEP_12 = '2026-09-12T23:00:00Z';
const SAT_SEP_19 = '2026-09-19T23:00:00Z';

describe('occurrenceMatchesDays', () => {
  it('matches everything when no day is selected', () => {
    expect(occurrenceMatchesDays(FRI_SEP_4, [])).toBe(true);
  });

  it('judges the weekday in Boston time, not UTC', () => {
    // 02:30 UTC on Sunday 6 Sep is 22:30 EDT on Saturday 5 Sep.
    const lateSaturday = '2026-09-06T02:30:00Z';
    expect(occurrenceMatchesDays(lateSaturday, ['Saturday'])).toBe(true);
    expect(occurrenceMatchesDays(lateSaturday, ['Sunday'])).toBe(false);
  });
});

describe('matchesDay', () => {
  const series = {
    startDate: FRI_SEP_4,
    recurrences: [FRI_SEP_4, SAT_SEP_12],
  };

  it('passes every event when no day is selected', () => {
    expect(matchesDay(series, [], range)).toBe(true);
    expect(matchesDay({ startDate: '' }, [], range)).toBe(true);
  });

  it('matches on any occurrence in the window, not just startDate', () => {
    // The map used to test startDate's weekday (Friday) alone and hid this
    // series under a Saturday filter while the feed listed its Saturday date.
    expect(matchesDay(series, ['Saturday'], range)).toBe(true);
    expect(matchesDay(series, ['Friday'], range)).toBe(true);
    expect(matchesDay(series, ['Monday'], range)).toBe(false);
  });

  it('ignores occurrences outside the window', () => {
    const later = { startDate: FRI_SEP_4, recurrences: [FRI_SEP_4, SAT_SEP_19] };
    expect(matchesDay(later, ['Saturday'], range)).toBe(false);
  });

  it('uses startDate for a one-off event', () => {
    expect(matchesDay({ startDate: SAT_SEP_12 }, ['Saturday'], range)).toBe(true);
    expect(matchesDay({ startDate: SAT_SEP_12 }, ['Friday'], range)).toBe(false);
  });

  it('never matches a dateless search-only record once a day is selected', () => {
    expect(matchesDay({ startDate: '' }, ['Saturday'], range)).toBe(false);
  });

  describe('with an approximate next date', () => {
    afterEach(() => vi.useRealTimers());

    it('only counts the single surfaced occurrence', () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date(FROM));
      const approx = { ...series, nextDateApproximate: true };
      expect(matchesDay(approx, ['Friday'], range)).toBe(true);
      expect(matchesDay(approx, ['Saturday'], range)).toBe(false);
    });
  });
});
