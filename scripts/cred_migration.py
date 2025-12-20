"""
Schema Migration: Add credential testing columns to dcim_device.

Path: scripts/schema_migration_v2.py (or run standalone)

This file shows the changes needed in db_schema.py for credential discovery support.

Changes:
1. Bump SCHEMA_VERSION to 2
2. Add migration SQL for new columns
3. Update v_device_detail view to include new columns
4. Add migration runner

Usage:
    Run this directly to test migration on existing database:

    python schema_migration_v2.py [--db-path ~/.vcollector/dcim.db]
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import argparse
import sys

MIGRATION_V2_SQL = """
-- Migration V2: Credential testing support
-- Add columns to track credential test results per device

-- Add new columns (SQLite requires one ALTER TABLE per column)
ALTER TABLE dcim_device ADD COLUMN credential_tested_at TEXT;
ALTER TABLE dcim_device ADD COLUMN credential_test_result TEXT DEFAULT 'untested';

-- Add index for filtering by test result
CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_result ON dcim_device(credential_test_result);
CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_tested ON dcim_device(credential_tested_at);
"""

# Updated view definition to include new columns
VIEW_UPDATE_SQL = """
-- Drop and recreate v_device_detail to include new columns
DROP VIEW IF EXISTS v_device_detail;

CREATE VIEW v_device_detail AS
SELECT 
    d.id,
    d.name,
    d.status,
    d.primary_ip4,
    d.primary_ip6,
    d.oob_ip,
    d.ssh_port,
    d.serial_number,
    d.asset_tag,
    d.credential_id,
    d.credential_tested_at,
    d.credential_test_result,
    d.description,
    d.last_collected_at,
    d.netbox_id,
    d.created_at,
    d.updated_at,
    -- Site info
    s.id AS site_id,
    s.name AS site_name,
    s.slug AS site_slug,
    -- Platform info
    p.id AS platform_id,
    p.name AS platform_name,
    p.slug AS platform_slug,
    p.netmiko_device_type,
    p.paging_disable_command,
    -- Manufacturer info (via platform)
    m.id AS manufacturer_id,
    m.name AS manufacturer_name,
    m.slug AS manufacturer_slug,
    -- Role info
    r.id AS role_id,
    r.name AS role_name,
    r.slug AS role_slug
