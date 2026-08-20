"""Add SIP registration fields to voximplant models

Revision ID: add_sip_registration_fields
Revises: add_elevenlabs_models
Create Date: 2026-03-06 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_sip_registration_fields'
down_revision = 'add_elevenlabs_models'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Добавление полей для SIP транков:
    - vox_sip_registrations в voximplant_child_accounts
    - phone_source, sip_provider, sip_registration_id в voximplant_phone_numbers
    """
    connection = op.get_bind()

    # 1. voximplant_child_accounts.vox_sip_registrations
    try:
        result = connection.execute(sa.text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'voximplant_child_accounts'
            AND column_name = 'vox_sip_registrations'
        """))
        if result.fetchone() is None:
            op.add_column('voximplant_child_accounts',
                sa.Column('vox_sip_registrations', sa.JSON(), nullable=True, server_default='{}')
            )
    except Exception:
        pass

    # 2. voximplant_phone_numbers.phone_source
    try:
        result = connection.execute(sa.text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'voximplant_phone_numbers'
            AND column_name = 'phone_source'
        """))
        if result.fetchone() is None:
            op.add_column('voximplant_phone_numbers',
                sa.Column('phone_source', sa.String(20), nullable=False, server_default='voximplant')
            )
    except Exception:
        pass

    # 3. voximplant_phone_numbers.sip_provider
    try:
        result = connection.execute(sa.text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'voximplant_phone_numbers'
            AND column_name = 'sip_provider'
        """))
        if result.fetchone() is None:
            op.add_column('voximplant_phone_numbers',
                sa.Column('sip_provider', sa.String(50), nullable=True)
            )
    except Exception:
        pass

    # 4. voximplant_phone_numbers.sip_registration_id
    try:
        result = connection.execute(sa.text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'voximplant_phone_numbers'
            AND column_name = 'sip_registration_id'
        """))
        if result.fetchone() is None:
            op.add_column('voximplant_phone_numbers',
                sa.Column('sip_registration_id', sa.String(50), nullable=True)
            )
    except Exception:
        pass


def downgrade() -> None:
    """Удаление полей SIP транков."""
    op.drop_column('voximplant_phone_numbers', 'sip_registration_id')
    op.drop_column('voximplant_phone_numbers', 'sip_provider')
    op.drop_column('voximplant_phone_numbers', 'phone_source')
    op.drop_column('voximplant_child_accounts', 'vox_sip_registrations')
