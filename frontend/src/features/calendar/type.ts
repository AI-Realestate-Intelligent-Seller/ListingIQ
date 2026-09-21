export type CalendarEventType =
  | "showing"
  | "call"
  | "follow_up"
  | "meeting"
  | "open_house";

export interface CalendarEvent {
  id: string;
  title: string;

  date: string;
  endDate: string;

  startTime: string;
  endTime: string;

  type: CalendarEventType;

  description?: string;
  leadName?: string;
  propertyAddress?: string;
  agentName?: string;

  status: "confirmed" | "pending" | "cancelled";
}

export type CalendarView = "month" | "week" | "day";