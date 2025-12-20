"""
Vault CLI handler.

Handles: vcollector vault <command>
"""

import sys
import getpass
from pathlib import Path

from vcollector.vault.resolver import CredentialResolver
from vcollector.core.config import get_config


def handle_vault(args) -> int:
    """Handle vault subcommand."""
    
    if not args.vault_command:
        print("Usage: vcollector vault <command>")
        print("Commands: init, add, list, remove, set-default, change-password")
        return 1

    resolver = CredentialResolver()

    if args.vault_command == "init":
        return _vault_init(resolver)
    elif args.vault_command == "add":
        return _vault_add(resolver, args)
    elif args.vault_command == "list":
        return _vault_list(resolver)
    elif args.vault_command == "remove":
        return _vault_remove(resolver, args)
    elif args.vault_command == "set-default":
        return _vault_set_default(resolver, args)
    elif args.vault_command == "change-password":
        return _vault_change_password(resolver)
    else:
        print(f"Unknown vault command: {args.vault_command}")
        return 1


def _vault_init(resolver: CredentialResolver) -> int:
    """Initialize vault."""
    if resolver.is_initialized():
        print("Vault already initialized")
        print(f"Database: {resolver.db_path}")
        return 1

    print("Initializing credential vault...")
    print(f"Database: {resolver.db_path}")
    print()

    password = getpass.getpass("Enter master password: ")
    confirm = getpass.getpass("Confirm master password: ")

    if password != confirm:
        print("Error: Passwords do not match")
        return 1

    if len(password) < 8:
        print("Error: Password must be at least 8 characters")
        return 1

    try:
        resolver.init_vault(password)
        print("\n✓ Vault initialized successfully")
        print("\nNext steps:")
        print("  vcollector vault add <name> --username <user>")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def _vault_add(resolver: CredentialResolver, args) -> int:
    """Add credential set."""
    if not resolver.is_initialized():
        print("Error: Vault not initialized. Run 'vcollector vault init' first.")
        return 1

    # Unlock vault
    password = getpass.getpass("Vault password: ")
    if not resolver.unlock_vault(password):
        print("Error: Invalid vault password")
        return 1

    try:
        # Get SSH password
        ssh_password = getpass.getpass(f"SSH password for {args.username} (enter to skip): ")
        if not ssh_password:
            ssh_password = None

        # Get SSH key if specified
        ssh_key = None
        ssh_key_passphrase = None
        if args.key_file:
            key_path = Path(args.key_file).expanduser()
            if not key_path.exists():
                print(f"Error: Key file not found: {key_path}")
                return 1
            ssh_key = key_path.read_text()
            ssh_key_passphrase = getpass.getpass("Key passphrase (enter if none): ")
            if not ssh_key_passphrase:
                ssh_key_passphrase = None

        # Validate we have at least one auth method
        if not ssh_password and not ssh_key:
            print("Error: Must provide password or SSH key")
            return 1

        # Add credential
        cred_id = resolver.add_credential(
            name=args.name,
            username=args.username,
            password=ssh_password,
            ssh_key=ssh_key,
            ssh_key_passphrase=ssh_key_passphrase,
            is_default=args.default,
        )

        print(f"\n✓ Added credential '{args.name}' (id={cred_id})")
        if args.default:
            print("  Set as default")

        return 0

    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        resolver.lock_vault()


def _vault_list(resolver: CredentialResolver) -> int:
    """List credentials."""
    if not resolver.is_initialized():
        print("Vault not initialized")
        return 1

    # Listing doesn't require unlock (no secrets shown)
    creds = resolver.list_credentials()

    if not creds:
        print("No credentials stored")
        print("\nAdd credentials with: vcollector vault add <name> --username <user>")
        return 0

    print(f"Credential sets ({len(creds)}):\n")
    
    for c in creds:
        default_marker = " (default)" if c.is_default else ""
        auth_types = []
        if c.has_password:
            auth_types.append("password")
        if c.has_ssh_key:
            auth_types.append("key")
        auth_str = ", ".join(auth_types) or "none"

        print(f"  {c.name}{default_marker}")
        print(f"    Username: {c.username}")
        print(f"    Auth: {auth_str}")
        print()

    return 0


def _vault_remove(resolver: CredentialResolver, args) -> int:
    """Remove credential set."""
    if not resolver.is_initialized():
        print("Error: Vault not initialized")
        return 1

    # Confirm
    confirm = input(f"Remove credential '{args.name}'? [y/N]: ")
    if confirm.lower() != 'y':
        print("Aborted")
        return 0

    if resolver.remove_credential(args.name):
        print(f"✓ Removed credential '{args.name}'")
        return 0
    else:
        print(f"Error: Credential '{args.name}' not found")
        return 1


def _vault_set_default(resolver: CredentialResolver, args) -> int:
    """Set default credential."""
    if not resolver.is_initialized():
        print("Error: Vault not initialized")
        return 1

    if resolver.set_default(args.name):
        print(f"✓ Set '{args.name}' as default credential")
        return 0
    else:
        print(f"Error: Credential '{args.name}' not found")
        return 1


def _vault_change_password(resolver: CredentialResolver) -> int:
    """Change master password."""
    # TODO: Implement - requires decrypting all creds and re-encrypting
    print("Not implemented yet")
    return 1
