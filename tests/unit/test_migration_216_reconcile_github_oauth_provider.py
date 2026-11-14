"""Real-DB tests for migration 216's GitHub OAuth provider reconciliation."""

from __future__ import annotations

import importlib

from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.engine import Engine

from storage.auth_provider_details import AuthProviderDetails, OAuthProviderDetails
from tests import postgres_testdb

migration = importlib.import_module(
    'migrations.versions.216_reconcile_github_oauth_provider'
)


def _github_rows(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, title, is_idp, color, enabled, details
                FROM auth_providers
                WHERE kind = 'OAuthProviderDetails'
                  AND details ->> 'provider_type' = 'github'
                ORDER BY id
                """
            )
        ).mappings()
        return [dict(row) for row in rows]


def _insert_existing_github_row(
    engine: Engine, *, client_id: str, enabled: bool
) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO auth_providers
                    (kind, title, is_idp, color, enabled,
                     allow_registration, allow_match_by_email, details)
                VALUES
                    ('OAuthProviderDetails', 'Admin GitHub', false, '#123456',
                     :enabled, true, true, CAST(:details AS json))
                RETURNING id
                """
            ),
            {
                'enabled': enabled,
                'details': migration._dump_details(
                    OAuthProviderDetails(
                        provider_type='github',
                        client_id=client_id,
                        client_secret='admin-secret',
                        authorization_url='https://admin.example/authorize',
                        token_url='https://admin.example/token',
                        userinfo_url='https://admin.example/user',
                        scopes=['repo'],
                    )
                ),
            },
        ).scalar_one()


def _prepare_215(engine: Engine, test_database: postgres_testdb.TestDatabase) -> None:
    """Start from the pre-216 schema without any environment-seeded GitHub row."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '215'
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM auth_providers
                WHERE kind = 'OAuthProviderDetails'
                  AND details ->> 'provider_type' = 'github'
                """
            )
        )


def _upgrade_216(test_database: postgres_testdb.TestDatabase) -> None:
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '216'
    )


def test_upgrade_inserts_encrypted_github_provider_for_existing_database(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    _prepare_215(engine, test_database)
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'halo-client')
    monkeypatch.setenv('GITHUB_APP_CLIENT_SECRET', 'halo-secret')

    _upgrade_216(test_database)

    rows = _github_rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row['title'] == 'GitHub'
    assert row['is_idp'] is False
    assert row['enabled'] is True
    assert row['details']['client_secret'] != 'halo-secret'
    details = TypeAdapter(AuthProviderDetails).validate_python(
        row['details'], context={'from_storage': True}
    )
    assert isinstance(details, OAuthProviderDetails)
    assert details.client_id == 'halo-client'
    assert details.client_secret is not None
    assert details.client_secret.get_secret_value() == 'halo-secret'
    assert details.scopes == ['repo', 'user:email', 'workflow']


def test_upgrade_without_client_id_inserts_nothing(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    _prepare_215(engine, test_database)
    monkeypatch.delenv('GITHUB_APP_CLIENT_ID', raising=False)
    monkeypatch.setenv('GITHUB_APP_CLIENT_SECRET', 'unused-secret')

    _upgrade_216(test_database)

    assert _github_rows(engine) == []


def test_upgrade_without_client_secret_inserts_nothing(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    _prepare_215(engine, test_database)
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'halo-client')
    monkeypatch.delenv('GITHUB_APP_CLIENT_SECRET', raising=False)

    _upgrade_216(test_database)

    assert _github_rows(engine) == []


def test_reconciliation_is_idempotent(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    _prepare_215(engine, test_database)
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'halo-client')
    monkeypatch.setenv('GITHUB_APP_CLIENT_SECRET', 'halo-secret')
    _upgrade_216(test_database)
    original = _github_rows(engine)

    with engine.begin() as conn:
        assert migration.reconcile_github_oauth_provider(conn) is False

    assert _github_rows(engine) == original


def test_existing_row_blocks_environment_rotation(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    _prepare_215(engine, test_database)
    existing_id = _insert_existing_github_row(
        engine, client_id='admin-client', enabled=True
    )
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'rotated-env-client')
    monkeypatch.setenv('GITHUB_APP_CLIENT_SECRET', 'rotated-env-secret')

    _upgrade_216(test_database)

    rows = _github_rows(engine)
    assert len(rows) == 1
    assert rows[0]['id'] == existing_id
    assert rows[0]['title'] == 'Admin GitHub'
    assert rows[0]['details']['client_id'] == 'admin-client'
    assert rows[0]['details']['authorization_url'] == (
        'https://admin.example/authorize'
    )


def test_existing_disabled_row_remains_disabled(
    monkeypatch,
    engine: Engine,
    test_database: postgres_testdb.TestDatabase,
):
    _prepare_215(engine, test_database)
    existing_id = _insert_existing_github_row(
        engine, client_id='disabled-client', enabled=False
    )
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'halo-client')
    monkeypatch.setenv('GITHUB_APP_CLIENT_SECRET', 'halo-secret')

    _upgrade_216(test_database)

    rows = _github_rows(engine)
    assert len(rows) == 1
    assert rows[0]['id'] == existing_id
    assert rows[0]['enabled'] is False
    assert rows[0]['details']['client_id'] == 'disabled-client'
