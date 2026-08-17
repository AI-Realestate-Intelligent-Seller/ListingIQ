"""Create a team member directly, bypassing the invitation email.

Useful while SMTP is not configured yet. The user is attached to an existing
Head of Brokerage's brokerage, exactly as accepting an invitation would do.

    python scripts/seed_team_member.py \
        --email ather.shamim@linchpinglobal.net \
        --first-name Ather --last-name Shamim \
        --role broker --password 'Password123!'
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.models import User  # noqa: E402

ROLES = ('broker', 'agent')


def main() -> int:
    parser = argparse.ArgumentParser(description='Seed a broker/agent into a brokerage.')
    parser.add_argument('--email', required=True)
    parser.add_argument('--first-name', required=True)
    parser.add_argument('--last-name', required=True)
    parser.add_argument('--role', choices=ROLES, default='broker')
    parser.add_argument('--password', required=True)
    parser.add_argument(
        '--hob-email',
        help='HOB whose brokerage to join. Defaults to the only HOB in the database.',
    )
    args = parser.parse_args()

    email = args.email.strip().lower()
    session = db.SessionLocal()
    try:
        hob_query = session.query(User).filter(User.role == 'hob')
        if args.hob_email:
            hob_query = hob_query.filter(User.email == args.hob_email.strip().lower())
        hobs = hob_query.all()
        if not hobs:
            print('No Head of Brokerage found. Register one first.')
            return 1
        if len(hobs) > 1:
            print('Multiple HOBs found — pass --hob-email to choose one:')
            for hob in hobs:
                print(f'  {hob.email} ({hob.brokerage_name})')
            return 1
        hob = hobs[0]

        if session.query(User).filter(User.email == email).first():
            print(f'{email} already exists — nothing to do.')
            return 0

        user = User(
            email=email,
            hashed_password=hash_password(args.password),
            first_name=args.first_name.strip(),
            last_name=args.last_name.strip(),
            full_name=f'{args.first_name.strip()} {args.last_name.strip()}',
            brokerage_id=hob.brokerage_id,
            brokerage_name=hob.brokerage_name,
            role=args.role,
            is_head_or_owner=False,
            is_verified=True,
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        print(
            f'Created {user.email} (id={user.id}) as {user.role} '
            f'in {user.brokerage_name} [{user.brokerage_id}]'
        )
        return 0
    finally:
        session.close()


if __name__ == '__main__':
    raise SystemExit(main())
