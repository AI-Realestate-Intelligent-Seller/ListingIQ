"""Copy a ListingIQ SQLite database into an empty PostgreSQL database.

    .venv/bin/python scripts/sqlite_to_postgres.py \
        --sqlite db_dumps/listingiq-YYYYMMDD-HHMMSS.db \
        --postgres postgresql://user:pass@127.0.0.1:5433/listingiq_dev

Steps:
  1. Build the schema in Postgres with the app's own Alembic migrations
     (`alembic upgrade head`), so it matches what production runs.
  2. Copy every table's rows, reading through SQLAlchemy reflection so SQLite
     values (0/1 booleans, text datetimes, JSON) arrive as proper Postgres types.
     Foreign-key triggers are paused during the copy so circular references
     (e.g. leads <-> conversations) load in any order.
  3. Move each id sequence past the copied rows, so new inserts don't collide.
  4. Compare row counts table by table and exit non-zero on any mismatch.

The target must be empty: the script refuses to load into a database that
already has rows, so it can never overwrite real data.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import MetaData, create_engine, func, inspect, select, text

PROJECT_DIR = Path(__file__).resolve().parents[1]
BATCH = 1000


def run_migrations(postgres_url: str) -> None:
    env = {**os.environ, 'POSTGRES_URL': postgres_url, 'CACHE_ENABLED': 'false'}
    subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
        cwd=PROJECT_DIR, env=env, check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--sqlite', required=True, help='Path to the SQLite .db file')
    parser.add_argument('--postgres', required=True, help='SQLAlchemy URL of an empty Postgres DB')
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite).resolve()
    if not sqlite_path.is_file():
        print(f'SQLite file not found: {sqlite_path}')
        return 2

    source = create_engine(f'sqlite:///file:{sqlite_path}?mode=ro&uri=true')
    target = create_engine(args.postgres)

    print('1/4 Creating schema with Alembic migrations …')
    run_migrations(args.postgres)

    source_meta = MetaData()
    source_meta.reflect(bind=source)
    target_meta = MetaData()
    target_meta.reflect(bind=target)

    data_tables = [table for table in target_meta.sorted_tables if table.name != 'alembic_version']
    with target.connect() as conn:
        occupied = [t.name for t in data_tables if conn.execute(select(func.count()).select_from(t)).scalar()]
    if occupied:
        print(f'Refusing to load: target already has rows in {", ".join(occupied[:5])}'
              f'{" …" if len(occupied) > 5 else ""}. Use an empty database.')
        return 2

    only_source = sorted(set(source_meta.tables) - set(target_meta.tables))
    only_target = sorted(set(target_meta.tables) - set(source_meta.tables))
    if only_source:
        print(f'   note: not in the migrated schema, skipped: {", ".join(only_source)}')
    if only_target:
        print(f'   note: new in the migrated schema (left empty): {", ".join(only_target)}')

    print('2/4 Copying rows …')
    counts: dict[str, int] = {}
    with source.connect() as src, target.begin() as dst:
        # Pause FK enforcement for this transaction only (superuser in the container).
        dst.execute(text('SET LOCAL session_replication_role = replica'))
        for table in data_tables:
            if table.name not in source_meta.tables:
                continue
            source_table = source_meta.tables[table.name]
            columns = [c.name for c in table.columns if c.name in source_table.columns]
            dropped = [c.name for c in source_table.columns if c.name not in table.columns]
            if dropped:
                print(f'   note: {table.name}: source-only columns skipped: {", ".join(dropped)}')
            result = src.execute(select(*[source_table.c[name] for name in columns]))
            copied = 0
            while rows := result.fetchmany(BATCH):
                dst.execute(table.insert(), [dict(zip(columns, row)) for row in rows])
                copied += len(rows)
            counts[table.name] = copied
            if copied:
                print(f'   {table.name}: {copied}')

        print('3/4 Resetting id sequences …')
        for table in data_tables:
            for column in table.primary_key.columns:
                sequence = dst.execute(
                    text('SELECT pg_get_serial_sequence(:t, :c)'),
                    {'t': f'"{table.name}"', 'c': column.name},
                ).scalar()
                if sequence:
                    dst.execute(text(
                        f'SELECT setval(:s, COALESCE((SELECT MAX("{column.name}") FROM "{table.name}"), 1), '
                        f'(SELECT MAX("{column.name}") FROM "{table.name}") IS NOT NULL)'
                    ), {'s': sequence})

    print('4/4 Verifying row counts …')
    mismatches = []
    with source.connect() as src, target.connect() as dst:
        for name, copied in counts.items():
            expected = src.execute(select(func.count()).select_from(source_meta.tables[name])).scalar()
            actual = dst.execute(select(func.count()).select_from(target_meta.tables[name])).scalar()
            if expected != actual:
                mismatches.append(f'{name}: sqlite={expected} postgres={actual}')
        version = dst.execute(text('SELECT version_num FROM alembic_version')).scalar()

    total = sum(counts.values())
    if mismatches:
        print('MISMATCH:\n  ' + '\n  '.join(mismatches))
        return 1
    print(f'OK: {total} rows in {len(counts)} tables match. Schema at {version}.')
    print(f'   tables inspected in target: {len(inspect(target).get_table_names())}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
