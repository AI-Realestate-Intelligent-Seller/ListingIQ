from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models import Conversation, Message
from ..routes.auth import get_current_user
from ..tenancy import brokerage_user_ids

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get('/')
def list_conversations(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    conversations = db.query(Conversation).filter(Conversation.user_id.in_(brokerage_user_ids(db, current_user))).all()
    response = []
    for conv in conversations:
        latest_message = (
            db.query(Message)
            .filter(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .first()
        )
        response.append(
            {
                'id': conv.id,
                'external_phone': conv.contact,
                'name': conv.name,
                'property_address': conv.property_address,
                'created_at': conv.created_at.isoformat(),
                'latest_message': latest_message.text if latest_message else None,
            }
        )
    return response


@router.get('/{conversation_id}/messages')
def get_conversation_messages(conversation_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.user_id.in_(brokerage_user_ids(db, current_user))).first()
    if not conv:
        raise HTTPException(status_code=404, detail='Conversation not found')
    messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at).all()
    return [
        {
            'id': msg.id,
            'direction': msg.direction,
            'from_number': msg.from_number,
            'to_number': msg.to_number,
            'text': msg.text,
            'status': msg.status,
            'event_type': msg.event_type,
            'created_at': msg.created_at.isoformat(),
        }
        for msg in messages
    ]
