import os
import requests
from ..core.config import settings

def send_message(to: str, text: str, simulation: bool = False) -> dict:
    cfg = settings.get('telnyx', {})
    mode = cfg.get('mode', os.getenv('SMS_MODE', 'simulation'))
    if mode == 'simulation':
        url = cfg.get('simulation_url', 'http://127.0.0.1:5051/v2/messages')
        payload = {"to": to, "text": text, "simulation": True}
        resp = requests.post(url, json=payload, timeout=10)
        try:
            return resp.json()
        except Exception:
            return {"ok": False, "status_code": resp.status_code, "text": resp.text}
    # production Telnyx path
    api_url = cfg.get('api_url', 'https://api.telnyx.com/v2/messages')
    api_key = cfg.get('api_key') or os.getenv('TELNYX_API_KEY')
    if not api_key:
        raise RuntimeError('TELNYX_API_KEY not configured')
    from_number = cfg.get('from_number') or os.getenv('TELNYX_FROM_NUMBER')
    if not from_number:
        raise RuntimeError('Telnyx from_number not configured')
    body = {"from": from_number, "to": to, "text": text}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = requests.post(api_url, json=body, headers=headers, timeout=10)
    try:
        return resp.json()
    except Exception:
        return {"ok": False, "status_code": resp.status_code, "text": resp.text}
