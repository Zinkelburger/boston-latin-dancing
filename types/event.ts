export type DanceStyle = 'bachata' | 'salsa' | 'kizomba' | 'zouk' | 'merengue' | 'other';

export type DayOfWeek =
  | 'Monday' | 'Tuesday' | 'Wednesday'
  | 'Thursday' | 'Friday' | 'Saturday' | 'Sunday';

export interface DanceEvent {
  id: string;
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
  /** Detected dance styles from title + description */
  styles: DanceStyle[];
  /** Raw cost string extracted from description */
  cost: string | null;
  /** Whether this is a recurring event */
  recurring: boolean;
}

export interface RecurringSchedule {
  dayOfWeek: DayOfWeek;
  time: string;
  note?: string;
}

export interface RecurringVenue {
  id: string;
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
