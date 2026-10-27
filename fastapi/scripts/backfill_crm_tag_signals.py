"""Add Expired/FSBO/Cancelled signals from Lofty CRM tags to already-imported leads.

Usage: python -m scripts.backfill_crm_tag_signals          (dry run, prints counts)
       python -m scripts.backfill_crm_tag_signals --apply  (writes the changes)
"""
import json
import sys

from app.db import SessionLocal
from app.leads.service import backfill_crm_tag_signals


def main():
    apply = '--apply' in sys.argv[1:]
    session = SessionLocal()
    try:
        result = backfill_crm_tag_signals(session, apply=apply)
    finally:
        session.close()
    print(json.dumps({'applied': apply, **result}, indent=2))


if __name__ == '__main__':
    main()
