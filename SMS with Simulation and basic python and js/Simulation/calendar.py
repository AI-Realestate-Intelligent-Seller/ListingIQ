"""Local collision-safe meeting calendar for SMS simulations."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, time as clock_time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "Simulation" / "calendar.db"
PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
          id INTEGER PRIMARY KEY,
          phone TEXT NOT NULL,
          name TEXT,
          title TEXT NOT NULL,
          start_at TEXT NOT NULL,
          end_at TEXT NOT NULL,
          join_token TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL
        )
        """
    )
    return connection


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO date and time") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def create_booking(payload: dict[str, Any]) -> dict[str, Any]:
    phone = str(payload.get("phone") or "").replace(" ", "").strip()
    name = str(payload.get("name") or "").strip()
    title = str(payload.get("title") or "Property consultation").strip()
    if not PHONE_PATTERN.fullmatch(phone):
        raise ValueError("phone must be a valid E.164 number")
    if not title:
        raise ValueError("title is required")
    start = parse_time(payload.get("start_at"), "start_at")
    end = parse_time(payload.get("end_at"), "end_at")
    if end <= start:
        raise ValueError("end_at must be later than start_at")
    if (end - start).total_seconds() > 4 * 60 * 60:
        raise ValueError("a meeting cannot be longer than four hours")

    start_value = start.isoformat().replace("+00:00", "Z")
    end_value = end.isoformat().replace("+00:00", "Z")
    token = uuid4().hex
    created = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        overlap = connection.execute(
            "SELECT id, start_at, end_at FROM bookings WHERE start_at < ? AND end_at > ? LIMIT 1",
            (end_value, start_value),
        ).fetchone()
        if overlap:
            raise RuntimeError("That time overlaps an existing booking")
        cursor = connection.execute(
            "INSERT INTO bookings(phone,name,title,start_at,end_at,join_token,created_at) VALUES(?,?,?,?,?,?,?)",
            (phone, name or None, title, start_value, end_value, token, created),
        )
        connection.commit()
        return dict(
            connection.execute("SELECT * FROM bookings WHERE id = ?", (cursor.lastrowid,)).fetchone()
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_bookings() -> list[dict[str, Any]]:
    connection = connect()
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM bookings ORDER BY start_at")]
    finally:
        connection.close()


def list_available_slots(
    timezone_name: str,
    days: int = 14,
    slot_minutes: int = 30,
) -> list[dict[str, str]]:
    """Return unbooked weekday slots from 9 AM through 6 PM local time."""

    zone = ZoneInfo(timezone_name)
    now_utc = datetime.now(timezone.utc)
    local_today = now_utc.astimezone(zone).date()
    connection = connect()
    try:
        busy = [
            (
                datetime.fromisoformat(row["start_at"].replace("Z", "+00:00")),
                datetime.fromisoformat(row["end_at"].replace("Z", "+00:00")),
            )
            for row in connection.execute("SELECT start_at,end_at FROM bookings")
        ]
    finally:
        connection.close()

    slots = []
    for offset in range(days):
        day = local_today + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        cursor = datetime.combine(day, clock_time(9, 0), zone)
        day_end = datetime.combine(day, clock_time(18, 0), zone)
        while cursor + timedelta(minutes=slot_minutes) <= day_end:
            end = cursor + timedelta(minutes=slot_minutes)
            start_utc = cursor.astimezone(timezone.utc)
            end_utc = end.astimezone(timezone.utc)
            if start_utc > now_utc and not any(start_utc < busy_end and end_utc > busy_start for busy_start, busy_end in busy):
                slots.append(
                    {
                        "start_at": start_utc.isoformat().replace("+00:00", "Z"),
                        "end_at": end_utc.isoformat().replace("+00:00", "Z"),
                        "label": cursor.strftime(f"%A, %B %d, %Y at %I:%M %p {timezone_name}"),
                    }
                )
            cursor = end
    return slots


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ListingIQ Calendar</title><style>
:root{font-family:Inter,system-ui,sans-serif;color:#172022;background:#f3f6f3;--green:#176b5b;--line:#dfe6e0}*{box-sizing:border-box}body{margin:0;padding:24px}.shell{max-width:1400px;margin:auto;background:#fff;border:1px solid var(--line);border-radius:22px;box-shadow:0 20px 60px #1c2a2618;overflow:hidden}.top{display:flex;align-items:center;gap:12px;padding:20px 24px;border-bottom:1px solid var(--line)}h1{margin:0;font-size:22px}.sub{color:#71807a;font-size:13px}.spacer{flex:1}button{border:0;border-radius:10px;padding:11px 15px;font-weight:700;cursor:pointer}.primary{color:#fff;background:var(--green)}.secondary{background:#edf3ef;color:#315249}.week-nav{display:flex;align-items:center;gap:8px;padding:14px 20px;border-bottom:1px solid var(--line)}#range{font-weight:700}.calendar{display:grid;grid-template-columns:repeat(7,minmax(150px,1fr));min-height:610px;overflow-x:auto}.day{min-width:150px;border-right:1px solid var(--line);padding:12px}.day:last-child{border:0}.day-head{position:sticky;top:0;padding:5px 2px 13px;background:#fff;color:#68756f;font-size:12px;font-weight:700}.day-head strong{display:block;margin-top:3px;color:#1d2b26;font-size:22px}.booking{margin:8px 0;padding:11px;color:#174c40;background:#e7f4ee;border-left:4px solid var(--green);border-radius:9px;font-size:12px;line-height:1.45}.booking b{display:block}.booking small{display:block;margin-top:5px;color:#5c7169;overflow-wrap:anywhere}dialog{width:min(500px,calc(100% - 30px));border:0;border-radius:18px;padding:0;box-shadow:0 25px 80px #17262255}dialog::backdrop{background:#14201c66}form{padding:24px}form h2{margin:0 0 18px}label{display:block;margin:13px 0;font-size:12px;font-weight:700}input{width:100%;height:43px;margin-top:6px;padding:0 11px;border:1px solid #d7dfd9;border-radius:9px}.actions{display:flex;justify-content:flex-end;gap:9px;margin-top:20px}.error{min-height:18px;color:#a84234;font-size:12px}.empty{padding:20px;color:#84908b;font-size:12px}@media(max-width:760px){body{padding:0}.shell{border-radius:0}.calendar{min-height:calc(100vh - 130px)}}
</style></head><body><main class="shell"><header class="top"><div><h1>Meeting calendar</h1><div class="sub">Simulation scheduling · no overlapping bookings</div></div><div class="spacer"></div><button class="primary" id="newBtn">New booking</button></header><div class="week-nav"><button class="secondary" id="prev">←</button><button class="secondary" id="today">Today</button><button class="secondary" id="next">→</button><span id="range"></span></div><section class="calendar" id="calendar"></section></main>
<dialog id="dialog"><form id="form"><h2>Schedule meeting</h2><label>Phone number<input id="phone" placeholder="+12025550100" required></label><label>Name<input id="name" placeholder="Contact name"></label><label>Meeting title<input id="title" value="Property consultation" required></label><label>Starts<input id="start" type="datetime-local" required></label><label>Ends<input id="end" type="datetime-local" required></label><div class="error" id="error"></div><div class="actions"><button class="secondary" type="button" id="cancel">Cancel</button><button class="primary">Book and send details</button></div></form></dialog>
<script>
let weekStart=startOfWeek(new Date()),bookings=[];const $=id=>document.getElementById(id);function startOfWeek(d){const x=new Date(d);x.setHours(0,0,0,0);x.setDate(x.getDate()-x.getDay());return x}function fmtTime(v){return new Intl.DateTimeFormat([],{hour:'numeric',minute:'2-digit'}).format(new Date(v))}function localInput(d){const x=new Date(d.getTime()-d.getTimezoneOffset()*60000);return x.toISOString().slice(0,16)}
async function load(){bookings=await fetch('/api/bookings').then(r=>r.json());render()}function render(){const end=new Date(weekStart);end.setDate(end.getDate()+6);$('range').textContent=`${weekStart.toLocaleDateString([],{month:'short',day:'numeric'})} – ${end.toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'})}`;$('calendar').replaceChildren();for(let i=0;i<7;i++){const day=new Date(weekStart);day.setDate(day.getDate()+i);const col=document.createElement('div');col.className='day';col.innerHTML=`<div class="day-head">${day.toLocaleDateString([],{weekday:'short'})}<strong>${day.getDate()}</strong></div>`;const matches=bookings.filter(b=>new Date(b.start_at).toDateString()===day.toDateString());if(!matches.length){const e=document.createElement('div');e.className='empty';e.textContent='Available';col.append(e)}matches.forEach(b=>{const card=document.createElement('div');card.className='booking';card.innerHTML=`<b>${fmtTime(b.start_at)}–${fmtTime(b.end_at)}</b><span>${b.title}</span><small>${b.name||b.phone}<br>${b.phone}</small>`;col.append(card)});$('calendar').append(col)}}
$('newBtn').onclick=()=>{const s=new Date();s.setMinutes(s.getMinutes()<30?30:60,0,0);const e=new Date(s.getTime()+30*60000);$('start').value=localInput(s);$('end').value=localInput(e);$('error').textContent='';$('dialog').showModal()};$('cancel').onclick=()=>$('dialog').close();$('prev').onclick=()=>{weekStart.setDate(weekStart.getDate()-7);render()};$('next').onclick=()=>{weekStart.setDate(weekStart.getDate()+7);render()};$('today').onclick=()=>{weekStart=startOfWeek(new Date());render()};$('form').onsubmit=async e=>{e.preventDefault();$('error').textContent='';const payload={phone:$('phone').value.trim(),name:$('name').value.trim(),title:$('title').value.trim(),start_at:new Date($('start').value).toISOString(),end_at:new Date($('end').value).toISOString()};const response=await fetch('/api/bookings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok){$('error').textContent=result.error;return}$('dialog').close();await load();alert(result.confirmation_sent?'Booking saved and confirmation added to ListingIQ.':`Booking saved. Confirmation warning: ${result.confirmation_error}`)};load();setInterval(load,5000);
</script></body></html>"""


class CalendarHandler(BaseHTTPRequestHandler):
    node_url = "http://127.0.0.1:5000/calendar-confirmation"
    public_base_url = "http://127.0.0.1:5052"
    calendar_timezone = "America/Chicago"

    def json_response(self, status: int, body: Any) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            encoded = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        elif self.path == "/api/bookings":
            self.json_response(200, list_bookings())
        elif self.path.startswith("/api/availability"):
            slots = list_available_slots(self.calendar_timezone)
            self.json_response(
                200,
                {
                    "timezone": self.calendar_timezone,
                    "current_time": datetime.now(ZoneInfo(self.calendar_timezone)).isoformat(),
                    "slot_minutes": 30,
                    "slots": slots,
                },
            )
        elif self.path.startswith("/join/"):
            token = self.path.split("?", 1)[0].removeprefix("/join/")
            connection = connect()
            booking = connection.execute("SELECT * FROM bookings WHERE join_token = ?", (token,)).fetchone()
            connection.close()
            if not booking:
                self.json_response(404, {"error": "Meeting link not found"})
                return
            body = f"<!doctype html><title>Meeting room</title><style>body{{font:16px system-ui;display:grid;place-items:center;height:100vh;margin:0;background:#eef4f0}}main{{padding:40px;background:white;border-radius:18px;text-align:center}}</style><main><h1>Meeting room</h1><p>{booking['title']}</p><p>This is a dummy simulation link for {booking['phone']}.</p><button disabled>Video meeting placeholder</button></main>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.json_response(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/bookings":
            self.json_response(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            booking = create_booking(payload)
        except RuntimeError as error:
            self.json_response(409, {"error": str(error)})
            return
        except (ValueError, json.JSONDecodeError) as error:
            self.json_response(400, {"error": str(error)})
            return

        join_url = f"{self.public_base_url}/join/{booking['join_token']}"
        zone = ZoneInfo(self.calendar_timezone)
        start = datetime.fromisoformat(booking["start_at"].replace("Z", "+00:00")).astimezone(zone)
        end = datetime.fromisoformat(booking["end_at"].replace("Z", "+00:00")).astimezone(zone)
        name = booking["name"] or "there"
        message = (
            f"Thanks, {name}. Your meeting with Bobbie is confirmed for "
            f"{start.strftime('%b %d, %Y at %I:%M %p')}–{end.strftime('%I:%M %p')} "
            f"{self.calendar_timezone}. "
            f"Join here: {join_url}"
        )
        confirmation_sent = False
        confirmation_error = None
        try:
            request = Request(
                self.node_url,
                data=json.dumps({"to": booking["phone"], "text": message, "booking_id": booking["id"]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=10) as response:
                confirmation_sent = response.status == 200
        except (HTTPError, URLError) as error:
            confirmation_error = str(error)
        self.json_response(201, {**booking, "join_url": join_url, "message": message, "confirmation_sent": confirmation_sent, "confirmation_error": confirmation_error})

    def log_message(self, message_format: str, *args: Any) -> None:
        print(f"[calendar] {message_format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local simulation calendar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5052)
    parser.add_argument("--node-url", default="http://127.0.0.1:5000/calendar-confirmation")
    parser.add_argument("--public-base-url", default="http://127.0.0.1:5052")
    parser.add_argument("--timezone", default="America/Chicago")
    args = parser.parse_args()
    try:
        ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        parser.error(f"Unknown timezone: {args.timezone}")
    CalendarHandler.node_url = args.node_url
    CalendarHandler.public_base_url = args.public_base_url.rstrip("/")
    CalendarHandler.calendar_timezone = args.timezone
    server = ThreadingHTTPServer((args.host, args.port), CalendarHandler)
    print(f"Simulation calendar: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
