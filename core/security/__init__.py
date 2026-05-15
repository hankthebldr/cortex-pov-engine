"""CortexSim security primitives — credential storage, crypto wrappers.

Public API:
    CredentialStore   — async high-level secrets + integration management
    redaction_policy  — pluggable plaintext → preview-tail policy
    MasterKeyError    — boot-time misconfiguration
"""

from .credentials import CredentialStore, redaction_policy
from .crypto import CryptoError, derive_fernet_key

__all__ = [
    "CredentialStore",
    "CryptoError",
    "MasterKeyError",
    "derive_fernet_key",
    "redaction_policy",
]

from config import MasterKeyError  # re-export for convenience
