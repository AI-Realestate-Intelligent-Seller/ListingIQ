"""add notification and reminder tables

Revision ID: ca392df3bef1
Revises: 0025_booking_location
Create Date: 2026-09-28 16:22:15.531670
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ca392df3bef1"
down_revision: Union[str, None] = "0025_booking_location"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Push subscriptions
    # -------------------------------------------------------------------------
    op.create_table(
        "push_subscriptions",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "endpoint",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "p256dh",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "auth",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.UniqueConstraint(
            "endpoint",
            name="uq_push_subscriptions_endpoint",
        ),
    )

    op.create_index(
        "ix_push_subscriptions_user_id",
        "push_subscriptions",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "ix_push_subscriptions_device_id",
        "push_subscriptions",
        ["device_id"],
        unique=False,
    )

    # -------------------------------------------------------------------------
    # Booking reminders
    # -------------------------------------------------------------------------
    op.create_table(
        "booking_reminders",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "booking_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "reminder_type",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "scheduled_for",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "sent_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_booking_reminders_booking_id",
        "booking_reminders",
        ["booking_id"],
        unique=False,
    )

    op.create_index(
        "ix_booking_reminders_user_id",
        "booking_reminders",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "ix_booking_reminders_status_scheduled_for",
        "booking_reminders",
        ["status", "scheduled_for"],
        unique=False,
    )

    # -------------------------------------------------------------------------
    # Notifications
    # -------------------------------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "booking_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "reminder_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "type",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "title",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "message",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "is_read",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "read_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.Column(
            "action_url",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reminder_id"],
            ["booking_reminders.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "reminder_id",
            name="uq_notifications_reminder_id",
        ),
    )

    op.create_index(
        "ix_notifications_user_id",
        "notifications",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "ix_notifications_booking_id",
        "notifications",
        ["booking_id"],
        unique=False,
    )

    op.create_index(
        "ix_notifications_is_read",
        "notifications",
        ["is_read"],
        unique=False,
    )

    op.create_index(
        "ix_notifications_created_at",
        "notifications",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    # Notifications
    op.drop_index(
        "ix_notifications_created_at",
        table_name="notifications",
    )

    op.drop_index(
        "ix_notifications_is_read",
        table_name="notifications",
    )

    op.drop_index(
        "ix_notifications_booking_id",
        table_name="notifications",
    )

    op.drop_index(
        "ix_notifications_user_id",
        table_name="notifications",
    )

    op.drop_table("notifications")

    # Booking reminders
    op.drop_index(
        "ix_booking_reminders_status_scheduled_for",
        table_name="booking_reminders",
    )

    op.drop_index(
        "ix_booking_reminders_user_id",
        table_name="booking_reminders",
    )

    op.drop_index(
        "ix_booking_reminders_booking_id",
        table_name="booking_reminders",
    )

    op.drop_table("booking_reminders")

    # Push subscriptions
    op.drop_index(
        "ix_push_subscriptions_device_id",
        table_name="push_subscriptions",
    )

    op.drop_index(
        "ix_push_subscriptions_user_id",
        table_name="push_subscriptions",
    )

    op.drop_table("push_subscriptions")