import hashlib
import secrets

from pwdlib import PasswordHash

passwords = PasswordHash.recommended()
# Equal-cost negative login path, generated once per process, never persisted.
dummy_hash = passwords.hash(secrets.token_urlsafe(32))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def pairing_code() -> str:
    return "".join(secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(10))
