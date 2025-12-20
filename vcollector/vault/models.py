"""
Credential data models.

Dataclasses representing credentials retrieved from the vault.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SSHCredentials:
    """SSH credentials for device authentication."""

    username: str
    password: Optional[str] = None
    key_content: Optional[str] = None  # PEM string, in-memory only
    key_passphrase: Optional[str] = None

    @property
    def has_key(self) -> bool:
        """Check if SSH key is available."""
        return self.key_content is not None

    @property
    def has_password(self) -> bool:
        """Check if password is available."""
        return self.password is not None


@dataclass
class CredentialInfo:
    """Credential set metadata (without secrets)."""

    id: int
    name: str
    username: str
    is_default: bool = False
    has_password: bool = False
    has_ssh_key: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class SNMPCredentials:
    """SNMP credentials (future use)."""

    version: str  # "2c" or "3"
    community: Optional[str] = None  # v2c
    username: Optional[str] = None  # v3
    auth_protocol: Optional[str] = None  # v3: MD5, SHA
    auth_password: Optional[str] = None  # v3
    priv_protocol: Optional[str] = None  # v3: DES, AES
    priv_password: Optional[str] = None  # v3
