from alembic.config import Config
from alembic import command
from pathlib import Path


def init_db():
    project_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(project_dir / 'alembic.ini'))
    command.upgrade(alembic_cfg, 'head')


if __name__ == '__main__':
    init_db()
    print('Database initialized via Alembic migration')
