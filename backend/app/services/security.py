"""Password hashing (scrypt, stdlib) and JWT access tokens (HS256, PyJWT)."""
import base64
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

import jwt

ISSUER = "support-agent"
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
MIN_SECRET_LENGTH = 32


class TokenError(Exception):
    pass


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Verified against when the e-mail is unknown, so "no such user" costs the same time as "wrong password".
_DUMMY_HASH = hash_password(secrets.token_urlsafe(8))


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def create_token(secret: str, *, user_id: int, role: str, expires_minutes: int, db: str = "") -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "role": role, "db": db, "iss": ISSUER, "iat": now, "exp": now + timedelta(minutes=expires_minutes)}
    return jwt.encode(claims, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict:
    try:
        # algorithms is pinned: a token claiming alg=none or another algorithm is rejected.
        return jwt.decode(token, secret, algorithms=["HS256"], issuer=ISSUER, options={"require": ["exp", "sub", "iss"]})
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc


def generate_secret() -> str:
    return secrets.token_urlsafe(48)
