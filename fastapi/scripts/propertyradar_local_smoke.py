"""Exercise real localhost routes without exposing secrets or calling PropertyRadar."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values

from app.db import SessionLocal
from app.models import User
from app.propertyradar.models import WebhookEvent


def main():
    # app.db loads the backend environment before auth captures SECRET_KEY.
    from app.auth import create_access_token

    secret = dotenv_values(Path(__file__).resolve().parents[1] / ".env").get(
        "PROPERTYRADAR_WEBHOOK_SECRET"
    )
    if not secret:
        raise SystemExit("Local webhook secret is not configured")
    payload = {
        "RadarID": "TEST-PROPERTY-001",
        "ListName": "ListingIQ - Chicago - Expired",
        "TriggerType": "New Match",
        "Change1": "Listing Status:Active:Expired",
    }
    response = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail-with-body",
            "--request",
            "POST",
            "http://127.0.0.1:8000/api/v1/webhooks/propertyradar",
            "--header",
            "Authorization: Bearer " + secret,
            "--header",
            "Content-Type: application/json",
            "--data",
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if response.returncode:
        raise SystemExit("Local curl failed: " + response.stderr + response.stdout)
    data = json.loads(response.stdout)
    assert data["status"] == "test", data
    print("Local webhook curl: accepted and marked test")
    with SessionLocal() as db:
        row = db.get(WebhookEvent, data["event_id"])
        assert row and row.is_test and row.processing_status == "test"
        print("Durable webhook row verified:", row.id)
        admin = db.query(User).filter_by(role="platform_admin", is_active=True).first()
        if admin:
            import requests

            token = create_access_token({"user_id": admin.id})
            response = requests.get(
                "http://127.0.0.1:8000/api/v1/platform-admin/integrations/propertyradar/events?q=TEST-PROPERTY-001",
                headers={"Authorization": "Bearer " + token},
                timeout=10,
            )
            response.raise_for_status()
            assert any(item["id"] == row.id for item in response.json()["items"])
            print("Event visible through authenticated Internal Console API")
        else:
            print(
                "No existing platform administrator found; authenticated live check skipped"
            )


if __name__ == "__main__":
    main()
