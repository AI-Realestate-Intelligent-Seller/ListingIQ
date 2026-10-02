"use client";

import {
  useState,
  useCallback,
  useEffect,
  useRef,
} from "react";

import {
  CalendarView,
  CalendarEvent,
} from "./type";

import {
  fetchCalendarEvents,
} from "./calender-api";

import {
  addMonths,
  addDays,
  isSameDay,
} from "./date-utils";

import {
  CalendarHeader,
} from "./calender-header";

import {
  MonthView,
} from "./month-view";

import {
  WeekView,
} from "./week-view";

import {
  DayView,
} from "./day-view";

import {
  CalendarSidebar,
} from "./calendar-sidebar";

import {
  CalendarEventDetail,
} from "./calender-event";


import styles from "../../styles/calender.module.css";


export function CalendarContainer({ focusBookingId = null }: { focusBookingId?: number | null }) {
  const focusedBookingRef = useRef<number | null>(null);
  const [focusError, setFocusError] = useState("");
  const [currentDate, setCurrentDate] =
    useState<Date>(new Date());

  const [selectedDate, setSelectedDate] =
    useState<Date>(new Date());

  const [view, setView] =
    useState<CalendarView>("month");

  const [activeEvent, setActiveEvent] =
    useState<CalendarEvent | null>(null);

  const [events, setEvents] =
    useState<CalendarEvent[]>([]);

  const [loadingEvents, setLoadingEvents] =
    useState(false);


  /*
   * Load calendar events from REST API.
   */
  const loadCalendarEvents = useCallback(
    async () => {
      try {
        setLoadingEvents(true);

        const calendarEvents =
          await fetchCalendarEvents();

        setEvents(calendarEvents);
        if (focusBookingId !== null && focusedBookingRef.current !== focusBookingId) {
          const booking = calendarEvents.find((event) => event.id === String(focusBookingId));
          if (booking) {
            const date = new Date(`${booking.date}T12:00:00`);
            setCurrentDate(date);
            setSelectedDate(date);
            setActiveEvent(booking);
            focusedBookingRef.current = focusBookingId;
            setFocusError("");
          } else {
            setFocusError("This booking is no longer available in your calendar.");
          }
        }

      } catch (error) {
        console.error(
          "Failed to load calendar:",
          error,
        );

        setEvents([]);

      } finally {
        setLoadingEvents(false);
      }
    },
    [focusBookingId],
  );


  /*
   * Initial calendar load.
   */
  useEffect(() => {
    loadCalendarEvents();
  }, [loadCalendarEvents]);


  /*
   * Listen for calendar events coming from
   * the shared/global WebSocket provider.
   *
   * The CalendarContainer no longer creates
   * or manages its own WebSocket connection.
   */
  useEffect(() => {
    const handleCalendarEvent = async () => {
      await loadCalendarEvents();
    };

    window.addEventListener(
      "listingiq-calendar-event",
      handleCalendarEvent,
    );

    return () => {
      window.removeEventListener(
        "listingiq-calendar-event",
        handleCalendarEvent,
      );
    };
  }, [loadCalendarEvents]);


  /*
   * Previous period.
   */
  const handlePrev = useCallback(() => {
    if (view === "month") {
      setCurrentDate(
        (date) =>
          addMonths(date, -1),
      );
    } else if (view === "week") {
      setCurrentDate(
        (date) =>
          addDays(date, -7),
      );
    } else {
      setCurrentDate(
        (date) =>
          addDays(date, -1),
      );
    }
  }, [view]);


  /*
   * Next period.
   */
  const handleNext = useCallback(() => {
    if (view === "month") {
      setCurrentDate(
        (date) =>
          addMonths(date, 1),
      );
    } else if (view === "week") {
      setCurrentDate(
        (date) =>
          addDays(date, 7),
      );
    } else {
      setCurrentDate(
        (date) =>
          addDays(date, 1),
      );
    }
  }, [view]);


  /*
   * Jump back to today.
   */
  const handleToday = useCallback(() => {
    const now = new Date();

    setCurrentDate(now);
    setSelectedDate(now);
  }, []);


  /*
   * Select calendar date.
   */
  const handleSelectDate = useCallback(
    (date: Date) => {
      setSelectedDate(date);
      setCurrentDate(date);

      if (!isSameDay(date, selectedDate)) {
        // Optional:
        // setView("day");
      }
    },
    [selectedDate],
  );


  return (
    <div>



      <CalendarHeader
        currentDate={currentDate}
        view={view}
        onViewChange={setView}
        onPrev={handlePrev}
        onNext={handleNext}
        onToday={handleToday}
      />


      {focusError ? <p role="status">{focusError}</p> : null}
      {loadingEvents ? (
        <div
          style={{
            padding: "8px 0",
            fontSize: "12px",
          }}
        >
          Loading calendar...
        </div>
      ) : null}


      <div className={styles.calendarLayout}>
        <div>

          {view === "month" && (
            <MonthView
              year={currentDate.getFullYear()}
              month={currentDate.getMonth()}
              selectedDate={selectedDate}
              onSelectDate={handleSelectDate}
              onSelectEvent={setActiveEvent}
              events={events}
            />
          )}


          {view === "week" && (
            <WeekView
              date={currentDate}
              onSelectEvent={setActiveEvent}
              events={events}
            />
          )}


          {view === "day" && (
            <DayView
              date={currentDate}
              onSelectEvent={setActiveEvent}
              events={events}
            />
          )}

        </div>


        <CalendarSidebar
          currentDate={currentDate}
          selectedDate={selectedDate}
          onSelectDate={handleSelectDate}
          onSelectEvent={setActiveEvent}
          events={events}
        />

      </div>


      {activeEvent && (
        <CalendarEventDetail
          event={activeEvent}
          onClose={() =>
            setActiveEvent(null)
          }
        />
      )}

    </div>
  );
}
