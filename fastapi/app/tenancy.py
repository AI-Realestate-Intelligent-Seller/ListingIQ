"""Who can see whose work — the brokerage is the tenant, not the account.

A brokerage is the set of users sharing a `brokerage_id` (see routes.team). Its
lead pool, its campaigns and the conversations those campaigns opened belong to
the brokerage as a whole: two agents on the same team work one pool, so the same
owner is never texted twice by the same office.

Every row still records the account that created it in `user_id`, which is the
provenance — who imported the lead, who sent the campaign. Visibility is the
brokerage; authorship is the account. Reading rows always goes through
`brokerage_user_ids` so the boundary is defined in exactly one place, and a
query that forgets it is a visible omission rather than a silent leak.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session

from .models import User


# Read-mostly lookups memoized on the session for the life of one transaction.
# The database is remote, so each repeated query in a request costs a network
# round trip; any commit or rollback drops the memo so writes are always seen.
SESSION_CACHE_KEY = 'request_cache'


def session_cache(session: Session) -> dict:
    return session.info.setdefault(SESSION_CACHE_KEY, {})


@event.listens_for(Session, 'after_commit')
@event.listens_for(Session, 'after_rollback')
def _drop_session_cache(session: Session) -> None:
    session.info.pop(SESSION_CACHE_KEY, None)


def brokerage_user_ids(session: Session, user: User) -> list[int]:
    """Every account whose work this user may see.

    An account with no brokerage_id — a legacy row, or one created outside the
    registration flow — is a tenant of one. Falling back to the user's own id
    keeps such an account isolated rather than accidentally grouping every
    brokerage-less account in the database into one shared pool.
    """
    if not user.brokerage_id:
        return [user.id]
    cache = session_cache(session)
    key = ('brokerage_user_ids', user.id, user.brokerage_id)
    if key not in cache:
        rows = (session.query(User.id)
                .filter(User.brokerage_id == user.brokerage_id)
                .all())
        cache[key] = [row[0] for row in rows]
    ids = list(cache[key])
    # The caller's own id is always in scope, even mid-transaction.
    if user.id not in ids:
        ids.append(user.id)
    return ids
