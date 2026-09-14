
'use client';

import { CalendarEvent } from './type';

import {
  getMonthGrid,
  isSameMonth,
  isToday,
  toISODate,
} from './date-utils';

import styles from '../../styles/calender.module.css';

interface MonthViewProps {
  year: number;
  month: number;
  selectedDate: Date;
  onSelectDate: (date: Date) => void;
  onSelectEvent: (event: CalendarEvent) => void;
  events: CalendarEvent[];
}

const WEEKDAYS = [
  'Sun',
  'Mon',
  'Tue',
  'Wed',
  'Thu',
  'Fri',
  'Sat',
];

const eventTypeClass: Record<
  CalendarEvent['type'],
  string
> = {
  showing: styles.eventShowing,
  call: styles.eventCall,
  follow_up: styles.eventFollowUp,
  meeting: styles.eventMeeting,
  open_house: styles.eventOpenHouse,
};

export function MonthView({
  year,
  month,
  selectedDate,
  onSelectDate,
  onSelectEvent,
  events,
}: MonthViewProps) {
  const grid = getMonthGrid(year, month);

  return (
    <div className={styles.calendarCard}>
      <div className={styles.monthGrid}>
        {WEEKDAYS.map((day) => (
          <div
            key={day}
            className={styles.weekdayHeader}
          >
            {day}
          </div>
        ))}

        {grid.map((date, idx) => {
          const dateStr = toISODate(date);

          // Get events from the events prop
          // instead of mock-data.ts
          const dayEvents = events.filter((event) => {
  const eventEndDate = event.endDate || event.date;

  return (
    event.date <= dateStr &&
    eventEndDate >= dateStr
  );
});

          const currentMonth = isSameMonth(
            date,
            new Date(year, month)
          );

          const today = isToday(date);

          const selected = isSameDay(
            date,
            selectedDate
          );

          return (
            <div
              key={idx}
              className={`
                ${styles.dayCell}
                ${
                  !currentMonth
                    ? styles.dayCellOtherMonth
                    : ''
                }
                ${
                  today
                    ? styles.dayCellToday
                    : ''
                }
                ${
                  selected
                    ? styles.dayCellSelected
                    : ''
                }
              `}
              onClick={() =>
                onSelectDate(date)
              }
            >
              <div
                className={`${styles.dayNumber} ${
                  today
                    ? styles.dayNumberToday
                    : ''
                }`}
              >
                {date.getDate()}
              </div>

              <div
                className={styles.dayEvents}
              >
                {dayEvents
                  .slice(0, 3)
                  .map((evt) => (
                    <button
                      key={evt.id}
                      className={`${styles.eventChip} ${
                        eventTypeClass[
                          evt.type
                        ]
                      } ${
                        evt.status ===
                        'pending'
                          ? styles.eventPending
                          : ''
                      }`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectEvent(evt);
                      }}
                      title={`${evt.title} — ${evt.startTime}`}
                    >
                      {evt.startTime}{' '}
                      {evt.title}
                    </button>
                  ))}

                {dayEvents.length > 3 && (
                  <span
                    style={{
                      fontSize: 10,
                      color: '#8a9a95',
                      paddingLeft: 2,
                    }}
                  >
                    +{dayEvents.length - 3}{' '}
                    more
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function isSameDay(
  a: Date,
  b: Date
): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

