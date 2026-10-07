import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter(
    prefix="",
    tags=["Telnyx"],
)

TELNYX_API_BASE_URL = "https://api.telnyx.com/v2"
recorded_sessions: set[str] = set()


class CallRequest(BaseModel):
    phone_number: str = Field(pattern=r"^\+[1-9]\d{1,14}$")


def get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(
            status_code=503,
            detail=f"{name} is not configured",
        )
    return value


# ---------------------------------------------------------
# INBOUND CALL ROUTING WEBHOOK
# ---------------------------------------------------------
async def get_credential_sip_username() -> str:
    api_key = get_env("TELNYX_API_KEY")
    credential_id = get_env("TELNYX_WEBRTC_CREDENTIAL_ID")

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{TELNYX_API_BASE_URL}/telephony_credentials/{credential_id}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )

    if response.is_error:
        raise RuntimeError(f"Credential lookup failed: {response.status_code} {response.text}")

    sip_username = response.json().get("data", {}).get("sip_username")
    if not sip_username:
        raise RuntimeError("Credential has no sip_username")

    print("USING SIP USERNAME:", sip_username)
    return sip_username

async def route_inbound_to_browser(call: dict) -> None:
    """
    Transfers an inbound phone call to the browser's Telephony
    Credential SIP address so the JWT-authenticated TelnyxRTC
    client receives it.
    """
    api_key = get_env("TELNYX_API_KEY")
    sip_username = await get_credential_sip_username()

    call_control_id = call.get("call_control_id")
    if not call_control_id:
        print("No call_control_id in payload")
        return

    body = {
        "to": f"sip:{sip_username}@sip.telnyx.com",
        # Show the real caller in the browser. If Telnyx rejects this,
        # fall back to get_env("TELNYX_PHONE_NUMBER") only.
        "from": call.get("from") or get_env("TELNYX_PHONE_NUMBER"),
        "timeout_secs": 30,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{TELNYX_API_BASE_URL}/calls/{call_control_id}/actions/transfer",
            headers=headers,
            json=body,
        )

    print("TRANSFER STATUS:", response.status_code, response.text)


    
@router.post("/webhooks/telnyx")
async def telnyx_webhook(payload: dict):
    data = payload.get("data", {})
    event_type = data.get("event_type")
    call = data.get("payload", {})

    print(
        "TELNYX WEBHOOK:",
        "event=", event_type,
        "direction=", call.get("direction"),
        "session=", call.get("call_session_id"),
        "control_id=", call.get("call_control_id"),
        "from=", call.get("from"),
        "to=", call.get("to"),
    )

    # -----------------------------------------------------
    # ROUTE ORIGINAL INBOUND PSTN CALL TO WEBRTC
    # -----------------------------------------------------
    if (
        event_type == "call.initiated"
        and call.get("direction") == "incoming"
    ):
        try:
            await route_inbound_to_browser(call)

        except Exception as exc:
            print(
                "Failed to route inbound call:",
                exc,
            )

   
    # -----------------------------------------------------
    # TELNYX RECORDING EVENTS
    # Logging only — this code does NOT create a recording.
    # -----------------------------------------------------
    if event_type == "call.recording.saved":
        print(
            "!!! TELNYX CREATED A RECORDING !!!",
            "recording_id=", call.get("recording_id"),
            "session=", call.get("call_session_id"),
            "connection_id=", call.get("connection_id"),
            "call_leg_id=", call.get("call_leg_id"),
            "direction=", call.get("direction"),
        )
    if event_type == "call.recording.error":
        print(
            "RECORDING ERROR:",
            call,
        )
    if event_type == "call.hangup":
        print(
            "HANGUP DETAILS:",
            "cause=",
            call.get("hangup_cause"),
            "| source=",
            call.get("hangup_source"),
            "| sip_code=",
            call.get("sip_hangup_cause"),
            "| to=",
            call.get("to"),
        )

    return {
        "status": "received",
        "event_type": event_type,
    }
# ---------------------------------------------------------
# WEBRTC JWT TOKEN
# ---------------------------------------------------------
@router.post("/webrtc/token")
async def create_webrtc_token():
    """
    Generates a temporary JWT for a Telnyx Telephony Credential.

    The same JWT-authenticated TelnyxRTC browser client is used
    for BOTH outbound and inbound WebRTC calls. The permanent SIP
    username/password never needs to be exposed to the frontend.
    """
    api_key = get_env("TELNYX_API_KEY")
    credential_id = get_env("TELNYX_WEBRTC_CREDENTIAL_ID")
    caller_number = get_env("TELNYX_PHONE_NUMBER")

    url = (
        f"{TELNYX_API_BASE_URL}/telephony_credentials/"
        f"{credential_id}/token"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/plain",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="Telnyx WebRTC token request timed out",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to reach Telnyx: {exc}",
        ) from exc

    if response.is_error:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        )

    token = response.text.strip()
    if not token:
        raise HTTPException(
            status_code=502,
            detail="Telnyx returned an empty WebRTC token",
        )

    return {
        "token": token,
        "caller_number": caller_number,
    }