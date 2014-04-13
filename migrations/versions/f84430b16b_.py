"""Adds locking capabilities to payout table, creates new BonusPayout table, and DonationPercent table.

Revision ID: f84430b16b
Revises: 53d03041be56
Create Date: 2014-03-21 16:32:08.622226

"""

# revision identifiers, used by Alembic.
revision = 'f84430b16b'
down_revision = '53d03041be56'

from alembic import op
import sqlalchemy as sa


def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.create_table('donation_percent',
    sa.Column('user', sa.String(), nullable=False),
    sa.Column('perc', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('user')
    )
    op.create_table('bonus_payout',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user', sa.String(), nullable=True),
    sa.Column('amount', sa.BigInteger(), nullable=True),
    sa.Column('locked', sa.Boolean(), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('transaction_id', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['transaction_id'], ['transaction.txid'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.add_column(u'payout', sa.Column('locked', sa.Boolean(), nullable=True, server_default="FALSE"))
    op.add_column(u'payout', sa.Column('perc', sa.Float(), nullable=True, server_default="0"))
    op.add_column(u'payout', sa.Column('perc_applied', sa.BigInteger(), nullable=True, server_default="0"))
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_column(u'payout', 'perc_applied')
    op.drop_column(u'payout', 'perc')
    op.drop_column(u'payout', 'locked')
    op.drop_table('bonus_payout')
    op.drop_table('donation_percent')
    ### end Alembic commands ###
