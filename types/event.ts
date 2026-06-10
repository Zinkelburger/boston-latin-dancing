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
  /** True for past events kept for SEO (not shown on map/feed) */
  archived?: boolean;
  /** Human-readable organizer name (e.g. "BOBAS", "Fiesta Dance Company") */
  organizer?: string;
}

export interface RecurringSchedule {
  dayOfWeek: DayOfWeek;
  time: string;
  note?: string;
}

export interface RecurringVenue {
  id: string;
  slug?: string;
  name: string;
  location: string;
  lat: number;
  lng: number;
  description: string;
  url: string | null;
  styles: DanceStyle[];
  cost: string | null;
  schedule: RecurringSchedule[];
}
