import os
import time
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Request
from pydantic import SecretStr

from server.auth.auth_error import AuthError

DEFAULT_HEADER = 'X-OpenHands-Identity-Assertion'
DEFAULT_ISSUER = 'openhands-halo-bff'
DEFAULT_AUDIENCE = 'openhands-sandbox-server'
DEFAULT_MAX_LIFETIME_SECONDS = 300
DEFAULT_CLOCK_SKEW_SECONDS = 30
MIN_SECRET_BYTES = 32


class TrustedIdentityError(AuthError):
    pass


@dataclass(frozen=True)
class TrustedIdentity:
    object_id: UUID
    tenant_id: UUID


@dataclass(frozen=True)
class TrustedIdentityService:
    secret: SecretStr
    header: str = DEFAULT_HEADER
    issuer: str = DEFAULT_ISSUER
    audience: str = DEFAULT_AUDIENCE
    max_lifetime_seconds: int = DEFAULT_MAX_LIFETIME_SECONDS
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS

    def __post_init__(self) -> None:
        if len(self.secret.get_secret_value().encode('utf-8')) < MIN_SECRET_BYTES:
            raise TrustedIdentityError(
                'Trusted identity assertion secret must be at least 32 bytes'
            )
        if not 0 < self.max_lifetime_seconds <= 900:
            raise TrustedIdentityError(
                'Trusted identity assertion max lifetime must be between 1 and 900 seconds'
            )
        if not 0 <= self.clock_skew_seconds <= 300:
            raise TrustedIdentityError(
                'Trusted identity assertion clock skew must be between 0 and 300 seconds'
            )

    @classmethod
    def from_env(cls) -> 'TrustedIdentityService | None':
        secret = os.getenv('OH_TRUSTED_IDENTITY_ASSERTION_SECRET')
        if not secret:
            return None
        if len(secret.encode('utf-8')) < MIN_SECRET_BYTES:
            raise TrustedIdentityError(
                'OH_TRUSTED_IDENTITY_ASSERTION_SECRET must be at least 32 bytes'
            )
        return cls(
            secret=SecretStr(secret),
            header=os.getenv('OH_TRUSTED_IDENTITY_ASSERTION_HEADER', DEFAULT_HEADER),
            issuer=os.getenv('OH_TRUSTED_IDENTITY_ASSERTION_ISSUER', DEFAULT_ISSUER),
            audience=os.getenv(
                'OH_TRUSTED_IDENTITY_ASSERTION_AUDIENCE', DEFAULT_AUDIENCE
            ),
            max_lifetime_seconds=int(
                os.getenv(
                    'OH_TRUSTED_IDENTITY_ASSERTION_MAX_LIFETIME_SECONDS',
                    str(DEFAULT_MAX_LIFETIME_SECONDS),
                )
            ),
            clock_skew_seconds=int(
                os.getenv(
                    'OH_TRUSTED_IDENTITY_ASSERTION_CLOCK_SKEW_SECONDS',
                    str(DEFAULT_CLOCK_SKEW_SECONDS),
                )
            ),
        )

    def verify_request(self, request: Request) -> TrustedIdentity:
        assertion = request.headers.get(self.header)
        if not assertion:
            raise TrustedIdentityError('Missing trusted identity assertion')
        try:
            claims = jwt.decode(
                assertion,
                self.secret.get_secret_value(),
                algorithms=['HS256'],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.clock_skew_seconds,
                options={
                    'require': ['aud', 'exp', 'iat', 'iss', 'oid', 'tid'],
                },
            )
            issued_at = claims['iat']
            expires_at = claims['exp']
            if (
                not isinstance(issued_at, (int, float))
                or not isinstance(expires_at, (int, float))
                or expires_at <= issued_at
                or expires_at - issued_at > self.max_lifetime_seconds
            ):
                raise TrustedIdentityError(
                    'Trusted identity assertion lifetime is invalid'
                )
            if issued_at > time.time() + self.clock_skew_seconds:
                raise TrustedIdentityError(
                    'Trusted identity assertion was issued in the future'
                )
            return TrustedIdentity(
                object_id=UUID(claims['oid']),
                tenant_id=UUID(claims['tid']),
            )
        except TrustedIdentityError:
            raise
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise TrustedIdentityError('Trusted identity assertion is invalid') from exc


def get_trusted_identity_service() -> TrustedIdentityService | None:
    return TrustedIdentityService.from_env()
