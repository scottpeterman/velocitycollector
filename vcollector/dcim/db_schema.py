"""
VelocityCollector DCIM Database Schema

Matches NetBox DCIM naming conventions for easy import/export/sync.
Tables prefixed with 'dcim_' to mirror NetBox's app.model pattern.

Key design decisions:
- Use 'slug' fields for URL-friendly identifiers (NetBox convention)
- Store netbox_id for sync operations
- Keep schema minimal - only fields needed for collection operations
- Foreign keys use _id suffix (site_id, platform_id, etc.)
"""

import sqlite3
from pathlib import Path
from typing import Optional
from datetime import datetime


SCHEMA_VERSION = 2

SCHEMA_SQL = """
-- ============================================================================
-- DCIM SCHEMA - NetBox Compatible Subset
-- ============================================================================

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------------
-- Sites - Physical locations (buildings, data centers, POPs)
-- NetBox: /api/dcim/sites/
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dcim_site (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',  -- active, planned, staging, decommissioning, retired
    description TEXT,
    physical_address TEXT,
    facility TEXT,                          -- Data center/facility code
    time_zone TEXT,                         -- e.g., 'America/Denver'
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,               -- ID in NetBox for sync operations
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dcim_site_slug ON dcim_site(slug);
CREATE INDEX IF NOT EXISTS idx_dcim_site_status ON dcim_site(status);
CREATE INDEX IF NOT EXISTS idx_dcim_site_netbox_id ON dcim_site(netbox_id);

-- ----------------------------------------------------------------------------
-- Manufacturers - Hardware vendors (Cisco, Arista, Juniper, etc.)
-- NetBox: /api/dcim/manufacturers/
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dcim_manufacturer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dcim_manufacturer_slug ON dcim_manufacturer(slug);

-- ----------------------------------------------------------------------------
-- Platforms - Operating systems / software (IOS, NX-OS, EOS, Junos, etc.)
-- NetBox: /api/dcim/platforms/
-- 
-- This is critical for collection - determines SSH behavior, command syntax,
-- paging disable commands, and TextFSM template selection.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dcim_platform (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,              -- Display name: "Cisco IOS"
    slug TEXT NOT NULL UNIQUE,              -- URL-friendly: "cisco_ios"
    manufacturer_id INTEGER,                -- Optional - can be NULL
    description TEXT,
    
    -- Collection-specific fields (not in NetBox, but needed for SSH)
    netmiko_device_type TEXT,               -- e.g., 'cisco_ios', 'arista_eos'
    paging_disable_command TEXT,            -- e.g., 'terminal length 0'
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    
    FOREIGN KEY (manufacturer_id) REFERENCES dcim_manufacturer(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_dcim_platform_slug ON dcim_platform(slug);
CREATE INDEX IF NOT EXISTS idx_dcim_platform_manufacturer ON dcim_platform(manufacturer_id);

-- ----------------------------------------------------------------------------
-- Device Roles - Functional roles (router, switch, firewall, etc.)
-- NetBox: /api/dcim/device-roles/
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dcim_device_role (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    color TEXT DEFAULT '9e9e9e',            -- Hex color for UI (NetBox convention)
    description TEXT,
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dcim_device_role_slug ON dcim_device_role(slug);

-- ----------------------------------------------------------------------------
-- Devices - The main event
-- NetBox: /api/dcim/devices/
-- 
-- Note: NetBox requires device_type (make/model), but we simplify to just
-- platform since we're focused on collection, not rack elevation diagrams.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dcim_device (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    
    -- Required relationships
    site_id INTEGER NOT NULL,
    
    -- Optional relationships
    platform_id INTEGER,                    -- OS/software platform
    role_id INTEGER,                        -- Functional role
    
    -- Device attributes
    status TEXT NOT NULL DEFAULT 'active',  -- active, planned, staged, failed, offline, decommissioning, inventory
    serial_number TEXT,
    asset_tag TEXT UNIQUE,
    
    -- Network identity (critical for collection)
    primary_ip4 TEXT,                       -- IPv4 management address
    primary_ip6 TEXT,                       -- IPv6 management address
    oob_ip TEXT,                            -- Out-of-band management IP
    
    -- Collection settings
    credential_id INTEGER,                  -- FK to credentials table (NULL = use site/global default)
    ssh_port INTEGER DEFAULT 22,
    
    -- Credential testing (v2)
    credential_tested_at TEXT,              -- Last credential test timestamp
    credential_test_result TEXT DEFAULT 'untested',  -- 'untested', 'success', 'failed'
    
    -- Metadata
    description TEXT,
    comments TEXT,
    
    -- NetBox sync
    netbox_id INTEGER UNIQUE,
    
    -- Timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_collected_at TEXT,                 -- Last successful collection
    
    -- Constraints
    FOREIGN KEY (site_id) REFERENCES dcim_site(id) ON DELETE CASCADE,
    FOREIGN KEY (platform_id) REFERENCES dcim_platform(id) ON DELETE SET NULL,
    FOREIGN KEY (role_id) REFERENCES dcim_device_role(id) ON DELETE SET NULL,
    
    -- Name must be unique within site (NetBox behavior)
    UNIQUE (name, site_id)
);

CREATE INDEX IF NOT EXISTS idx_dcim_device_name ON dcim_device(name);
CREATE INDEX IF NOT EXISTS idx_dcim_device_site ON dcim_device(site_id);
CREATE INDEX IF NOT EXISTS idx_dcim_device_platform ON dcim_device(platform_id);
CREATE INDEX IF NOT EXISTS idx_dcim_device_role ON dcim_device(role_id);
CREATE INDEX IF NOT EXISTS idx_dcim_device_status ON dcim_device(status);
CREATE INDEX IF NOT EXISTS idx_dcim_device_primary_ip4 ON dcim_device(primary_ip4);
CREATE INDEX IF NOT EXISTS idx_dcim_device_netbox_id ON dcim_device(netbox_id);
CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_result ON dcim_device(credential_test_result);
CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_tested ON dcim_device(credential_tested_at);

-- ----------------------------------------------------------------------------
-- Useful views for common queries
-- ----------------------------------------------------------------------------

-- Full device view with joined names (avoids repeated JOINs in app code)
CREATE VIEW IF NOT EXISTS v_device_detail AS
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

-- Device count by site
CREATE VIEW IF NOT EXISTS v_site_device_counts AS
SELECT 
    s.id,
    s.name,
    s.slug,
    s.status,
    COUNT(d.id) AS device_count,
    COUNT(CASE WHEN d.status = 'active' THEN 1 END) AS active_devices
FROM dcim_site s
LEFT JOIN dcim_device d ON s.id = d.site_id
GROUP BY s.id;

-- Device count by platform
CREATE VIEW IF NOT EXISTS v_platform_device_counts AS
SELECT 
    p.id,
    p.name,
    p.slug,
    m.name AS manufacturer_name,
    COUNT(d.id) AS device_count
FROM dcim_platform p
LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
LEFT JOIN dcim_device d ON p.id = d.platform_id
GROUP BY p.id;

-- ----------------------------------------------------------------------------
-- Default data - Common platforms with netmiko mappings
-- ----------------------------------------------------------------------------

-- Note: These are inserted by init_default_data() function, not here,
-- to avoid duplicate inserts on schema reapplication.
"""

