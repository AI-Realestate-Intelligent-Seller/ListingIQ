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

/**
 * Convert event.date + event.startTime into a real Date.
 *
 * Expected examples:
 * date: "2026-09-19"
 * startTime: "10:35"
 *
 * Also supports:
 * "10:35 AM"
 * "4:30 PM"
 */
function getEventDateTime(
  date: string,
  startTime: string
): Date | null {
  if (!date || !startTime) {
    return null;
  }

  const [year, month, day] = date
    .split('-')
    .map(Number);

  if (!year || !month || !day) {
    return null;
  }

  const time = startTime.trim();

  /**
   * 12-hour format:
   * 10:35 AM
   * 4:30 PM
   */
  const twelveHourMatch = time.match(
    /^(\d{1,2}):(\d{2})\s*(AM|PM)$/i
  );

  if (twelveHourMatch) {
    let hour = Number(
      twelveHourMatch[1]
    );

    const minute = Number(
      twelveHourMatch[2]
    );

    const period =
      twelveHourMatch[3].toUpperCase();

    if (period === 'PM' && hour !== 12) {
      hour += 12;
    }

    if (period === 'AM' && hour === 12) {
      hour = 0;
    }

    return new Date(
      year,
      month - 1,
      day,
      hour,
      minute,
      0,
      0
    );
  }

  /**
   * 24-hour format:
   * 10:35
   * 16:30
   */
  const twentyFourHourMatch = time.match(
    /^(\d{1,2}):(\d{2})$/
  );

  if (twentyFourHourMatch) {
    const hour = Number(
      twentyFourHourMatch[1]
    );

    const minute = Number(
      twentyFourHourMatch[2]
    );

    return new Date(
      year,
      month - 1,
      day,
      hour,
      minute,
      0,
      0
    );
  }

  return null;
}

export function CalendarSidebar({
  currentDate,
  selectedDate,
  onSelectDate,
  onSelectEvent,
  events,
}: CalendarSidebarProps) {
  const year =
    currentDate.getFullYear();

  const month =
    currentDate.getMonth();

  const miniGrid =
    getMonthGrid(year, month);

  /**
   * IMPORTANT:
   *
   * Previously:
   *
   * event.date >= todayStr
   *
   * only checked the date.
   *
   * That meant:
   *
   * Today = Sep 19
   * Current time = 4:30 PM
   * Meeting = Sep 19 at 10:35 AM
   *
   * Sep 19 === Sep 19
   *
   * therefore it incorrectly appeared
   * in Upcoming.
   *
   * Now we compare the full:
   *
   * DATE + TIME
   */
  const now = new Date();

  const upcoming = [...events]
    .filter((event) => {
      if (
        event.status === 'cancelled'
      ) {
        return false;
      }

      const eventDateTime =
        getEventDateTime(
          event.date,
          event.startTime
        );

      if (!eventDateTime) {
        return false;
      }

      return (
        eventDateTime.getTime() >
        now.getTime()
      );
    })
    .sort((a, b) => {
      const first =
        getEventDateTime(
          a.date,
          a.startTime
        );

      const second =
        getEventDateTime(
          b.date,
          b.startTime
        );

      if (!first && !second) {
        return 0;
      }

      if (!first) {
        return 1;
      }

      if (!second) {
        return -1;
      }

      return (
        first.getTime() -
        second.getTime()
      );
    })
    .slice(0, 5);

  return (
    <aside
      className={styles.sidebar}
    >
      {/* Mini Calendar */}

      <div
        className={
          styles.miniCalendar
        }
      >
        <p
          className={
            styles.sidebarTitle
          }
        >
          {formatMonthYear(
            currentDate
          )}
        </p>

        <div
          className={
            styles.miniCalendarGrid
          }
        >
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

          {miniGrid.map(
            (date, idx) => {
              const dateStr =
                toISODate(date);

              /**
               * All events belonging
               * to this calendar day.
               */
              const dayEvents =
                events.filter(
                  (event) =>
                    event.date ===
                    dateStr
                );

              const inMonth =
                isSameMonth(
                  date,
                  currentDate
                );

              const today =
                isToday(date);

              const selected =
                isSameDay(
                  date,
                  selectedDate
                );

              return (
                <button
                  key={idx}
                  className={`
                    ${
                      styles.miniCalendarDay
                    }

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
                    position:
                      'relative',
                  }}
                  onClick={() =>
                    onSelectDate(
                      date
                    )
                  }
                >
                  {date.getDate()}

                  {dayEvents.length >
                    0 &&
                    !today && (
                      <span
                        style={{
                          position:
                            'absolute',
                          bottom: 3,
                          width: 4,
                          height: 4,
                          borderRadius:
                            '50%',
                          background:
                            '#1f5c4d',
                        }}
                      />
                    )}
                </button>
              );
            }
          )}
        </div>
      </div>

      {/* Upcoming Events */}

      <div>
        <p
          className={
            styles.sidebarTitle
          }
        >
          Upcoming
        </p>

        <div
          className={
            styles.upcomingList
          }
        >
          {upcoming.length ===
          0 ? (
            <p
              className={
                styles.upcomingMeta
              }
            >
              No upcoming events
            </p>
          ) : (
            upcoming.map(
              (evt) => (
                <button
                  key={evt.id}
                  className={
                    styles.upcomingItem
                  }
                  onClick={() =>
                    onSelectEvent(
                      evt
                    )
                  }
                >
                  <span
                    className={
                      styles.upcomingDot
                    }
                    style={{
                      background:
                        eventDotColors[
                          evt.type
                        ],
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
                      {
                        evt.title
                      }
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
                        `${evt.date}T00:00:00`
                      ).toLocaleDateString(
                        'en-US',
                        {
                          month:
                            'short',
                          day: 'numeric',
                        }
                      )}

                      {' · '}

                      {
                        evt.startTime
                      }
                    </p>
                  </div>
                </button>
              )
            )
          )}
        </div>
      </div>
    </aside>
  );
}