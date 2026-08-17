"""Local Telnyx-compatible outbound SMS simulator.

This module never contacts Telnyx.  It can be imported directly in tests or
run as a small HTTP service that accepts ``POST /v2/messages``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from inbound import build_inbound_webhook, send_inbound_webhook
except ImportError:  # Imported as Simulation.outbound
    from Simulation.inbound import build_inbound_webhook, send_inbound_webhook


DEFAULT_FROM_NUMBER = "+12245798015"
PHONE_NUMBER_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOBBIE_KNOWLEDGE_PDF = PROJECT_ROOT / "data" / "knowledge" / "Bobbie_Fisher_REMAX_Comprehensive_Profile_and_AI_Knowledge_Base.pdf"
RAG_STOP_WORDS = {
    "about", "and", "are", "bobbie", "does", "for", "from", "has", "have",
    "her", "how", "remax", "she", "tell", "that", "the", "their", "this",
    "what", "when", "where", "which", "who", "why", "with", "you", "your",
}


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load simple .env values without replacing exported environment values."""

    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            os.environ.setdefault(key, value)


class BobbieKnowledgeIndex:
    """Lazy local lexical RAG index over Bobbie's PDF knowledge document."""

    def __init__(self, pdf_path: Path = BOBBIE_KNOWLEDGE_PDF) -> None:
        self.pdf_path = pdf_path
        self.chunks: list[dict[str, Any]] | None = None
        self.document_frequency: Counter[str] = Counter()
        self.lock = threading.Lock()

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return [
            token
            for token in re.findall(r"[a-z0-9'/-]+", value.lower())
            if len(token) > 2 and token not in RAG_STOP_WORDS
        ]

    def _load(self) -> None:
        if self.chunks is not None:
            return
        with self.lock:
            if self.chunks is not None:
                return
            if not self.pdf_path.is_file():
                raise RuntimeError(f"Bobbie knowledge PDF not found: {self.pdf_path}")
            try:
                result = subprocess.run(
                    ["pdftotext", "-layout", str(self.pdf_path), "-"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeError(f"Could not extract Bobbie knowledge PDF: {error}") from error

            chunks = []
            for page_number, raw_page in enumerate(result.stdout.split("\f"), start=1):
                page = re.sub(r"[ \t]+", " ", raw_page).strip()
                if not page:
                    continue
                paragraphs = [part.strip() for part in re.split(r"\n\s*\n", page) if part.strip()]
                buffer = ""
                for paragraph in paragraphs:
                    if buffer and len(buffer) + len(paragraph) > 1300:
                        chunks.append({"page": page_number, "text": buffer})
                        buffer = ""
                    buffer += ("\n\n" if buffer else "") + paragraph
                if buffer:
                    chunks.append({"page": page_number, "text": buffer})
            for index, chunk in enumerate(chunks, start=1):
                chunk["id"] = f"bobbie-p{chunk['page']}-c{index}"
                chunk["tokens"] = self._tokens(chunk["text"])
                self.document_frequency.update(set(chunk["tokens"]))
            self.chunks = chunks
            print(f"[bobbie-rag] Indexed {len(chunks)} chunks from {len(result.stdout.split(chr(12)))} PDF pages.")

    def search(self, query: str, limit: int = 4) -> dict[str, Any]:
        self._load()
        assert self.chunks is not None
        query_tokens = self._tokens(query)
        scored = []
        for chunk in self.chunks:
            counts = Counter(chunk["tokens"])
            score = 0.0
            for token in query_tokens:
                frequency = counts[token]
                if frequency:
                    score += (1 + math.log(frequency)) * math.log(
                        1 + len(self.chunks) / max(1, self.document_frequency[token])
                    )
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        matches = [
            {
                "id": chunk["id"],
                "page": chunk["page"],
                "score": round(score, 3),
                "text": chunk["text"],
            }
            for score, chunk in scored[: max(1, min(limit, 6))]
        ]
        return {
            "query": query,
            "document": self.pdf_path.name,
            "document_status": "Draft for verification and approval; preserve cautions and do not upgrade unverified claims.",
            "matches": matches,
        }


BOBBIE_KNOWLEDGE = BobbieKnowledgeIndex()


class SimulationValidationError(ValueError):
    """Raised when a simulated outbound message is invalid."""


def simulate_outbound_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a Telnyx-like queued response without sending a real SMS."""

    from_number = payload.get("from") or DEFAULT_FROM_NUMBER
    to_number = payload.get("to")
    text = payload.get("text")

    if not isinstance(from_number, str) or not PHONE_NUMBER_PATTERN.fullmatch(from_number):
        raise SimulationValidationError("'from' must be a valid E.164 phone number")
    if not isinstance(to_number, str) or not PHONE_NUMBER_PATTERN.fullmatch(to_number):
        raise SimulationValidationError("'to' must be a valid E.164 phone number")
    if not isinstance(text, str) or not text.strip():
        raise SimulationValidationError("'text' must be a non-empty string")

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "data": {
            "id": str(uuid4()),
            "record_type": "message",
            "direction": "outbound",
            "type": "SMS",
            "from": {"phone_number": from_number},
            "to": [{"phone_number": to_number, "status": "queued"}],
            "text": text,
            "received_at": now,
            "sent_at": None,
            "completed_at": None,
            "cost": {"amount": "0.0000", "currency": "USD"},
            "simulation": True,
        }
    }


def is_conversation_closing(text: str) -> bool:
    """Return true when an SMS clearly ends the conversation."""

    normalized = re.sub(r"^[^\w]+", "", text.strip(), flags=re.UNICODE)
    if not normalized or "?" in normalized:
        return False
    farewell = re.search(
        r"(?:^|[,.!]\s*)(?:bye(?:\s+for\s+now)?|good\s*bye|take\s+care|"
        r"have\s+a\s+(?:(?:good|great|nice)\s+(?:day|evening|night|weekend|one)|"
        r"good\s+rest\s+of\s+(?:your|the)\s+day)|"
        r"you\s+too(?:,?\s*(?:bye|good\s*bye))?|"
        r"talk\s+(?:soon|then|tomorrow)(?:\s+at\s+[\w:]+(?:\s*[ap]m)?)?|"
        r"see\s+you\s+(?:soon|then|tomorrow(?:\s+at\s+[\w:]+(?:\s*[ap]m)?)?|"
        r"at\s+[\w:]+(?:\s*[ap]m)?))"
        r"\s*[,!.]*(?:\s+[A-Za-z][A-Za-z'’\-]*)?[!.]*$",
        normalized,
        re.IGNORECASE,
    )
    polite_close = re.fullmatch(
        r"(?:thanks?|thank\s+you),?\s+(?:you\s+(?:too|as\s+well)|same\s+to\s+you)\s*[!.]*",
        normalized,
        re.IGNORECASE,
    )
    appointment_close = re.fullmatch(
        r"(?:sounds\s+good|looking\s+forward\s+to\s+it)[,!.].*\b"
        r"(?:see\s+you|talk)\s+(?:soon|then|tomorrow|at\s+[\w:]+(?:\s*[ap]m)?)\s*[!.]*",
        normalized,
        re.IGNORECASE,
    )
    return bool(farewell or polite_close or appointment_close)


RECIPIENT_STYLES = (
    {
        "name": "friendly-curious",
        "behavior": "Be approachable. First ask why this property caught the sender's attention. Once one useful answer is given, discuss the property naturally and become open to a short call if it would help.",
    },
    {
        "name": "direct-practical",
        "behavior": "Be direct and brief. First ask what the sender is actually proposing. Focus on process, value, or next steps rather than running a credentials checklist.",
    },
    {
        "name": "cautious-verifier",
        "behavior": "Be politely cautious. You may first ask who the sender is or how they reached you, but accept a clear answer and do not keep interrogating them.",
    },
    {
        "name": "market-curious",
        "behavior": "Be curious about the property and market. First ask what the sender thinks could be different from the prior sale attempt. If the reply is useful, reveal some conditional openness.",
    },
    {
        "name": "busy-owner",
        "behavior": "Keep replies especially short and initially prefer text. Ask one practical question, then either progress or politely close instead of extending the conversation indefinitely.",
    },
)


def recipient_style(contact: str) -> dict[str, str]:
    digits = re.sub(r"\D", "", contact)
    selector = int(digits[-2:] or "0") % len(RECIPIENT_STYLES)
    return RECIPIENT_STYLES[selector]


def fit_recipient_sms(value: str, max_length: int = 200) -> str:
    """Return a complete, privacy-safe SMS without cutting through a word."""

    text = " ".join(value.strip('"“”').split())
    if re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, re.IGNORECASE):
        text = "I’d rather keep this conversation by text for now."
    if re.search(r"\bemail(?:'s| is)? fine\b.{0,80}\b(?:prefer|keep|stick to)\b.{0,30}\btext\b", text, re.IGNORECASE):
        text = "I’d rather keep this conversation by text for now."
    if text.count("?") > 1:
        first_question = text.index("?")
        text = text[: first_question + 1] + text[first_question + 1 :].replace("?", ".")
    if len(text) <= max_length:
        return text
    prefix = text[:max_length]
    sentence_end = max(prefix.rfind("."), prefix.rfind("!"), prefix.rfind("?"))
    if sentence_end >= 70:
        return prefix[: sentence_end + 1].strip()
    word_end = prefix.rfind(" ")
    return prefix[: max(1, word_end)].rstrip(" ,:;-") + "…"


class AutomatedRecipient:
    """Use DeepSeek to play one recipient at a time and feed replies to Node."""

    def __init__(
        self,
        webhook_url: str,
        max_replies: int,
        delay_seconds: float,
        max_parallel: int,
    ) -> None:
        self.webhook_url = webhook_url
        self.max_replies = max_replies
        self.delay_seconds = delay_seconds
        self.parallel_slots = threading.Semaphore(max_parallel)
        self.histories: dict[str, list[dict[str, str]]] = {}
        self.reply_counts: dict[str, int] = {}
        self.active_contacts: set[str] = set()
        self.lock = threading.Lock()

    def schedule_reply(self, outbound_payload: dict[str, Any]) -> None:
        contact = str(outbound_payload["to"])
        if is_conversation_closing(str(outbound_payload["text"])):
            print(
                f"[recipient-simulation] Bobbie closed the conversation with {contact}; "
                "no further inbound reply will be generated."
            )
            return
        with self.lock:
            if contact in self.active_contacts:
                print(f"[recipient-simulation] A reply is already pending for {contact}.")
                return
            count = self.reply_counts.get(contact, 0)
            if self.max_replies > 0 and count >= self.max_replies:
                print(
                    f"[recipient-simulation] Turn limit reached for {contact} "
                    f"({self.max_replies} automatic inbound replies)."
                )
                try:
                    webhook = build_inbound_webhook(
                        contact,
                        str(outbound_payload["from"]),
                        "(No reply)",
                    )
                    send_inbound_webhook(self.webhook_url, webhook)
                except Exception as error:
                    print(f"[recipient-simulation] Could not report completion for {contact}: {error}")
                return
            self.active_contacts.add(contact)

        thread = threading.Thread(
            target=self._reply,
            args=(outbound_payload,),
            daemon=True,
        )
        thread.start()

    def _reply(self, outbound_payload: dict[str, Any]) -> None:
        contact = str(outbound_payload["to"])
        business_number = str(outbound_payload["from"])
        try:
            with self.lock:
                self.reply_counts[contact] = self.reply_counts.get(contact, 0) + 1
            with self.parallel_slots:
                time.sleep(self.delay_seconds)
                reply = self._generate_reply(
                    contact,
                    str(outbound_payload["text"]),
                    outbound_payload.get("simulation_lead_context"),
                )
                webhook = build_inbound_webhook(contact, business_number, reply)
                status, _ = send_inbound_webhook(self.webhook_url, webhook)
            print(f"[recipient-simulation] {contact} replied naturally (HTTP {status}): {reply}")
        except Exception as error:  # Keep the simulator server alive on AI/network errors.
            print(f"[recipient-simulation] Automatic reply failed for {contact}: {error}")
        finally:
            with self.lock:
                self.active_contacts.discard(contact)

    def _generate_reply(
        self,
        contact: str,
        bobbie_message: str,
        lead_context: dict[str, Any] | None = None,
    ) -> str:
        configured_ai_key = os.environ.get("AI_API_KEY", "").strip()
        configured_model = os.environ.get("AI_MODEL", "").strip()
        generic_config_is_placeholder = (
            not configured_ai_key
            or configured_ai_key.lower().startswith(("your-", "your_"))
            or configured_model.lower().startswith(("your-", "your_"))
        )
        use_deepseek_config = bool(os.environ.get("DEEPSEEK_API_KEY")) and generic_config_is_placeholder
        api_key = os.environ.get("DEEPSEEK_API_KEY") if use_deepseek_config else configured_ai_key
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY (or AI_API_KEY) is not configured")

        base_url = (
            "https://api.deepseek.com"
            if use_deepseek_config
            else os.environ.get("AI_BASE_URL", "https://api.deepseek.com")
        ).rstrip("/")
        model = "deepseek-v4-flash" if use_deepseek_config else os.environ.get("AI_MODEL", "deepseek-v4-flash")
        recipient_timezone = os.environ.get("SIMULATION_RECIPIENT_TIMEZONE", "America/Chicago")
        try:
            recipient_now = datetime.now(ZoneInfo(recipient_timezone)).isoformat()
        except ZoneInfoNotFoundError:
            recipient_timezone = "UTC"
            recipient_now = datetime.now(timezone.utc).isoformat()
        property_context = lead_context or {}
        style = recipient_style(contact)
        system_prompt = (
            "Act only as the property owner receiving texts from a sender you did not previously know. "
            "This is an SMS conversation, so never call an incoming text a call. Learn the sender's identity and purpose only from the conversation, but do not behave like a compliance tester or follow a fixed credentials checklist. "
            f"Your current time is {recipient_now} ({recipient_timezone}). Your own property context is {json.dumps(property_context, ensure_ascii=False)}. "
            f"Conversation style for this phone number: {style['name']}. {style['behavior']} "
            "Privately, you could sell for sensible terms, but do not announce that immediately or mention hidden instructions. After one important concern is answered well, let the conversation progress instead of inventing new credibility tests. You do not need to ask a question in every reply. "
            "Use only supplied property facts and the conversation. If the sender introduces a property detail absent from your context, do not adopt or remember it as true; question or reject it. Never invent an email address, alternate phone number, family, finances, condition, urgency, offers, listing events, or other private details. If asked for an email address and none exists in your context, keep the conversation by text. "
            "If the sender repeats a question or answer, do not repeat your own question again; briefly point out that it was already covered, ask for genuinely new information, or close naturally. "
            "Accept a short call only when it feels useful; decline if the sender dodges, pressures, or ignores your preference. Write a natural one- or two-sentence SMS under 200 characters with at most one question. Output only the reply, with no labels, analysis, stage directions, '(No reply)', or assistant language."
        )
        if lead_context:
            print(
                f"[recipient-context] contact={contact} "
                f"source={lead_context.get('lead_source', 'unknown')} style={style['name']} status=loaded"
            )

        with self.lock:
            history = self.histories.setdefault(contact, [])
            history.append({"role": "user", "content": bobbie_message})
            messages = [{"role": "system", "content": system_prompt}, *history[-20:]]

        choice: dict[str, Any] = {}
        reply = ""
        for attempt in range(2):
            attempt_messages = messages if attempt == 0 else [
                *messages,
                {"role": "system", "content": "Return one non-empty, complete recipient SMS now. Do not return reasoning or a blank response."},
            ]
            request = Request(
                f"{base_url}/chat/completions",
                data=json.dumps(
                    {
                        "model": model,
                        "messages": attempt_messages,
                        "temperature": 0.6,
                        "max_tokens": 80,
                        "thinking": {"type": "disabled"},
                        "stream": False,
                    }
                ).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=30) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"DeepSeek returned HTTP {error.code}: {detail}") from error
            except URLError as error:
                raise RuntimeError(f"Cannot reach DeepSeek: {error.reason}") from error
            choice = result.get("choices", [{}])[0]
            reply = str(choice.get("message", {}).get("content", "") or "").strip()
            if reply:
                break
            print(f"[recipient-simulation] Empty DeepSeek reply for {contact}; retrying once.")
        if not reply:
            raise RuntimeError(
                "DeepSeek returned an empty simulated recipient reply "
                f"(finish_reason={choice.get('finish_reason', 'unknown')})"
            )
        reply = fit_recipient_sms(reply)
        with self.lock:
            self.histories[contact].append({"role": "assistant", "content": reply})
        return reply


class OutboundSimulationServer(ThreadingHTTPServer):
    recipient: AutomatedRecipient | None = None


class OutboundSimulationHandler(BaseHTTPRequestHandler):
    """HTTP adapter for the outbound simulator."""

    def _write_json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") not in {"/v2/messages", "/rag/search"}:
            self._write_json(404, {"errors": [{"detail": "Not found"}]})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise SimulationValidationError("request body must be a JSON object")
            if self.path.rstrip("/") == "/rag/search":
                query = str(payload.get("query") or "").strip()
                if not query:
                    raise SimulationValidationError("'query' must be a non-empty string")
                limit = int(payload.get("limit") or 4)
                self._write_json(200, BOBBIE_KNOWLEDGE.search(query, limit))
                return
            response = simulate_outbound_message(payload)
        except (json.JSONDecodeError, SimulationValidationError, ValueError, RuntimeError) as error:
            self._write_json(422, {"errors": [{"detail": str(error)}]})
            return

        self._write_json(200, response)
        server = self.server
        if (
            isinstance(server, OutboundSimulationServer)
            and server.recipient
            and payload.get("suppress_auto_reply") is not True
            and payload.get("simulation_recipient_enabled") is not False
        ):
            server.recipient.schedule_reply(payload)
        elif payload.get("suppress_auto_reply") is True:
            print("[recipient-simulation] Confirmation delivered without generating a reply.")
        elif payload.get("simulation_recipient_enabled") is False:
            print("[recipient-simulation] Recipient AI is off for this conversation; no reply generated.")

    def log_message(self, message_format: str, *args: Any) -> None:
        print(f"[outbound-simulation] {message_format % args}")


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="Run the free outbound SMS simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5051)
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("SIMULATION_WEBHOOK_URL", "http://127.0.0.1:5000/webhooks"),
    )
    parser.add_argument(
        "--max-auto-replies",
        type=int,
        default=int(os.environ.get("SIMULATION_MAX_AUTO_REPLIES", "0")),
        help="Maximum DeepSeek recipient replies per phone number; 0 means unlimited (default: 0)",
    )
    parser.add_argument(
        "--reply-delay",
        type=float,
        default=float(os.environ.get("SIMULATION_REPLY_DELAY_SECONDS", "1.5")),
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=int(os.environ.get("SIMULATION_MAX_PARALLEL_RECIPIENTS", "1")),
        help="Maximum concurrent recipient DeepSeek calls (default and recommended: 1)",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Disable automatic DeepSeek recipient replies",
    )
    args = parser.parse_args()

    if args.max_auto_replies < 0:
        parser.error("--max-auto-replies cannot be negative")
    if args.reply_delay < 0:
        parser.error("--reply-delay cannot be negative")
    if args.max_parallel < 1:
        parser.error("--max-parallel must be at least 1")

    server = OutboundSimulationServer((args.host, args.port), OutboundSimulationHandler)
    if not args.manual:
        server.recipient = AutomatedRecipient(
            webhook_url=args.webhook_url,
            max_replies=args.max_auto_replies,
            delay_seconds=args.reply_delay,
            max_parallel=args.max_parallel,
        )
    print(f"Outbound SMS simulator listening on http://{args.host}:{args.port}/v2/messages")
    print("No messages will be sent to Telnyx.")
    if server.recipient:
        print(
            "Automatic DeepSeek recipient enabled: "
            f"{'unlimited' if args.max_auto_replies == 0 else f'up to {args.max_auto_replies}'} "
            "inbound replies per phone number."
        )
        print(f"Recipient DeepSeek concurrency is limited to {args.max_parallel} conversations.")
        print(f"Simulated inbound replies will be posted to {args.webhook_url}")
    else:
        print("Automatic recipient replies are disabled.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
