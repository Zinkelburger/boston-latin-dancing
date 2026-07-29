export type DanceStyle = 'bachata' | 'salsa' | 'kizomba' | 'zouk' | 'merengue' | 'other';

export type DayOfWeek =
  | 'Monday' | 'Tuesday' | 'Wednesday'
  | 'Thursday' | 'Friday' | 'Saturday' | 'Sunday';

export interface DanceEvent {
  id: string;
  slug?: string;
  name: string;
  /** ISO datetime string for event start */
  startDate: string;
  /** ISO datetime string for event end */
  endDate: string;
  /** Day of week derived from startDate */
  dayOfWeek: DayOfWeek;
  location: string;
  lat: number | null;
  lng: number | null;
  description: string;
  url: string | null;
  /** Additional source URLs collected when the same event is scraped from multiple sources */
  urls?: string[];
  /** Detected dance styles from title + description */
  styles: DanceStyle[];
  /** Raw cost string extracted from description */
  cost: string | null;
  /** Whether this is a recurring event */
  recurring: boolean;
  /** Human-readable pattern, e.g. "First Sunday of each month" (set at publish) */
  recurrenceLabel?: string;
  /** ISO datetime strings for all known occurrences (present when recurring) */
  recurrences?: string[];
  /** Weekly schedule for venue-style recurring events (e.g. Havana Club) */
  schedule?: RecurringSchedule[];
  /** True when the recurring pattern is irregular and the next date can't be
   *  reliably predicted (e.g. "dates vary, check FB page"). Suppresses the
   *  "Next: ..." label in cards/popups. */
  nextDateApproximate?: boolean;
  /** True for past events kept for SEO (not shown on map/feed) */
  archived?: boolean;
  /** True for dateless venue records (irregular schedules): findable via
   *  search and detail pages, but never a map pin, feed row, or filter hit. */
  searchOnly?: boolean;
  /** Human-readable organizer name (e.g. "BOBAS", "Fiesta Dance Company") */
  organizer?: string;
  /** Big one-off event (festival, annual edition, big outdoor party) —
   *  distinct map pin, ⭐ badge, and the "Big events" filter. Set at publish
   *  by heuristic or an explicit override on the stored event. */
  special?: boolean;
}

export interface RecurringSchedule {
  dayOfWeek: DayOfWeek;
  time: string;
  note?: string;
}

export interface RecurringVenue {
  id: string;
  slug?: string;
  /** Links to the scrape source ID — only events from this source can confirm the venue date */
  sourceId?: string;
  name: string;
  location: string;
  lat: number;
  lng: number;
  description: string;
  url: string | null;
  urls?: string[];
  styles: DanceStyle[];
  cost: string | null;
  schedule: RecurringSchedule[];
  /** True when the recurring pattern is irregular (e.g. weather-dependent, dates vary) */
  nextDateApproximate?: boolean;
  /** Specific YYYY-MM-DD dates to skip when expanding the weekly schedule
   *  (e.g. a Friday taken over by a special-edition event, or a cancellation). */
  excludeDates?: string[];
  /** Override the auto-generated recurrence label */
  recurrenceLabel?: string;
}
