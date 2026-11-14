import json
from unittest.mock import AsyncMock, call, patch
from uuid import UUID, uuid4

import pytest

from storage.trusted_identity_mapping_store import (
    TrustedIdentityBootstrap,
    TrustedIdentityMappingStore,
    reconcile_trusted_identity_mappings_from_env,
)


@pytest.mark.asyncio
async def test_reconcile_trusted_identity_mappings_from_env(monkeypatch):
    first = {
        'tenant_id': str(uuid4()),
        'object_id': str(uuid4()),
        'org_id': str(uuid4()),
        'user_id': str(uuid4()),
    }
    second = {
        'tenant_id': str(uuid4()),
        'object_id': str(uuid4()),
        'org_id': str(uuid4()),
        'user_id': str(uuid4()),
        'enabled': False,
    }
    monkeypatch.setenv('OH_TRUSTED_IDENTITY_MAPPINGS', json.dumps([first, second]))

    with patch(
        'storage.trusted_identity_mapping_store.TrustedIdentityMappingStore.reconcile',
        new_callable=AsyncMock,
    ) as reconcile:
        assert await reconcile_trusted_identity_mappings_from_env() == 2

    assert reconcile.await_args_list == [
        call(
            tenant_id=UUID(first['tenant_id']),
            object_id=UUID(first['object_id']),
            org_id=UUID(first['org_id']),
            user_id=UUID(first['user_id']),
            enabled=True,
            bootstrap=None,
        ),
        call(
            tenant_id=UUID(second['tenant_id']),
            object_id=UUID(second['object_id']),
            org_id=UUID(second['org_id']),
            user_id=UUID(second['user_id']),
            enabled=False,
            bootstrap=None,
        ),
    ]


@pytest.mark.asyncio
async def test_reconcile_trusted_identity_mappings_rejects_malformed_config(
    monkeypatch,
):
    monkeypatch.setenv('OH_TRUSTED_IDENTITY_MAPPINGS', '[{"tenant_id":"bad"}]')

    with pytest.raises(ValueError, match='require UUID'):
        await reconcile_trusted_identity_mappings_from_env()


@pytest.mark.asyncio
async def test_reconcile_bootstraps_fresh_enterprise_principal(
    async_session_maker,
):
    from storage.org import Org
    from storage.org_member import OrgMember
    from storage.trusted_identity_mapping import TrustedIdentityMapping
    from storage.user import User

    tenant_id = uuid4()
    object_id = uuid4()
    org_id = uuid4()
    user_id = uuid4()
    bootstrap = TrustedIdentityBootstrap(
        org_name='Halo PPE',
        user_email='halo-owner@example.com',
    )

    with patch(
        'storage.trusted_identity_mapping_store.a_session_maker',
        async_session_maker,
    ):
        mapping = await TrustedIdentityMappingStore().reconcile(
            tenant_id=tenant_id,
            object_id=object_id,
            org_id=org_id,
            user_id=user_id,
            bootstrap=bootstrap,
        )

    assert mapping.org_id == org_id
    assert mapping.user_id == user_id
    async with async_session_maker() as session:
        org = await session.get(Org, org_id)
        user = await session.get(User, user_id)
        member = await session.get(OrgMember, (org_id, user_id))
        persisted = await session.get(
            TrustedIdentityMapping,
            {'tenant_id': tenant_id, 'object_id': object_id},
        )
    assert org is not None and org.name == 'Halo PPE'
    assert user is not None and user.email == 'halo-owner@example.com'
    assert user.email_verified is True
    assert user.accepted_tos is not None
    assert user.onboarding_completed is True
    assert member is not None and member.status == 'active'
    assert persisted is not None


@pytest.mark.asyncio
async def test_reconcile_bootstrap_replay_is_idempotent(async_session_maker):
    kwargs = {
        'tenant_id': uuid4(),
        'object_id': uuid4(),
        'org_id': uuid4(),
        'user_id': uuid4(),
        'bootstrap': TrustedIdentityBootstrap(
            org_name='Halo PPE',
            user_email='halo-owner@example.com',
        ),
    }
    with patch(
        'storage.trusted_identity_mapping_store.a_session_maker',
        async_session_maker,
    ):
        first = await TrustedIdentityMappingStore().reconcile(**kwargs)
        second = await TrustedIdentityMappingStore().reconcile(**kwargs)

    assert first.tenant_id == second.tenant_id
    assert first.object_id == second.object_id


@pytest.mark.asyncio
async def test_reconcile_rejects_conflicting_persisted_mapping(async_session_maker):
    kwargs = {
        'tenant_id': uuid4(),
        'object_id': uuid4(),
        'org_id': uuid4(),
        'user_id': uuid4(),
        'bootstrap': TrustedIdentityBootstrap(
            org_name='Halo PPE',
            user_email='halo-owner@example.com',
        ),
    }
    with patch(
        'storage.trusted_identity_mapping_store.a_session_maker',
        async_session_maker,
    ):
        await TrustedIdentityMappingStore().reconcile(**kwargs)
        with pytest.raises(ValueError, match='conflicts'):
            await TrustedIdentityMappingStore().reconcile(
                **{**kwargs, 'user_id': uuid4()}
            )
