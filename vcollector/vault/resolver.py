"""
Credential Resolver - Vault unlock and credential retrieval.

This module handles:
- Vault initialization with master password
- Unlocking vault for credential access
- Adding, removing, updating credentials
- Retrieving decrypted credentials for use

TODO: Port from vcmdbv2/vcollector/credential_resolver.py
"""

import os
import sqlite3
import hashlib
import base64
from pathlib import Path
from typing import Optional, List

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from vcollector.core.config import get_config
from vcollector.vault.models import SSHCredentials, CredentialInfo


class CredentialResolver:
    """
    Manages encrypted credential vault.
    
    The vault stores credentials encrypted with Fernet symmetric encryption.
    A master password is used to derive the encryption key via PBKDF2.
    
    Usage:
        resolver = CredentialResolver()
        
        # Initialize new vault
        resolver.init_vault(master_password="secret")
        
        # Unlock existing vault
        if resolver.unlock_vault(password="secret"):
            creds = resolver.get_ssh_credentials(credential_name="lab")
            # use creds.username, creds.password, creds.key_content
        
        # Lock when done
        resolver.lock_vault()
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize resolver.
        
        Args:
            db_path: Path to collector.db. If None, uses config default.
        """
        config = get_config()
        self.db_path = db_path or config.collector_db
        self._fernet: Optional[Fernet] = None
        self._unlocked = False

    @property
    def is_unlocked(self) -> bool:
        """Check if vault is currently unlocked."""
        return self._unlocked and self._fernet is not None

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection, creating schema if needed."""
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # Create tables if they don't exist
        self._ensure_schema(conn)
        
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection):
        """Create database schema if it doesn't exist."""
        cursor = conn.cursor()
        
        cursor.executescript("""
            -- Vault metadata
            CREATE TABLE IF NOT EXISTS vault_metadata (
                id INTEGER PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                value TEXT
            );
            
            -- Encrypted credentials
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                password_encrypted TEXT,
                ssh_key_encrypted TEXT,
                ssh_key_passphrase_encrypted TEXT,
                is_default INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Job execution history
            CREATE TABLE IF NOT EXISTS job_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                job_file TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                total_devices INTEGER,
                success_count INTEGER,
                failed_count INTEGER,
                status TEXT,
                error_message TEXT
            );
            
            -- Capture metadata  
            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER,
                device_name TEXT NOT NULL,
                capture_type TEXT NOT NULL,
                filepath TEXT NOT NULL,
                file_size INTEGER,
                captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
                job_history_id INTEGER,
                FOREIGN KEY (job_history_id) REFERENCES job_history(id)
            );
            
            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_credentials_name ON credentials(name);
            CREATE INDEX IF NOT EXISTS idx_credentials_default ON credentials(is_default);
            CREATE INDEX IF NOT EXISTS idx_job_history_started ON job_history(started_at);
            CREATE INDEX IF NOT EXISTS idx_captures_device ON captures(device_name);
            CREATE INDEX IF NOT EXISTS idx_captures_type ON captures(capture_type);
        """)
        
        conn.commit()

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        """Derive encryption key from password using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def is_initialized(self) -> bool:
        """Check if vault has been initialized."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT value FROM vault_metadata WHERE key = 'password_hash'"
        )
        row = cursor.fetchone()
        conn.close()
        
        return row is not None

    def init_vault(self, master_password: str) -> bool:
        """
        Initialize vault with master password.
        
        Args:
            master_password: Master password for vault encryption.
            
        Returns:
            True if successful.
            
        Raises:
            ValueError: If vault already initialized.
        """
        if self.is_initialized():
            raise ValueError("Vault already initialized")
        
        # Generate salt
        salt = os.urandom(16)
        
        # Hash password for verification
        password_hash = hashlib.pbkdf2_hmac(
            'sha256',
            master_password.encode(),
            salt,
            100000
        )
        
        # Store salt and hash
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO vault_metadata (key, value) VALUES (?, ?)",
            ('salt', base64.b64encode(salt).decode())
        )
        cursor.execute(
            "INSERT INTO vault_metadata (key, value) VALUES (?, ?)",
            ('password_hash', base64.b64encode(password_hash).decode())
        )
        
        conn.commit()
        conn.close()
        
        # Unlock vault
        return self.unlock_vault(master_password)

    def unlock_vault(self, password: str) -> bool:
        """
        Unlock vault with master password.
        
        Args:
            password: Master password.
            
        Returns:
            True if password correct and vault unlocked.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Get salt and hash
        cursor.execute(
            "SELECT key, value FROM vault_metadata WHERE key IN ('salt', 'password_hash')"
        )
        rows = {row['key']: row['value'] for row in cursor.fetchall()}
        conn.close()
        
        if 'salt' not in rows or 'password_hash' not in rows:
            return False
        
        salt = base64.b64decode(rows['salt'])
        stored_hash = base64.b64decode(rows['password_hash'])
        
        # Verify password
        computed_hash = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode(),
            salt,
            100000
        )
        
        if computed_hash != stored_hash:
            return False
        
        # Derive encryption key
        key = self._derive_key(password, salt)
        self._fernet = Fernet(key)
        self._unlocked = True
        
        return True

    def lock_vault(self):
        """Lock vault, clearing encryption key from memory."""
        self._fernet = None
        self._unlocked = False

    def _encrypt(self, plaintext: str) -> str:
        """Encrypt string."""
        if not self._fernet:
            raise RuntimeError("Vault not unlocked")
        return self._fernet.encrypt(plaintext.encode()).decode()

    def _decrypt(self, ciphertext: str) -> str:
        """Decrypt string."""
        if not self._fernet:
            raise RuntimeError("Vault not unlocked")
        return self._fernet.decrypt(ciphertext.encode()).decode()

    def add_credential(
        self,
        name: str,
        username: str,
        password: Optional[str] = None,
        ssh_key: Optional[str] = None,
        ssh_key_passphrase: Optional[str] = None,
        is_default: bool = False,
    ) -> int:
        """
        Add credential set to vault.
        
        Args:
            name: Unique name for credential set.
            username: SSH username.
            password: SSH password (optional if using key).
            ssh_key: SSH private key PEM content (optional).
            ssh_key_passphrase: Passphrase for SSH key (optional).
            is_default: Set as default credential.
            
        Returns:
            ID of created credential.
        """
        if not self.is_unlocked:
            raise RuntimeError("Vault not unlocked")
        
        # Encrypt sensitive fields
        password_enc = self._encrypt(password) if password else None
        key_enc = self._encrypt(ssh_key) if ssh_key else None
        passphrase_enc = self._encrypt(ssh_key_passphrase) if ssh_key_passphrase else None
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # If setting as default, clear other defaults
        if is_default:
            cursor.execute("UPDATE credentials SET is_default = 0")
        
        cursor.execute("""
            INSERT INTO credentials (
                name, username, password_encrypted, 
                ssh_key_encrypted, ssh_key_passphrase_encrypted, is_default
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (name, username, password_enc, key_enc, passphrase_enc, 1 if is_default else 0))
        
        cred_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return cred_id

    def remove_credential(self, name: str) -> bool:
        """Remove credential set."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM credentials WHERE name = ?", (name,))
        deleted = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return deleted

    def set_default(self, name: str) -> bool:
        """Set credential as default."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Clear existing default
        cursor.execute("UPDATE credentials SET is_default = 0")
        
        # Set new default
        cursor.execute(
            "UPDATE credentials SET is_default = 1 WHERE name = ?",
            (name,)
        )
        updated = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return updated

    def list_credentials(self) -> List[CredentialInfo]:
        """List all credential sets (without secrets)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, username, is_default,
                   password_encrypted IS NOT NULL as has_password,
                   ssh_key_encrypted IS NOT NULL as has_ssh_key,
                   created_at, updated_at
            FROM credentials
            ORDER BY name
        """)
        
        results = []
        for row in cursor.fetchall():
            results.append(CredentialInfo(
                id=row['id'],
                name=row['name'],
                username=row['username'],
                is_default=bool(row['is_default']),
                has_password=bool(row['has_password']),
                has_ssh_key=bool(row['has_ssh_key']),
                created_at=row['created_at'],
                updated_at=row['updated_at'],
            ))
        
        conn.close()
        return results

    def get_ssh_credentials(
        self,
        credential_name: Optional[str] = None
    ) -> Optional[SSHCredentials]:
        """
        Get decrypted SSH credentials.
        
        Args:
            credential_name: Name of credential set. If None, uses default.
            
        Returns:
            SSHCredentials or None if not found.
        """
        if not self.is_unlocked:
            raise RuntimeError("Vault not unlocked")
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if credential_name:
            cursor.execute(
                "SELECT * FROM credentials WHERE name = ?",
                (credential_name,)
            )
        else:
            cursor.execute(
                "SELECT * FROM credentials WHERE is_default = 1"
            )
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        # Decrypt fields
        password = None
        if row['password_encrypted']:
            password = self._decrypt(row['password_encrypted'])
        
        key_content = None
        if row['ssh_key_encrypted']:
            key_content = self._decrypt(row['ssh_key_encrypted'])
        
        key_passphrase = None
        if row['ssh_key_passphrase_encrypted']:
            key_passphrase = self._decrypt(row['ssh_key_passphrase_encrypted'])
        
        return SSHCredentials(
            username=row['username'],
            password=password,
            key_content=key_content,
            key_passphrase=key_passphrase,
        )
