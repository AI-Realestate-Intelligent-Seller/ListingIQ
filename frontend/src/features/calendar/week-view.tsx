
'use client';

import { CalendarEvent } from './type';

import {
  getWeekGrid,
  toISODate,
  isToday,
  formatShortWeekday,
  parseTime,
  getBusinessHours,
} from './date-utils';

import styles from '../../styles/calender.module.css';

interface WeekViewProps {
  date: Date;
  onSelectEvent: (event: CalendarEvent) => void;
  events: CalendarEvent[];
}

const eventColors: Record<
  CalendarEvent['type'],
  { bg: string; border: string }
> = {
  showing: {
    bg: '#dcfce7',
    border: '#166534',
  },
  call: {
    bg: '#eff6ff',
    border: '#1d4ed8',
  },
  follow_up: {
    bg: '#fef9c3',
    border: '#854d0e',
  },
  meeting: {
    bg: '#f5f3ff',
    border: '#6d28d9',
  },
  open_house: {
    bg: '#fff3e0',
    border: '#b45309',
  },
};

export function WeekView({
  date,
  onSelectEvent,
  events,
}: WeekViewProps) {
  const weekDays = getWeekGrid(date);
  const hours = getBusinessHours();

  return (
    <div className={styles.calendarCard}>
      <div className={styles.weekView}>
        <div>
          <div className={styles.weekDayHeader} />

          <div className={styles.weekTimeColumn}>
            {hours.map((h) => (
              <div
                key={h}
                className={styles.weekTimeSlot}
              >
                {h}
              </div>
            ))}
          </div>
        </div>

        {weekDays.map((day) => {
          const dateStr = toISODate(day);

          // Get events from Google Calendar data
          // passed down from CalendarContainer.
       const dayEvents = events.filter((event) => {
  const eventEndDate = event.endDate

  return (
    event.date <= dateStr &&
    eventEndDate >= dateStr
  );
});

          const today = isToday(day);

          return (
            <div
              key={dateStr}
              className={styles.weekDayColumn}
            >
              <div
                className={`${styles.weekDayHeader} ${
                  today
                    ? styles.weekDayHeaderToday
                    : ''
                }`}
              >
                <div className={styles.weekDayName}>
                  {formatShortWeekday(day)}
                </div>

                <div
                  className={`${styles.weekDayNumber} ${
                    today
                      ? styles.weekDayNumberToday
                      : ''
                  }`}
                >
                  {day.getDate()}
                </div>
              </div>

              <div className={styles.weekDayGrid}>
                {hours.map((h) => (
                  <div
                    key={h}
                    className={styles.weekGridSlot}
                  />
                ))}

                {dayEvents
                  .filter(
                    (evt) =>
                      evt.startTime !== '00:00'
                  )
                  .map((evt) => {
                    const startMin = parseTime(
                      evt.startTime
                    );

                    const endMin = parseTime(
                      evt.endTime
                    );

                    const startHour = 8;

                    const top =
                      ((startMin -
                        startHour * 60) /
                        60) *
                      64;

                    const height =
                      ((endMin - startMin) /
                        60) *
                      64;

                    const colors =
                      eventColors[evt.type];

                    return (
                      <button
                        key={evt.id}
                        className={
                          styles.weekEvent
                        }
                        style={{
                          top: `${top}px`,
                          height: `${Math.max(
                            height,
                            24
                          )}px`,
                          background:
                            colors.bg,
                          color:
                            colors.border,
                        }}
                        onClick={() =>
                          onSelectEvent(evt)
                        }
                      >
                        <span
                          className={
                            styles.weekEventTitle
                          }
                        >
                          {evt.title}
                        </span>

                        <span
                          className={
                            styles.weekEventTime
                          }
                        >
                          {evt.startTime} –{' '}
                          {evt.endTime}
                        </span>
                      </button>
                    );
                  })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

