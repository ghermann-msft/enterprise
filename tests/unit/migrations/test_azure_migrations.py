import contextlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import alembic

ENV_PY = Path(__file__).parents[3] / 'migrations' / 'env.py'


class _OfflineAlembicContext:
    def __init__(self):
        self.config = SimpleNamespace(
            config_file_name=None, attributes={}, get_main_option=lambda _name: None
        )

    def is_offline_mode(self) -> bool:
        return True

    def configure(self, **_kwargs) -> None:
        pass

    @contextlib.contextmanager
    def begin_transaction(self):
        yield

    def run_migrations(self) -> None:
        pass


def test_azure_migrations_use_db_session_injector(monkeypatch):
    for key in list(os.environ):
        if key.startswith(('DB_', 'GCP_', 'PG')):
            monkeypatch.delenv(key)
    env_vars = {
        'DB_AZURE_IDENTITY': 'true',
        'DB_HOST': 'azure.postgres.database.azure.com',
        'DB_PORT': '5432',
        'DB_NAME': 'openhands',
        'DB_USER': 'halo-workload',
        'DB_SSL_MODE': 'require',
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    context = _OfflineAlembicContext()
    monkeypatch.setattr(alembic, 'context', context)
    monkeypatch.setitem(sys.modules, 'alembic.context', context)
    connector_module = MagicMock()
    connector_module.Connector = MagicMock()
    monkeypatch.setitem(sys.modules, 'google.cloud.sql.connector', connector_module)
    storage_module = ModuleType('storage')
    storage_module.__path__ = []
    storage_base_module = ModuleType('storage.base')
    storage_base_module.Base = SimpleNamespace(metadata=MagicMock())
    monkeypatch.setitem(sys.modules, 'storage', storage_module)
    monkeypatch.setitem(sys.modules, 'storage.base', storage_base_module)
    spec = importlib.util.spec_from_file_location('azure_migrations_env', ENV_PY)
    assert spec is not None and spec.loader is not None
    env = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env)

    engine = MagicMock()
    injector = MagicMock()
    injector.get_db_engine.return_value = engine
    monkeypatch.setattr(env, 'DbSessionInjector', MagicMock(return_value=injector))

    with env.migration_engine() as result:
        assert result is engine

    env.DbSessionInjector.assert_called_once_with(
        persistence_dir=Path('.'),
        host='azure.postgres.database.azure.com',
        port=5432,
        name='openhands',
        user='halo-workload',
        azure_identity=True,
        ssl_mode='require',
        pool_size=1,
        max_overflow=0,
    )
    engine.dispose.assert_called_once()
    injector.close_azure_credential.assert_called_once()
