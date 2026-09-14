'use client';

import { readAuthSession } from "../../features/auth/lib/auth-storage";
import { CalendarView } from './type';
import { formatMonthYear } from './date-utils';
import styles from '../../styles/calender.module.css';
//import { enablePushNotifications,checkPushStatus } from "./push-notification";


interface CalendarHeaderProps {
  currentDate: Date;
  view: CalendarView;
  onViewChange: (view: CalendarView) => void;
  onPrev: () => void;
  onNext: () => void;
  onToday: () => void;
}

export function CalendarHeader({
  currentDate,
  view,
  onViewChange,
  onPrev,
  onNext,
  onToday,
}: CalendarHeaderProps) {
  const handleGoogleCalendarConnect = async () => {
    const session = readAuthSession();

    if (!session?.access_token) {
      console.error("No ListingIQ access token found");
      return;
    }

    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/auth/google/login`,
        {
          method: "GET",
          headers: {
            Authorization: `Bearer ${session.access_token}`,
          },
        }
      );

      if (!response.ok) {
        const error = await response.text();
        console.error("Google Calendar login failed:", error);
        return;
      }

      const data = await response.json();
      window.location.href = data.authorization_url;
    } catch (error) {
      console.error("Google Calendar connection failed:", error);
    }
  };

  return (
    <div className={styles.calendarHeader}>
      <div className={styles.calendarHeaderLeft}>
        <h1 className={styles.calendarTitle}>Calendar</h1>

        <button className={`${styles.btn} ${styles.btnGhost}`} onClick={onToday}>
          Today
        </button>

        <div style={{ display: 'flex', gap: 4 }}>
          <button className={`${styles.btn} ${styles.btnIcon} ${styles.btnGhost}`} onClick={onPrev} aria-label="Previous">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <button className={`${styles.btn} ${styles.btnIcon} ${styles.btnGhost}`} onClick={onNext} aria-label="Next">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        </div>

        <span className={styles.monthLabel}>{formatMonthYear(currentDate)}</span>

        <div className={styles.viewToggle}>
          {(['month', 'week', 'day'] as CalendarView[]).map((v) => (
            <button
              key={v}
              className={`${styles.viewToggleBtn} ${view === v ? styles.viewToggleBtnActive : ''}`}
              onClick={() => onViewChange(v)}
            >
              {v.charAt(0).toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>
      </div>

      
    </div>
  );
}