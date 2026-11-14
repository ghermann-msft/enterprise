import os
from unittest.mock import patch

from openhands.app_server.config import config_from_env
from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxServiceInjector,
)
from openhands.app_server.sandbox.remote_sandbox_spec_service import (
    RemoteSandboxSpecServiceInjector,
)


def _clean_env() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in ('PATH', 'HOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'TMP', 'TEMP')
        if key in os.environ
    }


def test_sandbox_server_url_selects_remote_sandbox_services():
    env = _clean_env()
    env['SANDBOX_SERVER_URL'] = 'http://openhands-sandbox-server:3000'

    with patch.dict(os.environ, env, clear=True):
        config = config_from_env()

    assert isinstance(config.sandbox, RemoteSandboxServiceInjector)
    assert config.sandbox.api_url == 'http://openhands-sandbox-server:3000'
    assert config.sandbox.api_key == ''
    assert isinstance(config.sandbox_spec, RemoteSandboxSpecServiceInjector)


def test_sandbox_server_url_takes_precedence_over_local_runtime():
    env = _clean_env()
    env.update(
        {
            'SANDBOX_SERVER_URL': 'http://openhands-sandbox-server:3000',
            'SANDBOX_API_KEY': 'shared-key',
            'RUNTIME': 'local',
        }
    )

    with patch.dict(os.environ, env, clear=True):
        config = config_from_env()

    assert isinstance(config.sandbox, RemoteSandboxServiceInjector)
    assert config.sandbox.api_url == 'http://openhands-sandbox-server:3000'
    assert config.sandbox.api_key == 'shared-key'
