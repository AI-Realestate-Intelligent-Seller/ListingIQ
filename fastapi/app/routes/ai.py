from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..routes.auth import get_current_user
from ..ai_runtime import ai_runtime
from ..models import Conversation, Message
from ..sms.runtime_context import AgentResolutionError, resolve_agent_runtime_context

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class AiReplyOut(BaseModel):
    """Public response: never the prompt, Persona contents or retrieved knowledge."""
    reply: str


@router.post('/reply', response_model=AiReplyOut)
def generate_ai_reply(conversation_id: int, next_step: str = Query(..., min_length=1, max_length=200),
                      current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Owner only. A Persona and its knowledge belong to one user, so brokerage
    # colleagues and the Head of Brokerage get the same 404 as a missing thread.
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id, Conversation.user_id == current_user.id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail='Conversation not found')
    try:
        context = resolve_agent_runtime_context(db, conv)
    except AgentResolutionError as error:
        raise HTTPException(status_code=409, detail='Personal AI is not available for this conversation') from error
    if context.user_id != current_user.id or context.brokerage_id != current_user.brokerage_id:
        raise HTTPException(status_code=404, detail='Conversation not found')
    messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at).all()
    history = [f"{msg.direction}: {msg.text}" for msg in messages]
    return {"reply": ai_runtime.generate_reply(history, next_step, context)}
