# Telnyx Call Test

A browser phone dialer for testing inbound and outbound voice calls with Telnyx. The Next.js frontend handles microphone access, call audio, and call controls through the Telnyx WebRTC SDK. The FastAPI backend obtains temporary calling tokens and receives webhooks to transfer inbound phone calls to the browser.

## Features

- Connect and disconnect a browser calling session.
- Dial phone numbers using a text input or on-screen keypad.
- Answer or reject incoming calls and display the caller number when available.
- Mute, unmute, and hang up calls.
- Display backend health, missing configuration, and inbound diagnostics.
- Log webhook events, transfer responses, recording events, and hangup details.

The interface handles one call at a time. A second incoming call is rejected as busy while another call is tracked. The keypad edits the destination number; it does not send DTMF tones during a call.

## Technology

| Component | Implementation |
| --- | --- |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4 |
| Browser calling | `@telnyx/webrtc` |
| Backend | Python, FastAPI, Uvicorn |
| Telnyx HTTP requests | `httpx` |
| Configuration | `python-dotenv` and Next.js environment variables |

## Project structure

```text
telnyx-call-test/
├── README.md
├── backend/
│   ├── main.py                 # FastAPI app, CORS, root and health routes
│   ├── requirements.txt        # Python dependencies
│   ├── .env                    # Local credentials; excluded from Git
│   └── routes/
│       ├── __init__.py
│       └── telnyx.py            # Token creation, webhook, inbound transfer
└── frontend/
    ├── package.json            # Dependencies and npm scripts
    ├── next.config.ts
    ├── public/                 # Static assets
    └── src/
        ├── app/
        │   ├── page.tsx        # Dialer and WebRTC session lifecycle
        │   ├── layout.tsx      # Application layout
        │   └── globals.css     # Interface styles
        └── lib/
            └── api.ts          # Backend requests and error formatting
```

## Prerequisites

- Python 3.10 or newer, with pip and virtual environment support.
- Node.js and npm compatible with the Next.js version in `frontend/package.json`.
- A Telnyx API key, voice-capable phone number, and telephony credential associated with a SIP connection.
- Outbound calling configured in Telnyx, including the relevant outbound voice profile and destination permissions.
- A browser with microphone access and WebRTC support.
- For inbound calls: a Call Control application receiving calls to your Telnyx number, and a public HTTPS URL forwarding webhooks to the backend.

Calls use your Telnyx account and may incur charges. Use a destination you control for testing.

## Configuration

Create or update `backend/.env`:

```dotenv
TELNYX_API_KEY=your_telnyx_api_key
TELNYX_WEBRTC_CREDENTIAL_ID=your_telephony_credential_id
TELNYX_PHONE_NUMBER=+13125551234
TELNYX_CONNECTION_ID=your_call_control_application_connection_id
```

| Variable | Purpose |
| --- | --- |
| `TELNYX_API_KEY` | Authenticates backend token requests, credential lookup, and call transfers. |
| `TELNYX_WEBRTC_CREDENTIAL_ID` | Identifies the credential for browser login and the SIP destination of inbound transfers. |
| `TELNYX_PHONE_NUMBER` | Caller number returned to the browser; also the fallback caller number for inbound transfers. Use E.164 format. |
| `TELNYX_CONNECTION_ID` | Reported by the health endpoint when missing. The current calling and transfer handlers do not read this value. |

Keep the API key and permanent SIP credentials on the backend. The browser receives a temporary token and caller number.

The frontend defaults to `http://localhost:8000`. To override it, create `frontend/.env.local`:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Use a base URL without a trailing slash. Restart Next.js after changing this value; rebuild it when changing the value for a production build. Restart FastAPI after editing backend environment variables.

Backend CORS permits `http://localhost:3000`. If the frontend uses another origin, update `allow_origins` in `backend/main.py`.

## Run locally

These commands use PowerShell and start from the `telnyx-call-test` directory.

### 1. Install and start the backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

If a working `.venv` already exists, skip its creation. Using its Python executable directly avoids the need to activate it. Run Uvicorn from `backend` so application imports and the local `.env` resolve correctly.

- API: http://localhost:8000
- Health: http://localhost:8000/health
- Interactive API documentation: http://localhost:8000/docs

### 2. Install and start the frontend

Open another terminal in `telnyx-call-test`:

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. Both servers must remain running.

