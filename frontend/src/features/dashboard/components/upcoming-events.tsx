import type { AgentOverview } from "@/features/assignments/types/assignments.types";
import { formatLocalTime, getBrowserTimeZone } from "@/features/calendar/calender-api";

type UpcomingEventsProps = {
  events: AgentOverview["upcoming_events"];
  onOpenCalendar: (bookingId?: number) => void;
};

function CalendarIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <rect x="3" y="5" width="18" height="16" rx="3" />
      <path d="M7 3v4m10-4v4M3 11h18m-13 4h3m2 0h3m-8 3h3" strokeLinecap="round" />
    </svg>
  );
}

export function UpcomingEvents({ events, onOpenCalendar }: UpcomingEventsProps) {
  const timeZone = getBrowserTimeZone();
  const sortedEvents = [...events]
    .sort((a, b) => new Date(a.start_at).getTime() - new Date(b.start_at).getTime())
    .slice(0, 3);

  return (
    <article className="agent-analytics-card agent-events-card" aria-labelledby="upcoming-events-title">
      <div className="agent-events-heading">
        <div>
          <span className="agent-events-eyebrow">YOUR SCHEDULE</span>
          <h3 id="upcoming-events-title">Upcoming events</h3>
        </div>
        <span className="agent-events-icon"><CalendarIcon /></span>
      </div>

      {sortedEvents.length ? (
        <ol className="agent-upcoming-events" aria-label="Upcoming appointments">
          {sortedEvents.map((event, index) => {
            const title = event.title?.trim() || "Scheduled appointment";
            const name = event.name?.trim();
            const start = new Date(event.start_at);
            const end = event.end_at ? new Date(event.end_at) : null;
            const dateLabel = start.toLocaleDateString(undefined, { timeZone, weekday: "long", month: "long", day: "numeric", year: "numeric" });
            const crossesDay = end && end.toLocaleDateString(undefined, { timeZone }) !== start.toLocaleDateString(undefined, { timeZone });
            const endDateLabel = crossesDay ? `${end.toLocaleDateString(undefined, { timeZone, month: "short", day: "numeric" })}, ` : "";
            const timeLabel = `${formatLocalTime(event.start_at)}${event.end_at ? ` – ${endDateLabel}${formatLocalTime(event.end_at)}` : ""}`;

            return (
              <li key={event.id}>
                <button
                  type="button"
                  className={`agent-upcoming-event${index === 0 ? " is-next" : ""}`}
                  onClick={() => onOpenCalendar(event.id)}
                  aria-label={`Open ${title}${name ? ` with ${name}` : ""} on ${dateLabel}, ${timeLabel} in calendar`}
                >
                  <time className="agent-upcoming-event-date" dateTime={event.start_at}>
                    <span>{start.toLocaleDateString(undefined, { timeZone, month: "short" })}</span>
                    <strong>{start.toLocaleDateString(undefined, { timeZone, day: "numeric" })}</strong>
                  </time>
                  <span className="agent-upcoming-event-info">
                    <span className="agent-upcoming-event-day">
                      {start.toLocaleDateString(undefined, { timeZone, weekday: "long" })}
                      {index === 0 ? <span className="agent-event-next-badge">Up next</span> : null}
                    </span>
                    <strong>{title}</strong>
                    {name ? <span className="agent-upcoming-event-name">{name}</span> : null}
                    <span className="agent-upcoming-event-time">{timeLabel}</span>
                  </span>
                  <span className="agent-upcoming-event-arrow" aria-hidden="true">↗</span>
                </button>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="agent-events-empty">
          <span className="agent-events-empty-icon"><CalendarIcon /></span>
          <strong>Your schedule is clear</strong>
          <p>Upcoming appointments will appear here so you can plan your day.</p>
        </div>
      )}

      <button type="button" className="agent-events-calendar-link" onClick={() => onOpenCalendar()}>
        View calendar <span aria-hidden="true">→</span>
      </button>
    </article>
  );
}
