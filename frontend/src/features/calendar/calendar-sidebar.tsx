
'use client';

import { CalendarEvent } from './type';

import {
  getMonthGrid,
  isSameMonth,
  isToday,
  isSameDay,
  toISODate,
  formatMonthYear,
} from './date-utils';
import styles from '../../styles/calender.module.css';

interface CalendarSidebarProps {
  currentDate: Date;
  selectedDate: Date;
  onSelectDate: (date: Date) => void;
  onSelectEvent: (event: CalendarEvent) => void;
  events: CalendarEvent[];
}

const eventDotColors: Record<
  CalendarEvent['type'],
  string
> = {
  showing: '#166534',
  call: '#1d4ed8',
  follow_up: '#854d0e',
  meeting: '#6d28d9',
  open_house: '#b45309',
};

export function CalendarSidebar({
  currentDate,
  selectedDate,
  onSelectDate,
  onSelectEvent,
  events,
}: CalendarSidebarProps) {
  const year = currentDate.getFullYear();
  const month = currentDate.getMonth();

  const miniGrid = getMonthGrid(year, month);

  // Get upcoming events from the real Google
  const todayStr = toISODate(new Date());

  const upcoming = [...events]
    .filter(
      (event) =>
        event.date >= todayStr &&
        event.status !== 'cancelled'
    )
    .sort((a, b) => {
      const first = `${a.date} ${a.startTime}`;
      const second = `${b.date} ${b.startTime}`;

      return first.localeCompare(second);
    })
    .slice(0, 5);

  return (
    <aside className={styles.sidebar}>
      {/* Mini Calendar */}
      <div className={styles.miniCalendar}>
        <p className={styles.sidebarTitle}>
          {formatMonthYear(currentDate)}
        </p>

        <div className={styles.miniCalendarGrid}>
          {[
            'S',
            'M',
            'T',
            'W',
            'T',
            'F',
            'S',
          ].map((d, i) => (
            <div
              key={i}
              className={
                styles.miniCalendarWeekday
              }
            >
              {d}
            </div>
          ))}

          {miniGrid.map((date, idx) => {
            const dateStr = toISODate(date);

            // Get events from the events prop
            const dayEvents = events.filter(
              (event) =>
                event.date === dateStr
            );

            const inMonth = isSameMonth(
              date,
              currentDate
            );

            const today = isToday(date);

            const selected = isSameDay(
              date,
              selectedDate
            );

            return (
              <button
                key={idx}
                className={`
                  ${styles.miniCalendarDay}
                  ${
                    !inMonth
                      ? styles.miniCalendarDayOtherMonth
                      : ''
                  }
                  ${
                    today
                      ? styles.miniCalendarDayToday
                      : ''
                  }
                  ${
                    selected
                      ? styles.miniCalendarDaySelected
                      : ''
                  }
                `}
                style={{
                  position: 'relative',
                }}
                onClick={() =>
                  onSelectDate(date)
                }
              >
                {date.getDate()}

                {dayEvents.length > 0 &&
                  !today && (
                    <span
                      style={{
                        position: 'absolute',
                        bottom: 3,
                        width: 4,
                        height: 4,
                        borderRadius: '50%',
                        background:
                          '#1f5c4d',
                      }}
                    />
                  )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Upcoming Events */}
      <div>
        <p className={styles.sidebarTitle}>
          Upcoming
        </p>

        <div className={styles.upcomingList}>
          {upcoming.map((evt) => (
            <button
              key={evt.id}
              className={styles.upcomingItem}
              onClick={() =>
                onSelectEvent(evt)
              }
            >
              <span
                className={styles.upcomingDot}
                style={{
                  background:
                    eventDotColors[evt.type],
                }}
              />

              <div
                className={
                  styles.upcomingContent
                }
              >
                <p
                  className={
                    styles.upcomingTitle
                  }
                >
                  {evt.title}
                </p>

                <p
                  className={
                    styles.upcomingMeta
                  }
                >
                  {evt.leadName ||
                    evt.propertyAddress ||
                    'No location'}
                </p>

                <p
                  className={
                    styles.upcomingTime
                  }
                >
                  {new Date(
                    evt.date
                  ).toLocaleDateString(
                    'en-US',
                    {
                      month: 'short',
                      day: 'numeric',
                    }
                  )}

                  {' · '}

                  {evt.startTime}
                </p>
              </div>
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}

