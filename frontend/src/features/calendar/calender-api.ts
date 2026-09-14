
import { readAuthSession } from "../auth/lib/auth-storage";
import type { CalendarEvent, CalendarEventType } from "./type";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

interface CalendarBookingApiResponse {
  id: number;
  phone: string;
  name?: string | null;
  title: string;
  start_at: string;
  end_at: string;
  join_token?: string | null;
  join_url?: string | null;
  created_at?: string | null;
}

function inferEventType(title: string): CalendarEventType {
  const value = title.toLowerCase();

  if (value.includes("open house")) {
    return "open_house";
  }

  if (value.includes("showing")) {
    return "showing";
  }

  if (value.includes("call")) {
    return "call";
  }

  if (value.includes("follow")) {
    return "follow_up";
  }

  return "meeting";
}

/**
 * Get the timezone configured on the user's browser/device.
 *
 * Examples:
 * Asia/Karachi
 * America/Chicago
 * America/New_York
 */
function getBrowserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/**
 * Convert a UTC datetime from the API into the browser's
 * local calendar date.
 *
 * Example:
 * 2026-09-10T15:58:00Z
 * Asia/Karachi → 2026-09-10
 */
function formatLocalDate(dateTime: string): string {
  const date = new Date(dateTime);

  return new Intl.DateTimeFormat("en-CA", {
    timeZone: getBrowserTimeZone(),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

/**
 * Convert a UTC datetime from the API into the browser's
 * local time.
 *
 * Example:
 * 2026-09-10T15:58:00Z
 * Asia/Karachi → 08:58 PM
 */
function formatLocalTime(dateTime: string): string {
  const date = new Date(dateTime);

  return new Intl.DateTimeFormat("en-US", {
    timeZone: getBrowserTimeZone(),
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  }).format(date);
}

function mapBookingToCalendarEvent(
  booking: CalendarBookingApiResponse
): CalendarEvent {
  const title = booking.title || "Property consultation";

  return {
    id: String(booking.id),

    title,

    // Convert UTC date to browser local date
    date: formatLocalDate(booking.start_at),

    // Convert UTC end date to browser local date
    endDate: formatLocalDate(booking.end_at),

    // Convert UTC time to browser local time
    startTime: formatLocalTime(booking.start_at),

    // Convert UTC time to browser local time
    endTime: formatLocalTime(booking.end_at),

    type: inferEventType(title),

    description: booking.name
      ? `Meeting with ${booking.name}`
      : undefined,

    leadName: booking.name ?? undefined,

    status: "confirmed",
  };
}

// FETCH CALENDAR BOOKINGS

export async function fetchCalendarEvents(): Promise<CalendarEvent[]> {
  const session = readAuthSession();

  if (!session?.access_token) {
    console.error("No access token found");
    return [];
  }

  if (!API_URL) {
    throw new Error("NEXT_PUBLIC_API_URL is not configured");
  }

  const response = await fetch(
    `${API_URL}/sms/calendar/bookings`,
    {
      method: "GET",

      headers: {
        Authorization: `Bearer ${session.access_token}`,
      },
    }
  );

  const responseText = await response.text();

 

  if (!response.ok) {
    throw new Error(
      `Failed to fetch calendar bookings: ${response.status} ${response.statusText} - ${responseText}`
    );
  }

  const data: CalendarBookingApiResponse[] =
    JSON.parse(responseText);



  const browserTimeZone = getBrowserTimeZone();

 

  const mappedEvents = data.map(
    mapBookingToCalendarEvent
  );

 
  return mappedEvents;
}
  