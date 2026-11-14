import time
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import Request
from pydantic import SecretStr

from server.auth.trusted_identity import (
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    TrustedIdentityError,
    TrustedIdentityService,
    get_trusted_identity_service,
)

SECRET = 'a-trusted-identity-secret-at-least-32-bytes'


def _service() -> TrustedIdentityService:
    return TrustedIdentityService(SecretStr(SECRET))


def _request(token: str | None = None) -> Request:
    headers = []
    if token is not None:
        headers.append((b'x-openhands-identity-assertion', token.encode()))
    return Request({'type': 'http', 'headers': headers})


def _token(
    *,
    secret: str = SECRET,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    object_id: UUID | None = None,
    tenant_id: UUID | None = None,
    issued_at: int | None = None,
    expires_at: int | None = None,
    subject: str | None = None,
) -> str:
    now = int(time.time())
    oid = str(object_id or uuid4())
    return jwt.encode(
        {
            'iss': issuer,
            'aud': audience,
            'sub': subject or oid,
            'oid': oid,
            'tid': str(tenant_id or uuid4()),
            'iat': issued_at or now,
            'exp': expires_at or now + 60,
        },
        secret,
        algorithm='HS256',
        headers={'alg': 'HS256', 'typ': 'JWT'},
    )


def test_trusted_identity_disabled_without_secret(monkeypatch):
    monkeypatch.delenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET', raising=False)
    assert get_trusted_identity_service() is None


def test_trusted_identity_enabled_from_env(monkeypatch):
    monkeypatch.setenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET', SECRET)
    service = get_trusted_identity_service()
    assert service is not None
    assert service.header == 'X-OpenHands-Identity-Assertion'
    assert service.issuer == DEFAULT_ISSUER
    assert service.audience == DEFAULT_AUDIENCE


def test_trusted_identity_rejects_short_secret(monkeypatch):
    monkeypatch.setenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET', 'too-short')
    with pytest.raises(TrustedIdentityError, match='at least 32 bytes'):
        get_trusted_identity_service()


def test_trusted_identity_enabled_requires_assertion():
    with pytest.raises(TrustedIdentityError, match='Missing'):
        _service().verify_request(_request())


def test_trusted_identity_valid_assertion():
    object_id = uuid4()
    tenant_id = uuid4()
    identity = _service().verify_request(
        _request(_token(object_id=object_id, tenant_id=tenant_id))
    )
    assert identity is not None
    assert identity.object_id == object_id
    assert identity.tenant_id == tenant_id


@pytest.mark.parametrize(
    'token',
    [
        _token(secret='different-secret-at-least-32-bytes-long'),
        _token(expires_at=int(time.time()) - 60),
        _token(issuer='untrusted-issuer'),
        _token(audience='wrong-audience'),
    ],
)
def test_trusted_identity_rejects_untrusted_assertions(token):
    with pytest.raises(TrustedIdentityError):
        _service().verify_request(_request(token))


def test_trusted_identity_rejects_invalid_provenance():
    now = int(time.time())
    token = jwt.encode(
        {
            'iss': DEFAULT_ISSUER,
            'aud': DEFAULT_AUDIENCE,
            'oid': 'not-a-uuid',
            'tid': str(uuid4()),
            'iat': now,
            'exp': now + 60,
        },
        SECRET,
        algorithm='HS256',
    )
    with pytest.raises(TrustedIdentityError, match='invalid'):
        _service().verify_request(_request(token))
