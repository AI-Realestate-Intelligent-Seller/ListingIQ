from sqlalchemy.orm import Session
from .models import User, Conversation, Message
from .auth import hash_password
from datetime import datetime

class CRUD:
    @staticmethod
    def get_user_by_email(db: Session, email: str):
        return db.query(User).filter(User.email == email).first()

    @staticmethod
    def create_user(db: Session, email: str, password: str, full_name: str | None = None, role: str = 'user'):
        user = User(email=email, hashed_password=hash_password(password), full_name=full_name, role=role)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def get_or_create_conversation(db: Session, contact: str, user_id: int):
        conv = db.query(Conversation).filter(Conversation.contact == contact, Conversation.user_id == user_id).first()
        if conv:
            return conv
        conv = Conversation(contact=contact, user_id=user_id, created_at=datetime.utcnow())
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return conv

    @staticmethod
    def create_message(db: Session, conversation_id: int, direction: str, from_number: str | None, to_number: str | None, text: str, status: str, event_type: str):
        msg = Message(
            conversation_id=conversation_id,
            direction=direction,
            from_number=from_number,
            to_number=to_number,
            text=text,
            status=status,
            event_type=event_type,
            created_at=datetime.utcnow(),
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        return msg
