"""Create or update the first internal ListingIQ platform administrator."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import hash_password  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import User  # noqa: E402


def main():
    email = os.getenv('PLATFORM_ADMIN_EMAIL', '').strip().lower()
    password = os.getenv('PLATFORM_ADMIN_PASSWORD', '')
    name = os.getenv('PLATFORM_ADMIN_NAME', 'ListingIQ Platform Admin').strip()
    if not email or '@' not in email or len(password) < 12:
        raise SystemExit('Set PLATFORM_ADMIN_EMAIL and PLATFORM_ADMIN_PASSWORD (minimum 12 characters).')
    init_db()
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.email == email).first()
        if not user:
            user = User(email=email)
            session.add(user)
        user.hashed_password = hash_password(password)
        user.full_name = name
        user.first_name = name.split()[0]
        user.last_name = ' '.join(name.split()[1:])
        user.role = 'platform_admin'
        user.brokerage_id = None
        user.brokerage_name = None
        user.is_head_or_owner = False
        user.is_verified = True
        user.is_active = True
        session.commit()
        print(f'Platform administrator ready: {email}')
    finally:
        session.close()


if __name__ == '__main__':
    main()
