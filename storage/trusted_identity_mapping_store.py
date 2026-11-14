import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from server.constants import ORG_SETTINGS_VERSION
from storage.database import a_session_maker
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.trusted_identity_mapping import TrustedIdentityMapping
from storage.user import User

_RECONCILIATION_LOCK_ID = 764852690741038121


@dataclass(frozen=True)
class TrustedIdentityBootstrap:
    org_name: str
    user_email: str
    role: str = 'owner'

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> 'TrustedIdentityBootstrap':
        org_name = value.get('org_name')
        user_email = value.get('user_email')
        role = value.get('role', 'owner')
        if (
            not isinstance(org_name, str)
            or not org_name.strip()
            or not isinstance(user_email, str)
            or not user_email.strip()
            or role not in ('owner', 'admin', 'member')
        ):
            raise ValueError(
                'bootstrap requires non-empty org_name and user_email fields '
                'and an owner, admin, or member role'
            )
        return cls(
            org_name=org_name.strip(),
            user_email=user_email.strip(),
            role=role,
        )


@dataclass
class TrustedIdentityMappingStore:
    async def get(
        self, tenant_id: UUID, object_id: UUID
    ) -> TrustedIdentityMapping | None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(TrustedIdentityMapping).where(
                    TrustedIdentityMapping.tenant_id == tenant_id,
                    TrustedIdentityMapping.object_id == object_id,
                )
            )
            return result.scalars().one_or_none()

    async def reconcile(
        self,
        *,
        tenant_id: UUID,
        object_id: UUID,
        org_id: UUID,
        user_id: UUID,
        enabled: bool = True,
        bootstrap: TrustedIdentityBootstrap | None = None,
    ) -> TrustedIdentityMapping:
        async with a_session_maker() as session:
            await session.execute(
                text('SELECT pg_advisory_xact_lock(:lock_id)'),
                {'lock_id': _RECONCILIATION_LOCK_ID},
            )
            mapping = await session.get(
                TrustedIdentityMapping,
                {'tenant_id': tenant_id, 'object_id': object_id},
            )
            if mapping is not None:
                if mapping.org_id != org_id or mapping.user_id != user_id:
                    raise ValueError(
                        'Trusted identity mapping conflicts with the persisted '
                        'Enterprise principal'
                    )

            org = await session.get(Org, org_id)
            user = await session.get(User, user_id)
            membership = await session.get(OrgMember, (org_id, user_id))

            if bootstrap is None and (
                org is None or user is None or membership is None
            ):
                raise ValueError(
                    'Trusted identity mapping references an unprovisioned '
                    'Enterprise principal; add an explicit bootstrap object'
                )

            if bootstrap is not None:
                if org is None:
                    org = Org(
                        id=org_id,
                        name=bootstrap.org_name,
                        org_version=ORG_SETTINGS_VERSION,
                        v1_enabled=True,
                    )
                    session.add(org)
                    await session.flush()
                elif org.name != bootstrap.org_name:
                    raise ValueError(
                        'Trusted identity bootstrap org name conflicts with the '
                        'persisted organization'
                    )

                if user is None:
                    user = User(
                        id=user_id,
                        current_org_id=org_id,
                        email=bootstrap.user_email,
                        email_verified=True,
                        accepted_tos=datetime.utcnow(),
                        user_consents_to_analytics=True,
                        onboarding_completed=True,
                    )
                    session.add(user)
                    await session.flush()
                elif (
                    user.email
                    and user.email.casefold() != bootstrap.user_email.casefold()
                ):
                    raise ValueError(
                        'Trusted identity bootstrap email conflicts with the '
                        'persisted user'
                    )
                elif not user.email:
                    user.email = bootstrap.user_email
                    user.email_verified = True

                if membership is None:
                    role = (
                        await session.execute(
                            select(Role).where(Role.name == bootstrap.role)
                        )
                    ).scalar_one_or_none()
                    if role is None:
                        raise ValueError(
                            f'Trusted identity bootstrap role {bootstrap.role!r} '
                            'does not exist'
                        )
                    membership = OrgMember(
                        org_id=org_id,
                        user_id=user_id,
                        role_id=role.id,
                        llm_api_key='',
                        status='active',
                    )
                    session.add(membership)

            if mapping is None:
                mapping = TrustedIdentityMapping(
                    tenant_id=tenant_id,
                    object_id=object_id,
                    org_id=org_id,
                    user_id=user_id,
                    enabled=enabled,
                )
                session.add(mapping)
            else:
                mapping.enabled = enabled
            await session.commit()
            await session.refresh(mapping)
            return mapping


async def reconcile_trusted_identity_mappings_from_env() -> int:
    """Persist the deployment-approved Entra to Enterprise identity mappings."""
    raw = os.getenv('OH_TRUSTED_IDENTITY_MAPPINGS')
    if not raw:
        return 0
    try:
        records = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError('OH_TRUSTED_IDENTITY_MAPPINGS must be valid JSON') from exc
    if not isinstance(records, list):
        raise ValueError('OH_TRUSTED_IDENTITY_MAPPINGS must be a JSON array')

    store = TrustedIdentityMappingStore()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(
                'OH_TRUSTED_IDENTITY_MAPPINGS entries must be JSON objects'
            )
        try:
            enabled = record.get('enabled', True)
            if not isinstance(enabled, bool):
                raise TypeError('enabled must be a boolean')
            bootstrap_value = record.get('bootstrap')
            if bootstrap_value is not None and not isinstance(bootstrap_value, dict):
                raise TypeError('bootstrap must be an object')
            await store.reconcile(
                tenant_id=UUID(record['tenant_id']),
                object_id=UUID(record['object_id']),
                org_id=UUID(record['org_id']),
                user_id=UUID(record['user_id']),
                enabled=enabled,
                bootstrap=(
                    TrustedIdentityBootstrap.from_dict(bootstrap_value)
                    if bootstrap_value is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                'OH_TRUSTED_IDENTITY_MAPPINGS entries require UUID tenant_id, '
                'object_id, org_id, and user_id fields'
            ) from exc
    return len(records)