DEFAULT_MANUFACTURERS = [
    ("Cisco", "cisco", None),
    ("Arista", "arista", None),
    ("Juniper", "juniper", None),
    ("Palo Alto", "palo-alto", None),
    ("Fortinet", "fortinet", None),
    ("F5", "f5", None),
    ("HP", "hp", None),
    ("Dell", "dell", None),
]

DEFAULT_PLATFORMS = [
    # (name, slug, manufacturer_slug, netmiko_device_type, paging_disable)
    ("Cisco IOS", "cisco_ios", "cisco", "cisco_ios", "terminal length 0"),
    ("Cisco IOS-XE", "cisco_ios_xe", "cisco", "cisco_xe", "terminal length 0"),
    ("Cisco IOS-XR", "cisco_ios_xr", "cisco", "cisco_xr", "terminal length 0"),
    ("Cisco NX-OS", "cisco_nxos", "cisco", "cisco_nxos", "terminal length 0"),
    ("Cisco ASA", "cisco_asa", "cisco", "cisco_asa", "terminal pager 0"),
    ("Arista EOS", "arista_eos", "arista", "arista_eos", "terminal length 0"),
    ("Juniper Junos", "juniper_junos", "juniper", "juniper_junos", "set cli screen-length 0"),
    ("Palo Alto PAN-OS", "paloalto_panos", "palo-alto", "paloalto_panos", "set cli pager off"),
    ("Fortinet FortiOS", "fortinet_fortios", "fortinet", "fortinet", "config system console\nset output standard\nend"),
    ("F5 TMOS", "f5_tmos", "f5", "f5_tmsh", "modify cli preference pager disabled"),
    ("HP ProCurve", "hp_procurve", "hp", "hp_procurve", "no page"),
    ("HP Comware", "hp_comware", "hp", "hp_comware", "screen-length disable"),
    ("Dell OS10", "dell_os10", "dell", "dell_os10", "terminal length 0"),
]