On macOS or Linux, use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe` for backend commands.

## How calling works

### Connection and outbound calls

1. Clicking **Connect** sends `POST /webrtc/token` to FastAPI.
2. FastAPI requests a temporary Telnyx token for the telephony credential and returns it with the caller number.
3. The frontend creates a `TelnyxRTC` client using `login_token` and waits for its ready notification.
4. Clicking the green call button requests microphone access and creates an audio-only WebRTC call to the destination.
5. SDK notifications update the interface as the call rings, connects, or ends. Audio plays through the page's audio element.

Outbound browser calls are created through the SDK. There is no implemented `POST /call` endpoint or separate API call test mode.

### Inbound phone calls

```text
Caller → Telnyx phone number → Call Control application
                                  ↓ webhook
                           FastAPI /webhooks/telnyx
                                  ↓ transfer request
                   sip:<credential-username>@sip.telnyx.com
                                  ↓
                         Connected browser → Answer
```

For `call.initiated` events with direction `incoming`, the backend retrieves the configured credential's SIP username and requests a transfer to that SIP address with a 30-second timeout. The original caller number is passed through when available.

To test this flow:

1. Configure the Telnyx phone number to deliver calls to the intended Call Control application.
2. Expose the backend through an HTTPS tunnel or a reachable HTTPS backend host.
3. Set the application's webhook URL to `<public-backend-url>/webhooks/telnyx`.
4. Open the frontend, click **Connect**, and wait for **Telnyx connected**.
5. Call the Telnyx number from another phone, then select **Answer** or **Reject**.

Telnyx cannot deliver webhooks to localhost. The browser must use the same telephony credential targeted by the transfer. Browser call state comes from SDK notifications.

## Using the dialer

1. Confirm **Backend online** and review any missing configuration message.
2. Click **Connect**.
3. Enter an E.164 number such as `+13125551234`, or use the keypad and **+** button. Destination numbers accept `+`, country code, and digits only.
4. Click the green phone icon and allow microphone access.
5. Use **Mute / Unmute** and **Hang up** during the call.
6. Click **Disconnect** to end the browser session; it also attempts to hang up the current call.

Microphone access requires localhost or HTTPS. Keep the page open while calling or waiting for incoming calls. Expand **Inbound diagnostic** to inspect the last call notification. Use the browser console and backend terminal for additional diagnostics.

## Backend API

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/` | Returns API identification and running status. |
| `GET` | `/health` | Returns `status: "ok"` and missing environment variable names. |
| `POST` | `/webrtc/token` | Returns a temporary token and `caller_number`. The frontend sends `{}`. |
| `POST` | `/webhooks/telnyx` | Routes incoming call events, logs diagnostic events, and acknowledges the event type. |

Check health from PowerShell:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/health
```

Example response when all checked values are present:

```json
{
  "status": "ok",
  "missing_configuration": []
}
```

Health checks configuration presence only. They do not validate credentials, test Telnyx connectivity, or confirm that calls can be placed.

The token endpoint returns HTTP 503 for missing required configuration, 504 for an upstream timeout, and 502 for a connection failure or empty token. Telnyx errors are forwarded with their upstream status.

The webhook acknowledges events even when inbound routing raises an exception. Inspect backend logs and `TRANSFER STATUS` to determine whether routing succeeded.

## Development commands

Run inside `frontend`:

```powershell
npm run lint
npm run build
npm run start
```

`lint` runs ESLint. `build` creates a production frontend build. `start` serves that build and requires a successful build first. Run the backend separately. The project does not currently define a dedicated automated test suite.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Backend unavailable | Start FastAPI, verify `NEXT_PUBLIC_API_URL`, and check the allowed CORS origin. |
| Missing configuration | Set the named variables in `backend/.env`, restart FastAPI, then click **Refresh**. |
| Telnyx connection fails | Check the API key, telephony credential, SIP connection, and backend/browser error details. |
| Destination rejected | Use E.164 format and check outbound voice configuration and destination permissions. |
| Microphone access fails | Use localhost or HTTPS and allow microphone permission. |
| Call connects without audio | Check microphone/speaker selection, browser audio permissions, mute state, and the audio controls. |
| Incoming call never appears | Keep the browser connected; check the number's Call Control application, public webhook URL, credential SIP username, and transfer logs. |
| Webhook succeeds but transfer fails | The acknowledgement does not confirm transfer success. Inspect `TRANSFER STATUS` and routing exception logs. |
| Recording event appears | This backend logs recording events but does not start recordings. Inspect recording settings elsewhere in the Telnyx account. |

## Current limitations

This is a local test application. The token endpoint has no application authentication, and the webhook does not verify Telnyx signatures. Before shared or production deployment, add token request authentication and authorization, webhook signature verification, rate limiting, HTTPS, and appropriate CORS origins.

Inbound transfers have no implemented event deduplication or retry mechanism. Logs include phone numbers and call identifiers. There is no persistent call history, recording storage, or application-level automatic token renewal. Backend dependency versions are not pinned in `requirements.txt`.
