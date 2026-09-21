'use client';

import { CalendarEvent, CalendarEventType } from './type';
import styles from '../../styles/calender.module.css';

interface CalendarEventDetailProps {
  event: CalendarEvent;
  onClose: () => void;
}

const eventTypeLabels: Record<CalendarEventType, string> = {
  showing: 'Property Showing',
  call: 'Phone Call',
  follow_up: 'Follow-up',
  meeting: 'Meeting',
  open_house: 'Open House',
};

export function CalendarEventDetail({ event, onClose }: CalendarEventDetailProps) {
  const statusClass =
    event.status === 'confirmed'
      ? styles.modalStatusConfirmed
      : event.status === 'pending'
      ? styles.modalStatusPending
      : styles.modalStatusCancelled;

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modalCard} onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <h3 className={styles.modalTitle}>{event.title}</h3>
          <button className={styles.modalClose} onClick={onClose} aria-label="Close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className={styles.modalBody}>
          <div className={styles.modalRow}>
            <svg className={styles.modalRowIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
              <line x1="16" y1="2" x2="16" y2="6" />
              <line x1="8" y1="2" x2="8" y2="6" />
              <line x1="3" y1="10" x2="21" y2="10" />
            </svg>
            <div>
              <p className={styles.modalRowLabel}>Date & Time</p>
              <p className={styles.modalRowValue}>
                {new Date(event.date).toLocaleDateString('en-US', {
                  weekday: 'long',
                  month: 'long',
                  day: 'numeric',
                  year: 'numeric',
                })}
                {' · '}
                {event.startTime} – {event.endTime}
              </p>
            </div>
          </div>

          <div className={styles.modalRow}>
            <svg className={styles.modalRowIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
              <circle cx="12" cy="7" r="4" />
            </svg>
            <div>
              <p className={styles.modalRowLabel}>Type</p>
              <p className={styles.modalRowValue}>{eventTypeLabels[event.type]}</p>
              <span className={`${styles.modalStatus} ${statusClass}`}>{event.status}</span>
            </div>
          </div>

          {event.leadName && (
            <div className={styles.modalRow}>
              <svg className={styles.modalRowIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
              <div>
                <p className={styles.modalRowLabel}>Lead</p>
                <p className={styles.modalRowValue}>{event.leadName}</p>
              </div>
            </div>
          )}

          {event.propertyAddress && (
            <div className={styles.modalRow}>
              <svg className={styles.modalRowIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                <polyline points="9 22 9 12 15 12 15 22" />
              </svg>
              <div>
                <p className={styles.modalRowLabel}>Property</p>
                <p className={styles.modalRowValue}>{event.propertyAddress}</p>
              </div>
            </div>
          )}

          {event.agentName && (
            <div className={styles.modalRow}>
              <svg className={styles.modalRowIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
              <div>
                <p className={styles.modalRowLabel}>Agent</p>
                <p className={styles.modalRowValue}>{event.agentName}</p>
              </div>
            </div>
          )}

          {event.description && (
            <div className={styles.modalRow}>
              <svg className={styles.modalRowIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="17" y1="10" x2="3" y2="10" />
                <line x1="21" y1="6" x2="3" y2="6" />
                <line x1="21" y1="14" x2="3" y2="14" />
                <line x1="17" y1="18" x2="3" y2="18" />
              </svg>
              <div>
                <p className={styles.modalRowLabel}>Notes</p>
                <p className={styles.modalRowValue}>{event.description}</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}