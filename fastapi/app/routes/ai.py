from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..routes.auth import get_current_user
from ..ai_runtime import ai_runtime
from ..models import Conversation, Message
from ..tenancy import brokerage_user_ids

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post('/reply')
def generate_ai_reply(conversation_id: int, next_step: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.user_id.in_(brokerage_user_ids(db, current_user))).first()
    if not conv:
        raise HTTPException(status_code=404, detail='Conversation not found')
    messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at).all()
    history = [f"{msg.direction}: {msg.text}" for msg in messages]
    reply = ai_runtime.generate_reply(history, next_step, user_id=current_user.id)
    return {"reply": reply}
