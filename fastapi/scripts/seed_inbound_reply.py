"""Store an inbound reply on an existing conversation, straight into the database.

A conversation only reaches the Follow-ups tab once the owner has actually
replied, so this is the quickest way to put a thread there without a real SMS
coming back through Telnyx. Refresh the dashboard and the thread appears under
Follow-ups; it stays in the SMS tab as well, because that tab is the whole inbox.

The row written is identical to the one the Telnyx webhook writes
(`direction='inbound'`, `status='received'`, `event_type='message.received'`),
and the lead classifier runs exactly as it does there.

What it does NOT do by default is wake Bobbie. The webhook is what queues her
(`process_ai_reply`); writing to the table bypasses that, so running this costs
nothing and sends nothing. Pass --ai-reply to run her pipeline as well — that
calls DeepSeek and sends a real outbound message.

    # See what you can reply to
    python scripts/seed_inbound_reply.py --list

    # Reply on a specific thread
    python scripts/seed_inbound_reply.py --conversation-id 3

    # Reply on the newest thread that has not been answered yet
    python scripts/seed_inbound_reply.py

    # Your own wording, by phone number, backdated three days
    python scripts/seed_inbound_reply.py --contact +13125848528 \
        --text "What's my place worth?" --minutes-ago 4320

    # ...and let Bobbie actually answer it, as the webhook would
    python scripts/seed_inbound_reply.py --conversation-id 3 --ai-reply

Run it from `project/fastapi` with the same environment the API uses (it reads
POSTGRES_URL through app.core.config), e.g.

    .venv311/bin/python scripts/seed_inbound_reply.py --list
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.logger import setup_logging  # noqa: E402
from app.models import Conversation, Message, User  # noqa: E402
from app.routes.followups import _serialize_one  # noqa: E402
from app.sms import service  # noqa: E402

DEFAULT_TEXT = 'Maybe — depends what my place is worth honestly'


def reply_count(session, conversation_id: int) -> int:
    return (session.query(Message)
            .filter(Message.conversation_id == conversation_id, Message.direction == 'inbound')
            .count())


def describe(session, conversation: Conversation) -> str:
    owner = session.query(User).filter(User.id == conversation.user_id).first()
    replies = reply_count(session, conversation.id)
    return (f'  id={conversation.id:<4} {conversation.contact:<15} '
            f'{(conversation.name or "—")[:22]:<22} '
            f'replies={replies:<3} status={conversation.lead_status:<15} '
            f'handled_by={conversation.handled_by:<7} '
            f'owner={owner.email if owner else "?"}')


def list_conversations(session) -> int:
    conversations = (session.query(Conversation)
                     .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                     .all())
    if not conversations:
        print('No conversations in the database. Start one from the SMS tab first.')
        return 1

    print(f'{len(conversations)} conversation(s), newest first:\n')
    for conversation in conversations:
        marker = ' ← already in Follow-ups' if reply_count(session, conversation.id) else ''
        print(describe(session, conversation) + marker)
    print('\nPass --conversation-id or --contact to choose one.')
    return 0


def pick_conversation(session, conversation_id: int | None, contact: str | None) -> Conversation | None:
    """The thread to reply on: the one asked for, or the newest unanswered one."""
    if conversation_id is not None:
        conversation = session.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conversation:
            print(f'No conversation with id {conversation_id}. Run --list to see what exists.')
        return conversation

    if contact:
        # Same rule the webhook uses: the most recent thread for that number.
        conversation = (session.query(Conversation)
                        .filter(Conversation.contact == contact.strip())
                        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                        .first())
        if not conversation:
            print(f'No conversation for {contact}. Run --list to see what exists.')
        return conversation

    for conversation in (session.query(Conversation)
                         .order_by(Conversation.created_at.desc(), Conversation.id.desc())
                         .all()):
        if not reply_count(session, conversation.id):
            return conversation
    print('Every conversation already has a reply. Pass --conversation-id to add another.')
    return None


def run_bobbie(session, conversation: Conversation, text: str) -> None:
    """Answer the reply the way the webhook's background task would.

    `process_ai_reply` never raises — it logs and returns when the model or the
    carrier is unavailable — so success is judged by whether a new outbound
    message actually landed on the thread.
    """
    if not conversation.ai_enabled or conversation.handled_by != 'bobbie':
        print(f'\nBobbie is not on this thread (ai_enabled={bool(conversation.ai_enabled)}, '
              f"handled_by={conversation.handled_by!r}), so she will not reply. "
              'Hand it back to her from the UI first.')
        return
    if not settings['ai'].get('api_key'):
        print('\nDEEPSEEK_API_KEY is not set, so no reply can be generated. '
              'Set it in the environment the script runs in and try again.')
        return

    before = session.query(Message).filter(Message.conversation_id == conversation.id).count()
    print(f"\nRunning Bobbie's pipeline (model={settings['ai']['model']}, "
          f"sms_mode={settings['telnyx'].get('mode')})…")
    # Its own session, exactly as the background task gets one.
    service.process_ai_reply(conversation.id, text)

    session.expire_all()
    added = (session.query(Message)
             .filter(Message.conversation_id == conversation.id)
             .order_by(Message.created_at, Message.id)
             .all())[before:]
    if not added:
        print('She sent nothing. That is the designed behaviour when the model or the '
              'carrier is unavailable — the reason is on the API log at ERROR level.')
        return
    for message in added:
        print(f'  → [{message.event_type}] {message.text}')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Store an inbound reply on a conversation so it appears under Follow-ups.')
    parser.add_argument('--list', action='store_true',
                        help='Show every conversation and which ones already have a reply.')
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--conversation-id', type=int, help='Thread to reply on.')
    target.add_argument('--contact', help='Owner phone in E.164, e.g. +13125848528.')
    parser.add_argument('--text', default=DEFAULT_TEXT, help='The reply text.')
    parser.add_argument('--minutes-ago', type=int, default=0,
                        help="Backdate the reply. Two days or more makes it read as 'No response'.")
    parser.add_argument('--no-classify', action='store_true',
                        help='Skip the lead classifier, leaving lead_status untouched.')
    parser.add_argument('--ai-reply', action='store_true',
                        help='Also run Bobbie, as the webhook does. Calls DeepSeek and sends an SMS.')
    args = parser.parse_args()

    if args.ai_reply:
        # Bobbie's pipeline logs its decisions; without this the script is silent.
        setup_logging()

    session = db.SessionLocal()
    try:
        if args.list:
            return list_conversations(session)

        conversation = pick_conversation(session, args.conversation_id, args.contact)
        if not conversation:
            return 1

        text = args.text.strip()
        if not text:
            print('The reply text cannot be empty.')
            return 1

        message = Message(
            conversation_id=conversation.id,
            direction='inbound',
            from_number=conversation.contact,
            to_number=service.sending_number(),
            text=text,
            status='received',
            event_type='message.received',
            telnyx_id=None,
            created_at=datetime.utcnow() - timedelta(minutes=max(args.minutes_ago, 0)),
        )
        session.add(message)
        session.commit()
        session.refresh(message)

        # The classifier is deterministic and offline, so it runs by default and
        # the lead status matches what a real reply would have produced. The AI
        # pipeline is never started: nothing is generated and nothing is sent.
        if not args.no_classify:
            service.record_inbound_classification(session, conversation, text)

        owner = session.query(User).filter(User.id == conversation.user_id).first()
        print(f'Stored inbound message id={message.id} on conversation {conversation.id} '
              f'({conversation.contact}) at {message.created_at:%Y-%m-%d %H:%M} UTC')

        if args.ai_reply:
            run_bobbie(session, conversation, text)
        else:
            print('Bobbie was not run — the webhook is what queues her. '
                  'Add --ai-reply to have her answer this reply.')

        if owner:
            row = _serialize_one(session, conversation, owner)
            print('\nFollow-ups will show it as:')
            print(f'  {row["name"] or row["contact"]} — {row["reason_label"]}')
            print(f'  status={row["lead_status"]}  replies={row["reply_count"]}  '
                  f'decision={row["followup_state"]}  handled_by={row["handled_by"]}')
            print(f'\nSign in as {owner.email} and refresh the dashboard; '
                  'the Follow-ups tab polls every 8 seconds.')
        return 0
    finally:
        session.close()


if __name__ == '__main__':
    raise SystemExit(main())
