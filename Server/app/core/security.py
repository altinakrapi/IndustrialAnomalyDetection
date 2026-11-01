import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import HTTPException, status

from app.core.config import settings

_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS
    )
    return f"{_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        iterations, salt_hex, digest_hex = stored_hash.split("$", 2)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int) -> str:
    payload = json.dumps(
        {"sub": user_id, "exp": int(time.time()) + 8 * 60 * 60},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(
        settings.SECRET_KEY.encode("utf-8"), encoded, hashlib.sha256
    ).digest()
    return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def decode_access_token(token: str) -> int:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("Invalid token signature")
        payload_bytes = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(payload_bytes)
        user_id = payload["sub"]
        if not isinstance(user_id, int) or payload["exp"] <= int(time.time()):
            raise ValueError("Token expired or invalid")
        return user_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc