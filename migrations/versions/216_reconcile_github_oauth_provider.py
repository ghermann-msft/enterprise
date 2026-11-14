"""Reconcile the configured GitHub OAuth provider for existing databases.

Migration 168 seeded OAuth provider rows from environment variables only when
that migration originally ran. Deployments configured later therefore have no
GitHub provider row even though the runtime has valid GitHub App credentials.

This migration adds the missing current-schema row when both
``GITHUB_APP_CLIENT_ID`` and ``GITHUB_APP_CLIENT_SECRET`` are configured.
Any existing GitHub OAuth row is authoritative and is left untouched,
including its client credentials, enabled state, row id, branding, and
administrator customizations.

Revision ID: 216
Revises: 215
Create Date: 2026-11-14 00:00:00.000000
"""

import json
import os
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '216'
down_revision: str | None = '215'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OAUTH_KIND = 'OAuthProviderDetails'
_GITHUB_COLOR = '#24292F'
_GITHUB_SCOPES = ['repo', 'user:email', 'workflow']


def _dump_details(details) -> str:
    from pydantic import TypeAdapter

    from storage.auth_provider_details import AuthProviderDetails

    type_adapter: TypeAdapter = TypeAdapter(AuthProviderDetails)
    dumped = type_adapter.dump_python(
        details, mode='json', context={'for_storage': True}
    )
    return json.dumps(dumped)


def reconcile_github_oauth_provider(bind) -> bool:
    """Insert the configured GitHub provider if no GitHub row exists.

    Returns whether a row was inserted. Existing rows are never updated:
    environment changes must not silently rotate credentials or re-enable a
    provider that an administrator disabled.
    """
    client_id = os.getenv('GITHUB_APP_CLIENT_ID', '').strip()
    client_secret = os.getenv('GITHUB_APP_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        return False

    from openhands.app_server.integrations.service_types import ProviderType
    from storage.auth_provider_details import OAuthProviderDetails

    details = OAuthProviderDetails(
        provider_type=ProviderType.GITHUB,
        client_id=client_id,
        client_secret=client_secret,
        authorization_url='https://github.com/login/oauth/authorize',
        token_url='https://github.com/login/oauth/access_token',
        userinfo_url='https://api.github.com/user',
        scopes=_GITHUB_SCOPES,
    )
    result = bind.execute(
        sa.text(
            """
            INSERT INTO auth_providers
                (kind, title, is_idp, icon, color, enabled,
                 allow_registration, allow_match_by_email, details,
                 created_at, updated_at)
            SELECT
                :kind, 'GitHub', false, NULL, :color, true,
                true, true, CAST(:details AS json),
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (
                SELECT 1
                FROM auth_providers
                WHERE kind = :kind
                  AND details ->> 'provider_type' = 'github'
            )
            """
        ),
        {
            'kind': _OAUTH_KIND,
            'color': _GITHUB_COLOR,
            'details': _dump_details(details),
        },
    )
    return result.rowcount == 1


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')
    reconcile_github_oauth_provider(bind)


def downgrade() -> None:
    # A reconciled row is indistinguishable from an administrator-managed row
    # after migration. Removing it could destroy live OAuth links and tokens.
    pass
