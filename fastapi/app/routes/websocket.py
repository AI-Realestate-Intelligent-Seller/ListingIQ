import asyncio
import json
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import SessionLocal
from ..auth import decode_access_token
from ..models import User


router = APIRouter()


class CalendarConnectionManager:
    def __init__(self):
        self.connections: dict[int, set[WebSocket]] = defaultdict(set)
        self.loop: asyncio.AbstractEventLoop | None = None

    async def connect(
        self,
        websocket: WebSocket,
        user_id: int,
    ):
        self.connections[user_id].add(websocket)

        # Save the event loop used by FastAPI/Uvicorn.
        # The synchronous booking code will use this loop
        # to send WebSocket messages safely.
        self.loop = asyncio.get_running_loop()

        print(
            f"Calendar WebSocket connected: "
            f"user_id={user_id}, "
            f"connections={len(self.connections[user_id])}"
        )

    def disconnect(
        self,
        websocket: WebSocket,
        user_id: int,
    ):
        connections = self.connections.get(user_id)

        if not connections:
            return

        connections.discard(websocket)

        if not connections:
            self.connections.pop(user_id, None)

        print(
            f"Calendar WebSocket disconnected: "
            f"user_id={user_id}"
        )

    async def send_to_user(
        self,
        user_id: int,
        message: dict,
    ):
        connections = self.connections.get(user_id)

        if not connections:
            print(
                f"No active calendar WebSocket for "
                f"user_id={user_id}"
            )
            return

        disconnected = []

        for websocket in list(connections):
            try:
                await websocket.send_json(message)

            except Exception as error:
                print(
                    f"Failed to send WebSocket message: "
                    f"{error}"
                )

                disconnected.append(websocket)

        for websocket in disconnected:
            self.disconnect(websocket, user_id)


calendar_manager = CalendarConnectionManager()


def authenticate_token(token: str) -> User | None:
    """
    Authenticate the JWT sent by the browser when
    the WebSocket connection is opened.
    """

    if not token:
        return None

    try:
        payload = decode_access_token(token)

        user_id = payload.get("user_id")

        if not user_id:
            return None

        session = SessionLocal()

        try:
            user = (
                session.query(User)
                .filter(User.id == int(user_id))
                .first()
            )

            if not user:
                return None

            if not user.is_active:
                return None

            return user

        finally:
            session.close()

    except Exception as error:
        print(
            f"WebSocket authentication failed: "
            f"{error}"
        )

        return None


@router.websocket("/ws/calendar")
async def calendar_websocket(
    websocket: WebSocket,
):
    user_id = None

    try:
        # Accept WebSocket connection
        await websocket.accept()

        print("Calendar WebSocket connection accepted")

        # Browser sends authentication message first
        raw_message = await websocket.receive_text()

        try:
            message = json.loads(raw_message)

        except json.JSONDecodeError:
            print(
                "Calendar WebSocket rejected: "
                "invalid JSON"
            )

            await websocket.close(code=1008)

            return

        if message.get("type") != "auth":
            print(
                "Calendar WebSocket rejected: "
                "missing auth message"
            )

            await websocket.close(code=1008)

            return

        token = message.get("token")

        # Authenticate JWT
        user = authenticate_token(token)

        if not user:
            print(
                "Calendar WebSocket rejected: "
                "invalid token"
            )

            await websocket.close(code=1008)

            return

        user_id = user.id

        # Register connection
        await calendar_manager.connect(
            websocket,
            user_id,
        )

        # Tell browser authentication succeeded
        await websocket.send_json(
            {
                "type": "authenticated",
                "user_id": user.id,
            }
        )

        print(
            f"Calendar WebSocket authenticated: "
            f"user_id={user.id}"
        )

        # Keep connection alive
        while True:
            raw_message = await websocket.receive_text()

            try:
                message = json.loads(raw_message)

            except json.JSONDecodeError:
                continue

            # Optional heartbeat
            if message.get("type") == "ping":
                await websocket.send_json(
                    {
                        "type": "pong"
                    }
                )

    except WebSocketDisconnect:
        if user_id is not None:
            calendar_manager.disconnect(
                websocket,
                user_id,
            )

    except Exception as error:
        print(
            f"Calendar WebSocket error: "
            f"user_id={user_id}, "
            f"error={error}"
        )

        if user_id is not None:
            calendar_manager.disconnect(
                websocket,
                user_id,
            )


async def broadcast_calendar_event(
    user_id: int,
    event_type: str,
    booking: dict,
):
    """
    Send a calendar event to all WebSocket connections
    belonging to this user.
    """

    await calendar_manager.send_to_user(
        user_id,
        {
            "type": event_type,
            "booking": booking,
        },
    )


def broadcast_calendar_event_sync(
    user_id: int,
    event_type: str,
    booking: dict,
):
    """
    Called from synchronous booking code.

    The booking endpoint runs synchronously, while
    WebSocket communication runs on FastAPI's async
    event loop.

    This safely schedules the WebSocket broadcast
    on that event loop.
    """

    loop = calendar_manager.loop

    if loop is None:
        print(
            "Calendar WebSocket broadcast skipped: "
            "no active WebSocket event loop"
        )

        return

    if loop.is_closed():
        print(
            "Calendar WebSocket broadcast skipped: "
            "event loop is closed"
        )

        return

    try:
        asyncio.run_coroutine_threadsafe(
            broadcast_calendar_event(
                user_id=user_id,
                event_type=event_type,
                booking=booking,
            ),
            loop,
        )

        print(
            f"Calendar WebSocket broadcast scheduled: "
            f"user_id={user_id}, "
            f"event_type={event_type}"
        )

    except Exception as error:
        print(
            f"Calendar WebSocket broadcast failed: "
            f"{error}"
        )





async def broadcast_notification_event(
    user_id: int,
    notification: dict,
):
    await calendar_manager.send_to_user(
        user_id,
        {
            "type": "notification_created",
            "notification": notification,
        },
    )

def broadcast_notification_event_sync(
    user_id: int,
    notification: dict,
):
    loop = calendar_manager.loop

    if loop is None:
        print(
            "Notification WebSocket broadcast skipped: "
            "no active WebSocket event loop"
        )
        return

    if loop.is_closed():
        print(
            "Notification WebSocket broadcast skipped: "
            "event loop is closed"
        )
        return

    try:
        asyncio.run_coroutine_threadsafe(
            broadcast_notification_event(
                user_id=user_id,
                notification=notification,
            ),
            loop,
        )

        print(
            f"Notification WebSocket broadcast scheduled: "
            f"user_id={user_id}"
        )

    except Exception as error:
        print(
            f"Notification WebSocket broadcast failed: "
            f"{error}"
        )