DEFAULT_DEVICE_ROLES = [
    ("Router", "router", "3498db"),      # Blue
    ("Switch", "switch", "2ecc71"),      # Green
    ("Firewall", "firewall", "e74c3c"),  # Red
    ("Load Balancer", "load-balancer", "9b59b6"),  # Purple
    ("Wireless Controller", "wireless-controller", "f39c12"),  # Orange
    ("Access Point", "access-point", "1abc9c"),  # Teal
    ("Spine", "spine", "3498db"),
    ("Leaf", "leaf", "2ecc71"),
    ("Core", "core", "e67e22"),
    ("Distribution", "distribution", "f1c40f"),
    ("Access", "access", "27ae60"),
    ("Edge", "edge", "9b59b6"),
]


class DCIMDatabase:
    """Database manager for DCIM tables."""

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize DCIM database.

        Args:
            db_path: Path to SQLite database. If None, uses default location.
        """
        if db_path is None:
            db_path = Path.home() / ".vcollector" / "dcim.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Get database connection, creating if needed."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def init_schema(self, include_defaults: bool = True):
        """
        Initialize database schema.

        Args:
            include_defaults: If True, insert default platforms and roles.
        """
        cursor = self.conn.cursor()

        # Check if DCIM tables already exist (not just schema_version which may be shared)
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='dcim_site'
        """)

        if cursor.fetchone() is None:
            # Fresh database - apply schema
            cursor.executescript(SCHEMA_SQL)
            cursor.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,)
            )

            if include_defaults:
                self._init_default_data(cursor)

            self.conn.commit()
            return True

        # Check version for migrations
        cursor.execute("SELECT MAX(version) FROM schema_version")
        current_version = cursor.fetchone()[0] or 0

        if current_version < 2:
            # Run V2 migration - add credential testing columns
            self._run_migration_v2(cursor)
            cursor.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (2,)
            )
            self.conn.commit()

        return False

    def _run_migration_v2(self, cursor: sqlite3.Cursor):
        """
        Run V2 migration - add credential testing columns.

        Called automatically by init_schema() when upgrading from v1.
        """
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(dcim_device)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        # Add credential_tested_at if missing
        if 'credential_tested_at' not in existing_columns:
            cursor.execute(
                "ALTER TABLE dcim_device ADD COLUMN credential_tested_at TEXT"
            )

        # Add credential_test_result if missing
        if 'credential_test_result' not in existing_columns:
            cursor.execute(
                "ALTER TABLE dcim_device ADD COLUMN credential_test_result TEXT DEFAULT 'untested'"
            )

        # Add indexes (IF NOT EXISTS makes this safe to run multiple times)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_result ON dcim_device(credential_test_result)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_dcim_device_cred_tested ON dcim_device(credential_tested_at)"
        )

        # Recreate view to include new columns
        cursor.execute("DROP VIEW IF EXISTS v_device_detail")
        cursor.execute("""
            CREATE VIEW v_device_detail AS
            SELECT 
                d.id, d.name, d.status, d.primary_ip4, d.primary_ip6, d.oob_ip,
                d.ssh_port, d.serial_number, d.asset_tag, d.credential_id,
                d.credential_tested_at, d.credential_test_result,
                d.description, d.last_collected_at, d.netbox_id,
                d.created_at, d.updated_at,
                s.id AS site_id, s.name AS site_name, s.slug AS site_slug,
                p.id AS platform_id, p.name AS platform_name, p.slug AS platform_slug,
                p.netmiko_device_type, p.paging_disable_command,
                m.id AS manufacturer_id, m.name AS manufacturer_name, m.slug AS manufacturer_slug,
                r.id AS role_id, r.name AS role_name, r.slug AS role_slug
            FROM dcim_device d
            LEFT JOIN dcim_site s ON d.site_id = s.id
            LEFT JOIN dcim_platform p ON d.platform_id = p.id
            LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
            LEFT JOIN dcim_device_role r ON d.role_id = r.id
        """)

    def _init_default_data(self, cursor: sqlite3.Cursor):
        """Insert default manufacturers, platforms, and roles."""

        # Manufacturers
        cursor.executemany(
            """INSERT OR IGNORE INTO dcim_manufacturer (name, slug, description) 
               VALUES (?, ?, ?)""",
            DEFAULT_MANUFACTURERS
        )

        # Get manufacturer IDs for platform inserts
        cursor.execute("SELECT slug, id FROM dcim_manufacturer")
        mfg_map = {row[0]: row[1] for row in cursor.fetchall()}

        # Platforms
        for name, slug, mfg_slug, netmiko, paging in DEFAULT_PLATFORMS:
            mfg_id = mfg_map.get(mfg_slug)
            cursor.execute(
                """INSERT OR IGNORE INTO dcim_platform 
                   (name, slug, manufacturer_id, netmiko_device_type, paging_disable_command)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, slug, mfg_id, netmiko, paging)
            )

        # Device roles
        cursor.executemany(
            """INSERT OR IGNORE INTO dcim_device_role (name, slug, color) 
               VALUES (?, ?, ?)""",
            DEFAULT_DEVICE_ROLES
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def init_database(db_path: Optional[Path] = None) -> DCIMDatabase:
    """
    Initialize and return a DCIM database instance.

    Convenience function for quick setup.
    """
    db = DCIMDatabase(db_path)
    db.init_schema()
    return db


if __name__ == "__main__":
    # Quick test / initialization
    db = init_database()

    cursor = db.conn.cursor()

    print("=== DCIM Database Initialized ===\n")

    # Show manufacturers
    cursor.execute("SELECT name, slug FROM dcim_manufacturer ORDER BY name")
    print("Manufacturers:")
    for row in cursor.fetchall():
        print(f"  - {row['name']} ({row['slug']})")

    print()

    # Show platforms
    cursor.execute("""
        SELECT p.name, p.slug, p.netmiko_device_type, m.name as mfg
        FROM dcim_platform p
        LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
        ORDER BY m.name, p.name
    """)
    print("Platforms:")
    for row in cursor.fetchall():
        print(f"  - {row['name']} ({row['netmiko_device_type']}) - {row['mfg']}")

    print()

    # Show roles
    cursor.execute("SELECT name, slug, color FROM dcim_device_role ORDER BY name")
    print("Device Roles:")
    for row in cursor.fetchall():
        print(f"  - {row['name']} (#{row['color']})")

    db.close()