FROM dcim_device d
LEFT JOIN dcim_site s ON d.site_id = s.id
LEFT JOIN dcim_platform p ON d.platform_id = p.id
LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
LEFT JOIN dcim_device_role r ON d.role_id = r.id;
"""


def check_migration_needed(conn: sqlite3.Connection) -> bool:
    """Check if migration is needed by looking for new columns."""
    cursor = conn.execute("PRAGMA table_info(dcim_device)")
    columns = {row[1] for row in cursor.fetchall()}
    return 'credential_tested_at' not in columns


def get_current_version(conn: sqlite3.Connection) -> int:
    """Get current schema version."""
    try:
        cursor = conn.execute("SELECT MAX(version) FROM schema_version")
        result = cursor.fetchone()
        return result[0] if result and result[0] else 1
    except sqlite3.OperationalError:
        return 1


def run_migration(db_path: Path, dry_run: bool = False) -> bool:
    """
    Run migration on database.

    Args:
        db_path: Path to dcim.db
        dry_run: If True, show what would be done without executing

    Returns:
        True if migration was successful or not needed
    """
    if not db_path.exists():
        print(f"Error: Database not found: {db_path}")
        return False

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        current_version = get_current_version(conn)
        print(f"Current schema version: {current_version}")

        if not check_migration_needed(conn):
            print("Migration not needed - columns already exist")
            return True

        print("Migration V2 required: Adding credential testing columns")
        print()

        if dry_run:
            print("DRY RUN - Would execute:")
            print("-" * 60)
            print(MIGRATION_V2_SQL)
            print("-" * 60)
            print(VIEW_UPDATE_SQL)
            return True

        # Execute migration
        print("Executing migration...")

        # SQLite doesn't support multiple statements in ALTER TABLE
        # Execute each ALTER TABLE separately
        for line in MIGRATION_V2_SQL.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('--'):
                try:
                    conn.execute(line)
                    print(f"  ✓ {line[:60]}...")
                except sqlite3.OperationalError as e:
                    if 'duplicate column' in str(e).lower():
                        print(f"  - Column already exists, skipping")
                    else:
                        raise

        # Update view
        conn.executescript(VIEW_UPDATE_SQL)
        print("  ✓ Updated v_device_detail view")

        # Update schema version
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (2, datetime.now().isoformat())
        )
        print("  ✓ Updated schema_version to 2")

        conn.commit()
        print()
        print("Migration complete!")

        # Verify
        cursor = conn.execute("PRAGMA table_info(dcim_device)")
        columns = [row[1] for row in cursor.fetchall()]
        print(f"Device columns: {', '.join(columns[-5:])}")  # Show last 5

        return True

    except Exception as e:
        print(f"Migration failed: {e}")
        conn.rollback()
        return False

    finally:
        conn.close()


# =============================================================================
# Code to add to db_schema.py
# =============================================================================

DB_SCHEMA_ADDITIONS = '''
# Add to db_schema.py after line ~20:

SCHEMA_VERSION = 2  # Bump from 1

# Add after SCHEMA_SQL definition (~line 270):

MIGRATION_V2_SQL = """
-- Migration V2: Credential testing support
ALTER TABLE dcim_device ADD COLUMN credential_tested_at TEXT;
ALTER TABLE dcim_device ADD COLUMN credential_test_result TEXT DEFAULT 'untested';
CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_result ON dcim_device(credential_test_result);
CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_tested ON dcim_device(credential_tested_at);
"""

# Update the init_schema method in DCIMDatabase class (~line 377):

def init_schema(self, include_defaults: bool = True):
    """Initialize database schema."""
    cursor = self.conn.cursor()

    # ... existing code ...

    # Check version for migrations
    cursor.execute("SELECT MAX(version) FROM schema_version")
    current_version = cursor.fetchone()[0] or 0

    if current_version < 2:
        # Run V2 migration
        self._run_migration_v2(cursor)
        cursor.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (2,)
        )
        self.conn.commit()

    return False

def _run_migration_v2(self, cursor: sqlite3.Cursor):
    """Run V2 migration - add credential testing columns."""
    # Check if columns exist
    cursor.execute("PRAGMA table_info(dcim_device)")
    columns = {row[1] for row in cursor.fetchall()}

    if 'credential_tested_at' not in columns:
        cursor.execute(
            "ALTER TABLE dcim_device ADD COLUMN credential_tested_at TEXT"
        )

    if 'credential_test_result' not in columns:
        cursor.execute(
            "ALTER TABLE dcim_device ADD COLUMN credential_test_result TEXT DEFAULT 'untested'"
        )

    # Add indexes (CREATE INDEX IF NOT EXISTS is safe)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_result ON dcim_device(credential_test_result)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_tested ON dcim_device(credential_tested_at)"
    )
'''

# =============================================================================
# Code to add to dcim_repo.py
# =============================================================================

DCIM_REPO_ADDITIONS = '''
# Add to Device dataclass in dcim_repo.py (~line 113):

@dataclass
class Device:
    """Device model."""
    id: Optional[int] = None
    name: str = ""
    # ... existing fields ...
    credential_id: Optional[int] = None
    credential_tested_at: Optional[str] = None      # NEW
    credential_test_result: Optional[str] = None    # NEW: 'untested', 'success', 'failed'
    ssh_port: int = 22
    # ... rest of fields ...


# Add new method to DCIMRepository (~line 686):

def get_devices_by_credential_status(
    self,
    test_result: Optional[str] = None,
    has_credential: Optional[bool] = None,
    untested_only: bool = False,
) -> List[Device]:
    """
    Get devices filtered by credential status.

    Args:
        test_result: Filter by test result ('success', 'failed', 'untested')
        has_credential: True = only devices with credential_id set,
                       False = only devices without credential_id
        untested_only: Shortcut for test_result='untested'

    Returns:
        List of Device objects
    """
    query = "SELECT * FROM v_device_detail WHERE status = 'active'"
    params = []

    if untested_only:
        test_result = 'untested'

    if test_result:
        query += " AND (credential_test_result = ? OR credential_test_result IS NULL)"
        params.append(test_result)

    if has_credential is True:
        query += " AND credential_id IS NOT NULL"
    elif has_credential is False:
        query += " AND credential_id IS NULL"

    query += " ORDER BY site_name, name"

    rows = self.conn.execute(query, params).fetchall()
    return [self._row_to_dataclass(row, Device) for row in rows]


def get_credential_coverage_stats(self) -> Dict[str, int]:
    """Get credential coverage statistics."""
    stats = {}

    stats['total_active'] = self.conn.execute(
        "SELECT COUNT(*) FROM dcim_device WHERE status = 'active'"
    ).fetchone()[0]

    stats['with_credential'] = self.conn.execute(
        "SELECT COUNT(*) FROM dcim_device WHERE status = 'active' AND credential_id IS NOT NULL"
    ).fetchone()[0]

    stats['without_credential'] = self.conn.execute(
        "SELECT COUNT(*) FROM dcim_device WHERE status = 'active' AND credential_id IS NULL"
    ).fetchone()[0]

    # By test result
    for result in ['success', 'failed', 'untested']:
        stats[f'test_{result}'] = self.conn.execute(
            """SELECT COUNT(*) FROM dcim_device 
               WHERE status = 'active' AND (credential_test_result = ? OR 
               (credential_test_result IS NULL AND ? = 'untested'))""",
            (result, result)
        ).fetchone()[0]

    return stats
'''

# =============================================================================
# Code to add to main.py
# =============================================================================

MAIN_PY_ADDITIONS = '''
# Add to main.py imports (~line 10):
from vcollector.cli.creds import handle_creds, setup_creds_parser

# Add to main() after other subparsers (~line 110):

    # Creds subcommand
    setup_creds_parser(subparsers)

# Add to dispatch section (~line 130):

    elif args.command == "creds":
        from vcollector.cli.creds import handle_creds
        return handle_creds(args)
'''


def main():
    parser = argparse.ArgumentParser(
        description='Run schema migration for credential testing support'
    )
    parser.add_argument(
        '--db-path',
        default=Path.home() / '.vcollector' / 'dcim.db',
        type=Path,
        help='Path to dcim.db (default: ~/.vcollector/dcim.db)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without executing'
    )
    parser.add_argument(
        '--show-code',
        action='store_true',
        help='Show code additions needed for integration'
    )

    args = parser.parse_args()

    if args.show_code:
        print("=" * 70)
        print("CODE ADDITIONS NEEDED")
        print("=" * 70)
        print()
        print("1. db_schema.py additions:")
        print("-" * 70)
        print(DB_SCHEMA_ADDITIONS)
        print()
        print("2. dcim_repo.py additions:")
        print("-" * 70)
        print(DCIM_REPO_ADDITIONS)
        print()
        print("3. main.py additions:")
        print("-" * 70)
        print(MAIN_PY_ADDITIONS)
        return 0

    success = run_migration(args.db_path, args.dry_run)
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())