'use client';

import { CalendarEvent } from './type';

import {
  toISODate,
  isToday,
  parseTime,
  getAllHours,
  formatLongDate,
} from './date-utils';

import styles from '../../styles/calender.module.css';

interface DayViewProps {
  date: Date;
  onSelectEvent: (event: CalendarEvent) => void;
  events: CalendarEvent[];
}

const eventColors: Record<
  CalendarEvent['type'],
  { bg: string; border: string }
> = {
  showing: { bg: '#dcfce7', border: '#166534' },
  call: { bg: '#eff6ff', border: '#1d4ed8' },
  follow_up: { bg: '#fef9c3', border: '#854d0e' },
  meeting: { bg: '#f5f3ff', border: '#6d28d9' },
  open_house: { bg: '#fff3e0', border: '#b45309' },
};

export function DayView({
  date,
  onSelectEvent,
  events,
}: DayViewProps) {
  const dateStr = toISODate(date);
  const today = isToday(date);
  const hours = getAllHours();

  /*
   * The Calendar API has already converted UTC timestamps
   * into the browser's local timezone.
   *
   * Therefore event.date and event.endDate are already
   * the correct dates for the user.
   */
  const dayEvents = events.filter((event) => {
    const eventStartDate = event.date;
    const eventEndDate = event.endDate || event.date;

    return (
      eventStartDate <= dateStr &&
      eventEndDate >= dateStr
    );
  });



  return (
    <div className={styles.calendarCard}>
      <div
        className={`${styles.dayViewHeader} ${
          today ? styles.dayViewHeaderToday : ''
        }`}
      >
        <h2 className={styles.dayViewHeaderTitle}>
          {formatLongDate(date)}
        </h2>
      </div>

      <div className={styles.dayView}>
        {/* TIME COLUMN */}
        <div className={styles.dayTimeColumn}>
          {hours.map((h) => (
            <div
              key={h}
              className={styles.dayTimeSlot}
            >
              {h}
            </div>
          ))}
        </div>

        {/* EVENTS COLUMN */}
        <div className={styles.dayContentColumn}>
          {/* Grid */}
          {hours.map((h) => (
            <div
              key={h}
              className={styles.dayGridSlot}
            />
          ))}

          {/* Events */}
          {dayEvents.map((evt) => {
            const startMin = parseTime(evt.startTime);
            const endMin = parseTime(evt.endTime);

            /*
             * 72px = one hour.
             */
            const top = (startMin / 60) * 72;

            /*
             * Normal event duration.
             */
            let duration = endMin - startMin;

            /*
             * Safety for events crossing midnight.
             */
            if (duration <= 0) {
              duration = 60;
            }

            const height = (duration / 60) * 72;

            const colors = eventColors[evt.type];

            return (
              <button
                key={evt.id}
                type="button"
                className={styles.dayEvent}
                style={{
                  top: `${top}px`,
                  height: `${Math.max(height, 48)}px`,
                  background: colors.bg,
                  color: colors.border,
                }}
                onClick={() => onSelectEvent(evt)}
              >
                <span className={styles.dayEventTitle}>
                  {evt.title}
                </span>

                <span className={styles.dayEventMeta}>
                  {evt.startTime} – {evt.endTime}
                  {evt.leadName
                    ? ` · ${evt.leadName}`
                    : ''}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}