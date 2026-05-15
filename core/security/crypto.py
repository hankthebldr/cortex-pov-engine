"""Crypto primitives for the credentials layer.

Why HKDF: ``CORTEXSIM_SECRET`` is an arbitrary string the operator typed
(often loaded from 1Password). Fernet requires a 32-byte URL-safe base64
key — passing the raw secret would either fail (wrong length) or weaken
the key (low-entropy substrings). HKDF stretches the master key to exactly
32 bytes of high-entropy output without exposing the master key directly.

We use a fixed application-scoped salt and ``info`` label so the same
master key always derives the same data key. That's intentional: rotating
the data key without rotating the master key is not a goal for V1. To
rotate, generate a new CORTEXSIM_SECRET and re-encrypt every Secret row
(rotation tool: scripts/rotate-master-key.py — planned, Phase 9-A).
"""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# Application-scoped, public, non-secret. Changing this value rotates every
# stored Secret's effective key — only do that during a coordinated rotation.
_HKDF_SALT = b"cortexsim/secret-store/v1"
_HKDF_INFO = b"cortexsim.credentials.fernet"


class CryptoError(RuntimeError):
    """Raised on encryption/decryption failure (bad ciphertext, wrong key)."""


def derive_fernet_key(master_key: str) -> bytes:
    """Stretch a master-key string into a 32-byte url-safe base64 Fernet key.

    Output is stable for a given (master_key, _HKDF_SALT, _HKDF_INFO) triple.
    """
    if not master_key:
        raise CryptoError("Empty master key passed to derive_fernet_key")

    raw_master = master_key.encode("utf-8")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    derived = hkdf.derive(raw_master)
    return base64.urlsafe_b64encode(derived)
