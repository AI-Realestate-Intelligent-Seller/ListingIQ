"""Generate a simulated Telnyx inbound SMS webhook.

The simulator posts only to the webhook URL supplied by the user (localhost by
default). It does not contact Telnyx and does not deliver a real SMS.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


DEFAULT_WEBHOOK_URL = "http://127.0.0.1:5000/webhooks"
DEFAULT_FROM_NUMBER = "+13125848528"
DEFAULT_TO_NUMBER = "+12245798015"
PHONE_NUMBER_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


class SimulationValidationError(ValueError):
    """Raised when a simulated inbound message is invalid."""


def build_inbound_webhook(
    from_number: str,
    to_number: str,
    text: str,
) -> dict[str, Any]:
    """Build a Telnyx-compatible ``message.received`` webhook payload."""

    if not PHONE_NUMBER_PATTERN.fullmatch(from_number):
        raise SimulationValidationError("'from' must be a valid E.164 phone number")
    if not PHONE_NUMBER_PATTERN.fullmatch(to_number):
        raise SimulationValidationError("'to' must be a valid E.164 phone number")
    if not text.strip():
        raise SimulationValidationError("'text' must be a non-empty string")

    return {
        "data": {
            "event_type": "message.received",
            "payload": {
                "from": {"phone_number": from_number},
                "id": str(uuid4()),
                "text": text,
                "to": [{"phone_number": to_number, "status": "webhook_delivered"}],
                "type": "SMS",
                "simulation": True,
            },
        },
        "meta": {"attempt": 1, "simulation": True},
    }


def send_inbound_webhook(webhook_url: str, payload: dict[str, Any]) -> tuple[int, str]:
    """Post the simulated webhook and return its HTTP status and response body."""

    request = Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-SMS-Simulation": "true"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Webhook returned HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(
            f"Cannot reach the Node webhook at {webhook_url}. Start it with: node server.js"
        ) from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a free simulated inbound SMS")
    parser.add_argument("--text", help="Inbound message text; prompts when omitted")
    parser.add_argument("--from-number", default=DEFAULT_FROM_NUMBER)
    parser.add_argument("--to-number", default=DEFAULT_TO_NUMBER)
    parser.add_argument("--webhook-url", default=DEFAULT_WEBHOOK_URL)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the webhook without posting it",
    )
    args = parser.parse_args()

    text = args.text if args.text is not None else input("Simulated inbound message: ")
    try:
        payload = build_inbound_webhook(args.from_number, args.to_number, text)
        if args.dry_run:
            print(json.dumps(payload, indent=2))
            return

        status, response_body = send_inbound_webhook(args.webhook_url, payload)
    except (SimulationValidationError, RuntimeError) as error:
        parser.error(str(error))

    print(f"Simulated inbound SMS accepted (HTTP {status}).")
    print(f"Message ID: {payload['data']['payload']['id']}")
    print("Node will process the message and reply unless it detects a conversation-ending farewell.")
    if response_body.strip():
        print(f"Webhook response: {response_body.strip()}")


if __name__ == "__main__":
    main()
