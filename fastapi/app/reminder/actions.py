"""Dashboard notification targets, including links stored by older versions."""
from urllib.parse import parse_qs, urlencode, urlsplit


def notification_action(notification_type=None, action_url=None, conversation_id=None, booking_id=None):
    if conversation_id:
        return {"view": "followups", "conversation_id": conversation_id}
    if booking_id:
        return {"view": "calendar", "booking_id": booking_id}
    parsed = urlsplit(action_url or "")
    query = parse_qs(parsed.query)
    view = query.get("view", [None])[0]
    allowed = {"overview", "directory", "assignments", "leads", "campaigns", "followups", "calendar"}
    if view not in allowed:
        view = {"/assignments": "leads", "/agent": "calendar", "/calendar": "calendar", "/followups": "followups"}.get(parsed.path)
    if parsed.path.startswith("/calendar/bookings/"):
        view = "calendar"
        query["booking_id"] = [parsed.path.rsplit("/", 1)[-1]]
    if not view:
        view = {"LEAD_ASSIGNED": "leads", "CUSTOMER_REPLY": "followups", "booking_created": "calendar", "BOOKING_REMINDER": "calendar"}.get(notification_type, "overview")
    action = {"view": view}
    for key in ("conversation_id", "booking_id", "lead_id", "campaign_id"):
        value = query.get(key, [""])[0]
        if value.isdigit() and int(value) > 0:
            action[key] = int(value)
    return action


def dashboard_url(action):
    return "/dashboard?" + urlencode(action)


def action_for_notification(notification):
    return notification_action(
        notification.type, notification.action_url,
        notification.conversation_id, notification.booking_id,
    )
