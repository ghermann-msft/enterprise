import time
from unittest.mock import patch
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import Request

from openhands.app_server.user_auth.user_auth import AuthType
from server.auth.saas_user_auth import saas_user_auth_from_trusted_identity
from server.auth.trusted_identity import (
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    TrustedIdentityError,
)
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.trusted_identity_mapping import TrustedIdentityMapping
from storage.user import User

SECRET = 'a-trusted-identity-secret-at-least-32-bytes'


def _request(token: str | None = None) -> Request:
    headers = []
    if token is not None:
        headers.append((b'x-openhands-identity-assertion', token.encode()))
    return Request({'type': 'http', 'headers': headers})


def _token(*, object_id: UUID, tenant_id: UUID) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            'iss': DEFAULT_ISSUER,
            'aud': DEFAULT_AUDIENCE,
            'sub': str(object_id),
            'oid': str(object_id),
            'tid': str(tenant_id),
            'iat': now,
            'exp': now + 60,
        },
        SECRET,
        algorithm='HS256',
    )


async def _seed_identity(
    async_session_maker,
    *,
    object_id: UUID,
    tenant_id: UUID,
    user_disabled: bool = False,
    member_status: str = 'active',
    org_status: str = 'active',
    mapping_enabled: bool = True,
):
    user_id = uuid4()
    org_id = uuid4()
    async with async_session_maker() as session:
        role = Role(name=f'member-{uuid4()}', rank=3)
        org = Org(
            id=org_id,
            name=f'Org {org_id}',
            org_version=1,
            status=org_status,
        )
        session.add_all([role, org])
        await session.flush()
        session.add(
            User(
                id=user_id,
                current_org_id=org_id,
                user_consents_to_analytics=True,
                is_disabled=user_disabled,
            )
        )
        await session.flush()
        session.add(
            OrgMember(
                org_id=org_id,
                user_id=user_id,
                role_id=role.id,
                status=member_status,
                llm_api_key='test-api-key',
            )
        )
        session.add(
            TrustedIdentityMapping(
                tenant_id=tenant_id,
                object_id=object_id,
                org_id=org_id,
                user_id=user_id,
                enabled=mapping_enabled,
            )
        )
        await session.commit()
    return user_id, org_id


@pytest.mark.asyncio
async def test_trusted_identity_disabled_preserves_existing_auth(monkeypatch):
    monkeypatch.delenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET', raising=False)
    assert await saas_user_auth_from_trusted_identity(_request()) is None


@pytest.mark.asyncio
async def test_trusted_identity_builds_tenant_scoped_enterprise_auth(
    async_session_maker, monkeypatch
):
    object_id = uuid4()
    tenant_id = uuid4()
    user_id, org_id = await _seed_identity(
        async_session_maker, object_id=object_id, tenant_id=tenant_id
    )
    monkeypatch.setenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET', SECRET)

    with (
        patch('storage.user_store.a_session_maker', async_session_maker),
        patch('server.auth.saas_user_auth.a_session_maker', async_session_maker),
    ):
        auth = await saas_user_auth_from_trusted_identity(
            _request(_token(object_id=object_id, tenant_id=tenant_id))
        )

    assert auth is not None
    assert auth.user_id == str(user_id)
    assert auth.auth_type == AuthType.BEARER
    assert auth.effective_org_id_override == org_id


@pytest.mark.asyncio
async def test_trusted_identity_rejects_unmapped_identity(
    async_session_maker, monkeypatch
):
    monkeypatch.setenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET', SECRET)

    with (
        patch('server.auth.saas_user_auth.a_session_maker', async_session_maker),
        pytest.raises(TrustedIdentityError, match='not a member'),
    ):
        await saas_user_auth_from_trusted_identity(
            _request(_token(object_id=uuid4(), tenant_id=uuid4()))
        )


@pytest.mark.asyncio
async def test_trusted_identity_rejects_cross_tenant_mapping(
    async_session_maker, monkeypatch
):
    object_id = uuid4()
    mapped_tenant_id = uuid4()
    await _seed_identity(
        async_session_maker,
        object_id=object_id,
        tenant_id=mapped_tenant_id,
    )
    monkeypatch.setenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET', SECRET)

    with (
        patch('server.auth.saas_user_auth.a_session_maker', async_session_maker),
        pytest.raises(TrustedIdentityError, match='not mapped'),
    ):
        await saas_user_auth_from_trusted_identity(
            _request(_token(object_id=object_id, tenant_id=uuid4()))
        )


@pytest.mark.asyncio
async def test_trusted_identity_rejects_mapping_without_membership(
    async_session_maker, monkeypatch
):
    from sqlalchemy import delete

    object_id = uuid4()
    tenant_id = uuid4()
    user_id, org_id = await _seed_identity(
        async_session_maker,
        object_id=object_id,
        tenant_id=tenant_id,
    )
    async with async_session_maker() as session:
        await session.execute(
            delete(OrgMember).where(
                OrgMember.user_id == user_id,
                OrgMember.org_id == org_id,
            )
        )
        await session.commit()
    monkeypatch.setenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET', SECRET)

    with (
        patch('server.auth.saas_user_auth.a_session_maker', async_session_maker),
        pytest.raises(TrustedIdentityError, match='not mapped'),
    ):
        await saas_user_auth_from_trusted_identity(
            _request(_token(object_id=object_id, tenant_id=tenant_id))
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('seed_kwargs', 'error'),
    [
        ({'mapping_enabled': False}, 'mapping is disabled'),
        ({'user_disabled': True}, 'user is disabled'),
        ({'member_status': 'inactive'}, 'tenant is inactive'),
        ({'org_status': 'suspended'}, 'tenant is inactive'),
    ],
)
async def test_trusted_identity_rejects_inactive_enterprise_principal(
    async_session_maker, monkeypatch, seed_kwargs, error
):
    object_id = uuid4()
    tenant_id = uuid4()
    await _seed_identity(
        async_session_maker,
        object_id=object_id,
        tenant_id=tenant_id,
        **seed_kwargs,
    )
    monkeypatch.setenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET', SECRET)

    with (
        patch('server.auth.saas_user_auth.a_session_maker', async_session_maker),
        pytest.raises(TrustedIdentityError, match=error),
    ):
        await saas_user_auth_from_trusted_identity(
            _request(_token(object_id=object_id, tenant_id=tenant_id))
        )
