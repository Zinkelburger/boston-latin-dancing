'use client';

import { useState, useCallback, type FormEvent } from 'react';
import Link from 'next/link';
import TurnstileWidget from '@/app/components/TurnstileWidget';
import { API_URL, STYLE_LABELS, STYLE_PILL_CLASS } from '@/lib/constants';
import type { DanceStyle } from '@/types/event';

const ALL_STYLES = Object.keys(STYLE_LABELS) as DanceStyle[];

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'] as const;
const DAY_SHORT: Record<string, string> = {
  Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed',
  Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat', Sunday: 'Sun',
};

const WEEK_OPTIONS = ['1st', '2nd', '3rd', '4th', 'Last'] as const;

type SubmitState = 'idle' | 'submitting' | 'success' | 'error';

function recurrenceSummary(type: string, day: string, week: string): string {
  if (!type || !day) return '';
  if (type === 'weekly') return `Every ${day}`;
  if (type === 'biweekly') return `Every other ${day}`;
  if (type === 'monthly' && week) return `${week} ${day} of the month`;
  return '';
}

export default function SubmitClient() {
  const [email, setEmail] = useState('');
  const [instagram, setInstagram] = useState('');
  const [eventName, setEventName] = useState('');
  const [eventUrl, setEventUrl] = useState('');
  const [styles, setStyles] = useState<DanceStyle[]>([]);
  const [location, setLocation] = useState('');
  const [isRecurring, setIsRecurring] = useState(false);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [recurrenceType, setRecurrenceType] = useState('');
  const [dayOfWeek, setDayOfWeek] = useState('');
  const [weekOfMonth, setWeekOfMonth] = useState('');
  const [startDate, setStartDate] = useState('');
  const [notes, setNotes] = useState('');

  const [submitState, setSubmitState] = useState<SubmitState>('idle');
  const [errorMsg, setErrorMsg] = useState('');
  const [contactError, setContactError] = useState(false);
  const [turnstileToken, setTurnstileToken] = useState('');

  const resetForm = useCallback(() => {
    setEmail('');
    setInstagram('');
    setEventName('');
    setEventUrl('');
    setStyles([]);
    setLocation('');
    setIsRecurring(false);
    setDate('');
    setTime('');
    setRecurrenceType('');
    setDayOfWeek('');
    setWeekOfMonth('');
    setStartDate('');
    setNotes('');
    setSubmitState('idle');
    setErrorMsg('');
    setContactError(false);
    setTurnstileToken('');
  }, []);

  const toggleStyle = (s: DanceStyle) => {
    setStyles(prev =>
      prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s],
    );
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();

    if (!email.trim() && !instagram.trim()) {
      setContactError(true);
      return;
    }
    setContactError(false);
    setSubmitState('submitting');
    setErrorMsg('');

    const body = {
      email: email.trim(),
      instagram: instagram.trim(),
      event_name: eventName.trim(),
      event_url: eventUrl.trim(),
      styles,
      location: location.trim(),
      is_recurring: isRecurring,
      date: !isRecurring ? date : '',
      time: !isRecurring ? time : '',
      recurrence_type: isRecurring ? recurrenceType : '',
      day_of_week: isRecurring ? dayOfWeek : '',
      week_of_month: isRecurring && recurrenceType === 'monthly' ? weekOfMonth : '',
      start_date: isRecurring ? startDate : '',
      notes: notes.trim(),
      cf_turnstile_token: turnstileToken,
    };

    try {
      const res = await fetch(`${API_URL}/api/submit-event`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || `Error ${res.status}`);
      }
      setSubmitState('success');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Something went wrong');
      setSubmitState('error');
      // Turnstile tokens are single-use; get a fresh one for the retry.
      setTurnstileToken('');
      window.turnstile?.reset();
    }
  };

  const summary = recurrenceSummary(recurrenceType, dayOfWeek, weekOfMonth);

  return (
    <div className="submit-page">
      <div className="submit-page-card">
        {/* Header */}
        <div className="submit-modal-header">
          <h1 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0, color: '#1f2937' }}>
            Submit an Event
          </h1>
          <Link
            href="/"
            className="pretty-pill pretty-pill-neutral"
            style={{ padding: '0.2rem 0.5rem', lineHeight: 1, textDecoration: 'none' }}
          >
            &#x2715;
          </Link>
        </div>

        {submitState === 'success' ? (
          <div className="submit-modal-body" style={{ textAlign: 'center', padding: '3rem 1rem' }}>
            <div style={{ fontSize: '2rem', marginBottom: '0.75rem' }}>&#10003;</div>
            <p className="text-sm" style={{ color: '#4b5563' }}>
              Submitted! We&apos;ll review your event.
            </p>
            <div style={{ marginTop: '1.5rem', display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
              <Link href="/" className="pretty-pill pretty-pill-ghost text-sm" style={{ textDecoration: 'none' }}>
                Back to map
              </Link>
              <button onClick={resetForm} className="pretty-pill pretty-pill-rose text-sm">
                Submit another
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="submit-modal-body">
            {/* ── Contact ── */}
            <fieldset className="submit-section">
              <div className="submit-row">
                <div className="submit-field">
                  <label htmlFor="se-email">Email</label>
                  <input
                    id="se-email"
                    type="email"
                    placeholder="you@example.com"
                    value={email}
                    onChange={e => { setEmail(e.target.value); setContactError(false); }}
                    className={`submit-input${contactError && !email.trim() && !instagram.trim() ? ' submit-input-error' : ''}`}
                  />
                </div>
                <div className="submit-field">
                  <label htmlFor="se-ig">Instagram</label>
                  <input
                    id="se-ig"
                    type="text"
                    placeholder="@handle"
                    value={instagram}
                    onChange={e => { setInstagram(e.target.value); setContactError(false); }}
                    className={`submit-input${contactError && !email.trim() && !instagram.trim() ? ' submit-input-error' : ''}`}
                  />
                </div>
              </div>
            </fieldset>

            {/* ── Event Details ── */}
            <fieldset className="submit-section">
              <div className="submit-field">
                <label htmlFor="se-name">Event Name *</label>
                <input
                  id="se-name"
                  type="text"
                  required
                  placeholder="Bachata Social at Studio XYZ"
                  value={eventName}
                  onChange={e => setEventName(e.target.value)}
                  className="submit-input"
                />
              </div>
              <div className="submit-field">
                <label htmlFor="se-url">Link to Event *</label>
                <input
                  id="se-url"
                  type="url"
                  required
                  placeholder="https://..."
                  value={eventUrl}
                  onChange={e => setEventUrl(e.target.value)}
                  className="submit-input"
                />
              </div>
              <div className="submit-field">
                <label>Dance Styles</label>
                <div className="flex flex-wrap gap-1.5">
                  {ALL_STYLES.map(s => (
                    <button
                      key={s}
                      type="button"
                      className={`pretty-pill text-xs ${
                        styles.includes(s)
                          ? STYLE_PILL_CLASS[s]
                          : 'pretty-pill-ghost'
                      }`}
                      onClick={() => toggleStyle(s)}
                    >
                      {STYLE_LABELS[s]}
                    </button>
                  ))}
                </div>
              </div>
              <div className="submit-field">
                <label htmlFor="se-loc">Location / Venue</label>
                <input
                  id="se-loc"
                  type="text"
                  placeholder="Venue name or address"
                  value={location}
                  onChange={e => setLocation(e.target.value)}
                  className="submit-input"
                />
              </div>
            </fieldset>

            {/* ── Scheduling ── */}
            <fieldset className="submit-section">
              <legend className="submit-section-label">Schedule</legend>
              <div className="flex gap-1.5 mb-3">
                <button
                  type="button"
                  className={`pretty-pill text-xs ${!isRecurring ? 'pretty-pill-solid-rose' : 'pretty-pill-ghost'}`}
                  onClick={() => setIsRecurring(false)}
                >
                  One-time
                </button>
                <button
                  type="button"
                  className={`pretty-pill text-xs ${isRecurring ? 'pretty-pill-solid-rose' : 'pretty-pill-ghost'}`}
                  onClick={() => setIsRecurring(true)}
                >
                  Recurring
                </button>
              </div>

              {!isRecurring ? (
                <div className="submit-row">
                  <div className="submit-field">
                    <label htmlFor="se-date">Date</label>
                    <input
                      id="se-date"
                      type="date"
                      value={date}
                      onChange={e => setDate(e.target.value)}
                      className="submit-input"
                    />
                  </div>
                  <div className="submit-field">
                    <label htmlFor="se-time">Time</label>
                    <input
                      id="se-time"
                      type="text"
                      placeholder="e.g. 7:30 PM"
                      value={time}
                      onChange={e => setTime(e.target.value)}
                      className="submit-input"
                    />
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  <div className="submit-field">
                    <label>How often?</label>
                    <div className="flex flex-wrap gap-1.5">
                      {(['weekly', 'biweekly', 'monthly'] as const).map(t => (
                        <button
                          key={t}
                          type="button"
                          className={`pretty-pill text-xs ${
                            recurrenceType === t ? 'pretty-pill-rose' : 'pretty-pill-ghost'
                          }`}
                          onClick={() => setRecurrenceType(t)}
                        >
                          {t.charAt(0).toUpperCase() + t.slice(1)}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="submit-field">
                    <label>Day of week</label>
                    <div className="flex flex-wrap gap-1.5">
                      {DAYS.map(d => (
                        <button
                          key={d}
                          type="button"
                          className={`pretty-pill text-xs ${
                            dayOfWeek === d ? 'pretty-pill-rose' : 'pretty-pill-ghost'
                          }`}
                          onClick={() => setDayOfWeek(d)}
                        >
                          {DAY_SHORT[d]}
                        </button>
                      ))}
                    </div>
                  </div>

                  {recurrenceType === 'monthly' && (
                    <div className="submit-field">
                      <label>Which week?</label>
                      <div className="flex flex-wrap gap-1.5">
                        {WEEK_OPTIONS.map(w => (
                          <button
                            key={w}
                            type="button"
                            className={`pretty-pill text-xs ${
                              weekOfMonth === w ? 'pretty-pill-rose' : 'pretty-pill-ghost'
                            }`}
                            onClick={() => setWeekOfMonth(w)}
                          >
                            {w}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="submit-row">
                    <div className="submit-field">
                      <label htmlFor="se-start">Start date</label>
                      <input
                        id="se-start"
                        type="date"
                        value={startDate}
                        onChange={e => setStartDate(e.target.value)}
                        className="submit-input"
                      />
                    </div>
                    <div className="submit-field">
                      <label htmlFor="se-time-rec">Time</label>
                      <input
                        id="se-time-rec"
                        type="text"
                        placeholder="e.g. 8 PM"
                        value={time}
                        onChange={e => setTime(e.target.value)}
                        className="submit-input"
                      />
                    </div>
                  </div>

                  {summary && (
                    <p className="text-xs italic" style={{ color: '#6b7280' }}>
                      {summary}
                      {time && ` at ${time}`}
                      {startDate && `, starting ${new Date(startDate + 'T00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`}
                    </p>
                  )}
                </div>
              )}
            </fieldset>

            {/* ── Notes ── */}
            <fieldset className="submit-section">
              <legend className="submit-section-label">Additional Notes</legend>
              <textarea
                placeholder="Anything else we should know? (pricing, level, special instructions...)"
                value={notes}
                onChange={e => setNotes(e.target.value)}
                rows={3}
                className="submit-input submit-textarea"
              />
            </fieldset>

            {/* ── Actions ── */}
            <TurnstileWidget onToken={setTurnstileToken} />
            {submitState === 'error' && (
              <p className="text-xs" style={{ color: '#ef4444' }}>{errorMsg}</p>
            )}
            <button
              type="submit"
              disabled={submitState === 'submitting'}
              className="pretty-pill pretty-pill-solid-rose submit-btn"
            >
              {submitState === 'submitting' ? (
                <>
                  <span className="submit-spinner" />
                  Submitting...
                </>
              ) : (
                'Submit Event'
              )}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
