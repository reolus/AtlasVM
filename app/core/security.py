from __future__ import annotations

import base64
import hashlib
import hmac
import os


def hash_password(password: str, iterations: int = 260_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return 'pbkdf2_sha256${}${}${}'.format(
        iterations,
        base64.b64encode(salt).decode('ascii'),
        base64.b64encode(digest).decode('ascii'),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iter_s, salt_b64, digest_b64 = stored_hash.split('$', 3)
        if algorithm != 'pbkdf2_sha256':
            return False
        iterations = int(iter_s)
        salt = base64.b64decode(salt_b64.encode('ascii'))
        expected = base64.b64decode(digest_b64.encode('ascii'))
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False
