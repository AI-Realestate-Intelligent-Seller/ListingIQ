from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import re
from ..routes.auth import get_current_user
from ..db import SessionLocal
from ..crud import CRUD
from ..adapters.telnyx import send_message
from sqlalchemy.orm import Session
from ..models import Conversation

router = APIRouter()

PHONE_RE = re.compile(r'^\+[1-9]\d{6,14}$')

class ComposeIn(BaseModel):
    conversation_id: int | None = None
    to: str | None = None
    text: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post('/')
def compose_message(payload: ComposeIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not payload.text or len(payload.text) > 1000:
        raise HTTPException(status_code=400, detail='Invalid message text')

    conv = None
    if payload.conversation_id:
        conv = db.query(Conversation).filter(Conversation.id == payload.conversation_id, Conversation.user_id == current_user.id).first()
        if not conv:
            raise HTTPException(status_code=404, detail='Conversation not found')
        if payload.to and payload.to != conv.contact:
            raise HTTPException(status_code=400, detail='Conversation phone mismatch')
    else:
        if not payload.to or not PHONE_RE.match(payload.to):
            raise HTTPException(status_code=400, detail='Invalid E.164 phone number')
        conv = CRUD.get_or_create_conversation(db, payload.to, current_user.id)

    msg = CRUD.create_message(
        db,
        conversation_id=conv.id,
        direction='outbound',
        from_number=None,
        to_number=conv.contact,
        text=payload.text,
        status='queued',
        event_type='message.sent',
    )

    try:
        resp = send_message(conv.contact, payload.text)
        telnyx_id = None
        if isinstance(resp, dict):
            telnyx_id = resp.get('data', {}).get('id') or resp.get('id')
        msg.status = 'sent' if telnyx_id else 'queued'
        db.add(msg)
        db.commit()
        return {"ok": True, "message_id": msg.id, "conversation_id": conv.id, "telnyx": resp}
    except Exception as e:
        msg.status = 'failed'
        db.add(msg)
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))

