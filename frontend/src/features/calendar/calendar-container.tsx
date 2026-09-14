'use client';

import {
  useState,
  useCallback,
  useEffect,
  useRef,
} from 'react';

import {
  CalendarView,
  CalendarEvent,
} from './type';

import {
  fetchCalendarEvents,
} from './calender-api';

import {
  addMonths,
  addDays,
  isSameDay,
} from './date-utils';

import {
  CalendarHeader,
} from './calender-header';

import {
  MonthView,
} from './month-view';

import {
  WeekView,
} from './week-view';

import {
  DayView,
} from './day-view';

import {
  CalendarSidebar,
} from './calendar-sidebar';

import {
  CalendarEventDetail,
} from './calender-event';

import {
  readAuthSession,
} from '../auth/lib/auth-storage';


import PushNotifications from "./push-notification"
import styles from '../../styles/calender.module.css';

export function CalendarContainer() {
  const [currentDate, setCurrentDate] =
    useState<Date>(new Date());

  const [selectedDate, setSelectedDate] =
    useState<Date>(new Date());

  const [view, setView] =
    useState<CalendarView>('month');

  const [activeEvent, setActiveEvent] =
    useState<CalendarEvent | null>(null);

  const [events, setEvents] =
    useState<CalendarEvent[]>([]);

  const [loadingEvents, setLoadingEvents] =
    useState(false);

  const websocketRef =
    useRef<WebSocket | null>(null);

  /*
   * Load calendar events from REST API.
   */
  const loadCalendarEvents = useCallback(async () => {
    try {
      setLoadingEvents(true);

      const calendarEvents =
        await fetchCalendarEvents();

      setEvents(calendarEvents);
    } catch (error) {
      console.error(
        'Failed to load calendar:',
        error
      );

      setEvents([]);
    } finally {
      setLoadingEvents(false);
    }
  }, []);

 
  useEffect(() => {
    loadCalendarEvents();
  }, [loadCalendarEvents]);



  
  useEffect(() => {
    const session = readAuthSession();

    if (!session?.access_token) {
    

      return;
    }

    const apiUrl =
      process.env.NEXT_PUBLIC_API_URL;

    if (!apiUrl) {
      console.error(
        'Calendar WebSocket: NEXT_PUBLIC_API_URL is not configured'
      );

      return;
    }

    /*
     * Convert API URL to WebSocket URL.
     */
    const wsBaseUrl = apiUrl
      .replace(/^http:\/\//, 'ws://')
      .replace(/^https:\/\//, 'wss://')
      .replace(/\/api\/v1\/?$/, '')
      .replace(/\/$/, '');

    const wsUrl =
      `${wsBaseUrl}/ws/calendar`;

  

    
    const websocket =
      new WebSocket(wsUrl);

    websocketRef.current =
      websocket;

  
    websocket.onopen = () => {
   

     
      websocket.send(
        JSON.stringify({
          type: 'auth',
          token: session.access_token,
        })
      );
    };

   
    websocket.onmessage = async (event) => {
      try {
        const message =
          JSON.parse(event.data);

       

        if (
          message.type === 'authenticated'
        ) {
         

          return;
        }

        if (
          message.type === 'booking_created' ||
          message.type === 'booking_updated' ||
          message.type === 'booking_deleted'
        ) {
         
          await loadCalendarEvents();
        }
      } catch (error) {
        console.error(
          'Calendar WebSocket message error:',
          error
        );
      }
    };

    /*
     * WebSocket error.
     */
    websocket.onerror = (error) => {
      console.error(
        'Calendar WebSocket error:',
        error
      );

      console.error(
        'WebSocket readyState:',
        websocket.readyState
      );

      console.error(
        'WebSocket URL:',
        wsUrl
      );
    };

   
    websocket.onclose = (event) => {
     

      websocketRef.current =
        null;
    };
    return () => {
     

      websocket.close();

      websocketRef.current =
        null;
    };
  }, [loadCalendarEvents]);

  
  const handlePrev = useCallback(() => {
    if (view === 'month') {
      setCurrentDate(
        (date) =>
          addMonths(date, -1)
      );
    } else if (view === 'week') {
      setCurrentDate(
        (date) =>
          addDays(date, -7)
      );
    } else {
      setCurrentDate(
        (date) =>
          addDays(date, -1)
      );
    }
  }, [view]);


  const handleNext = useCallback(() => {
    if (view === 'month') {
      setCurrentDate(
        (date) =>
          addMonths(date, 1)
      );
    } else if (view === 'week') {
      setCurrentDate(
        (date) =>
          addDays(date, 7)
      );
    } else {
      setCurrentDate(
        (date) =>
          addDays(date, 1)
      );
    }
  }, [view]);

  
  const handleToday = useCallback(() => {
    const now = new Date();

    setCurrentDate(now);
    setSelectedDate(now);
  }, []);

  
  const handleSelectDate = useCallback(
    (date: Date) => {
      setSelectedDate(date);
      setCurrentDate(date);

      if (!isSameDay(date, selectedDate)) {
        // setView('day');
      }
    },
    [selectedDate]
  );

  return (
    <div>
     < PushNotifications/>
      <CalendarHeader
        currentDate={currentDate}
        view={view}
        onViewChange={setView}
        onPrev={handlePrev}
        onNext={handleNext}
        onToday={handleToday}
      />

      <div className={styles.calendarLayout}>
        <div>
          {view === 'month' && (
            <MonthView
              year={currentDate.getFullYear()}
              month={currentDate.getMonth()}
              selectedDate={selectedDate}
              onSelectDate={handleSelectDate}
              onSelectEvent={setActiveEvent}
              events={events}
            />
          )}

          {view === 'week' && (
            <WeekView
              date={currentDate}
              onSelectEvent={setActiveEvent}
              events={events}
            />
          )}

          {view === 'day' && (
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