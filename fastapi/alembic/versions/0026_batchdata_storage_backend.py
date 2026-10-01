"""Compatibility marker for a revision already stamped in Neon.

This revision deliberately performs no schema operations. The application now stores
R2 object keys in the existing batchdata_saved_files.relative_path column, so no new
database columns are required. Keep this marker only until the already-applied Neon
revision is reconciled during an explicitly approved database maintenance window.
"""

revision = "0026_batchdata_storage_backend"
down_revision = "b2f9919ecc68"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
