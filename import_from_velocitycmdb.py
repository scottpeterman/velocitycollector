#!/usr/bin/env python3
"""
Import devices from VelocityCMDB into VelocityCollector DCIM database.

Enhanced version with smarter platform/vendor matching logic from bulk_edit_dialog.

Maps VelocityCMDB schema to NetBox-compatible DCIM schema:
  - sites -> dcim_site
  - vendors -> dcim_manufacturer
  - device_types -> dcim_platform
  - device_roles -> dcim_device_role
  - devices -> dcim_device

Usage:
    # Import from default CMDB location (~/.velocitycmdb/data/assets.db)
    python import_from_cmdb.py

    # Import from specific CMDB database
    python import_from_cmdb.py --cmdb /path/to/assets.db

    # Import to specific DCIM database
    python import_from_cmdb.py --dcim /path/to/dcim.db

    # Dry run - show what would be imported
    python import_from_cmdb.py --dry-run

    # Import only specific site(s)
    python import_from_cmdb.py --sites usa,eng

    # Clear existing data before import
    python import_from_cmdb.py --clear
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# Default paths
DEFAULT_CMDB_PATH = Path.home() / ".velocitycmdb" / "data" / "assets.db"
DEFAULT_DCIM_PATH = Path.home() / ".vcollector" / "dcim.db"


# =============================================================================
# Platform/Vendor Matching Logic (ported from bulk_edit_dialog)
# =============================================================================

# Model prefix to vendor mapping - infer vendor from model number
MODEL_PREFIXES = {
    # Juniper
    'jnp': 'juniper',
    'ex': 'juniper',
    'qfx': 'juniper',
    'mx': 'juniper',
    'srx': 'juniper',
    'acx': 'juniper',
    'ptx': 'juniper',
    # Cisco
    'ws-c': 'cisco',
    'c9': 'cisco',
    'n9k': 'cisco',
    'n5k': 'cisco',
    'n7k': 'cisco',
    'n3k': 'cisco',
    'asr': 'cisco',
    'isr': 'cisco',
    'cat': 'cisco',
    'csr': 'cisco',
    'nexus': 'cisco',
    # Arista
    'dcs-': 'arista',
    'ccs-': 'arista',
    # Palo Alto
    'pa-': 'palo alto',
    'vm-': 'palo alto',
    # Fortinet
    'fg-': 'fortinet',
    'fgt': 'fortinet',
    'fortigate': 'fortinet',
    # F5
    'big-ip': 'f5',
    'bigip': 'f5',
    # Dell
    's4': 'dell',
    's5': 'dell',
    'n1': 'dell',
    'n2': 'dell',
    'n3': 'dell',
    # HP/Aruba
    'j9': 'hp',
    'jl': 'hp',
}

# Netmiko driver to platform slug mapping
NETMIKO_TO_PLATFORM = {
    'cisco_ios': 'cisco-ios',
    'cisco_xe': 'cisco-ios',
    'cisco_nxos': 'cisco-nxos',
    'cisco_xr': 'cisco-xr',
    'arista_eos': 'arista-eos',
    'juniper_junos': 'juniper-junos',
    'juniper': 'juniper-junos',
    'paloalto_panos': 'paloalto-panos',
    'fortinet': 'fortinet-fortios',
    'hp_procurve': 'hp-procurve',
    'hp_comware': 'hp-comware',
    'dell_os10': 'dell-os10',
    'dell_force10': 'dell-force10',
    'mikrotik_routeros': 'mikrotik-routeros',
    'ubiquiti_edgerouter': 'ubiquiti-edgeos',
    'linux': 'linux',
}

# Vendor name aliases for normalization
VENDOR_ALIASES = {
    'cisco': 'cisco',
    'cisco systems': 'cisco',
    'cisco systems, inc.': 'cisco',
    'arista': 'arista',
    'arista networks': 'arista',
    'arista networks, inc.': 'arista',
    'juniper': 'juniper',
    'juniper networks': 'juniper',
    'juniper networks, inc.': 'juniper',
    'palo alto': 'palo alto',
    'palo alto networks': 'palo alto',
    'paloalto': 'palo alto',
    'pan': 'palo alto',
    'fortinet': 'fortinet',
    'fortinet, inc.': 'fortinet',
    'fortigate': 'fortinet',
    'f5': 'f5',
    'f5 networks': 'f5',
    'f5 networks, inc.': 'f5',
    'dell': 'dell',
    'dell emc': 'dell',
    'dell inc.': 'dell',
    'hp': 'hp',
    'hpe': 'hp',
    'hewlett packard': 'hp',
    'hewlett-packard': 'hp',
    'hewlett packard enterprise': 'hp',
    'aruba': 'aruba',
    'aruba networks': 'aruba',
    'mikrotik': 'mikrotik',
    'ubiquiti': 'ubiquiti',
    'ubiquiti networks': 'ubiquiti',
    'ubiquiti inc.': 'ubiquiti',
}


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    if not text:
        return ""
    slug = text.lower().strip()
    slug = slug.replace(" ", "-").replace("_", "-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    return slug.strip("-")


def normalize_vendor(vendor: str) -> str:
    """Normalize vendor name for matching."""
    if not vendor:
        return ""
    return VENDOR_ALIASES.get(vendor.lower().strip(), vendor.lower().strip())


def vendor_from_model(model: str) -> Optional[str]:
    """Infer vendor from model number prefix."""
    if not model:
        return None
    model_lower = model.lower()
    for prefix, vendor in MODEL_PREFIXES.items():
        if model_lower.startswith(prefix):
            return vendor
    return None


class PlatformMatcher:
    """Match vendor/model/netmiko to existing DCIM platforms."""

    def __init__(self, dcim_conn: sqlite3.Connection):
        self.dcim = dcim_conn
        self._platforms = []
        self._by_name = {}
        self._by_slug = {}
        self._by_netmiko = {}
        self._by_vendor = {}
        self._load_platforms()

    def _load_platforms(self):
        """Load and index all platforms from DCIM."""
        cursor = self.dcim.execute("""
            SELECT p.id, p.name, p.slug, p.netmiko_device_type, 
                   p.manufacturer_id, m.name as manufacturer_name, m.slug as manufacturer_slug
            FROM dcim_platform p
            LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
        """)

        for row in cursor.fetchall():
            platform = {
                'id': row['id'],
                'name': row['name'],
                'slug': row['slug'],
                'netmiko': row['netmiko_device_type'],
                'mfg_id': row['manufacturer_id'],
                'mfg_name': row['manufacturer_name'],
                'mfg_slug': row['manufacturer_slug'],
            }
            self._platforms.append(platform)

            # Index by name
            name_lower = row['name'].lower()
            self._by_name[name_lower] = platform

            # Index by slug
            if row['slug']:
                self._by_slug[row['slug'].lower()] = platform

            # Index by netmiko driver
            if row['netmiko_device_type']:
                self._by_netmiko[row['netmiko_device_type'].lower()] = platform

            # Index by manufacturer
            if row['manufacturer_slug']:
                vendor_key = row['manufacturer_slug'].lower()
                if vendor_key not in self._by_vendor:
                    self._by_vendor[vendor_key] = []
                self._by_vendor[vendor_key].append(platform)

    def match(self, vendor: Optional[str] = None, model: Optional[str] = None,
              netmiko_driver: Optional[str] = None,
              device_type_name: Optional[str] = None) -> Optional[Tuple[int, str]]:
        """
        Try to match to a platform using multiple strategies.
        Returns (platform_id, platform_name) or None.
        """
        # Strategy 1: Direct netmiko driver match (most reliable)
        if netmiko_driver:
            netmiko_lower = netmiko_driver.lower()

            # Direct match on netmiko_device_type column
            if netmiko_lower in self._by_netmiko:
                p = self._by_netmiko[netmiko_lower]
                return (p['id'], p['name'])

            # Try mapped slug from NETMIKO_TO_PLATFORM
            if netmiko_lower in NETMIKO_TO_PLATFORM:
                slug = NETMIKO_TO_PLATFORM[netmiko_lower]
                if slug in self._by_slug:
                    p = self._by_slug[slug]
                    return (p['id'], p['name'])
                # Also try with underscores converted to hyphens
                slug_alt = netmiko_lower.replace('_', '-')
                if slug_alt in self._by_slug:
                    p = self._by_slug[slug_alt]
                    return (p['id'], p['name'])

        # Strategy 2: Match by device_type name from CMDB
        if device_type_name:
            name_lower = device_type_name.lower()
            if name_lower in self._by_name:
                p = self._by_name[name_lower]
                return (p['id'], p['name'])
            # Try as slug
            slug = slugify(device_type_name)
            if slug in self._by_slug:
                p = self._by_slug[slug]
                return (p['id'], p['name'])

        # Strategy 3: Vendor + model matching
        vendor_normalized = normalize_vendor(vendor) if vendor else None

        # If no vendor, try to infer from model
        if not vendor_normalized and model:
            vendor_normalized = vendor_from_model(model)

        if vendor_normalized and vendor_normalized in self._by_vendor:
            platforms = self._by_vendor[vendor_normalized]

            # If we have a model, try to find best match
            if model:
                model_lower = model.lower()
                for p in platforms:
                    if model_lower in p['name'].lower() or p['name'].lower() in model_lower:
                        return (p['id'], p['name'])

            # Return first platform for this vendor as generic fallback
            if platforms:
                p = platforms[0]
                return (p['id'], p['name'])

        # Strategy 4: Partial model match across all platforms
        if model:
            model_lower = model.lower()
            for name, p in self._by_name.items():
                if model_lower in name or name in model_lower:
                    return (p['id'], p['name'])

        return None


class CMDBImporter:
    """Import data from VelocityCMDB to VelocityCollector DCIM."""

    def __init__(self, cmdb_path: Path, dcim_path: Path, dry_run: bool = False):
        self.cmdb_path = cmdb_path
        self.dcim_path = dcim_path
        self.dry_run = dry_run

        self._cmdb_conn: Optional[sqlite3.Connection] = None
        self._dcim_conn: Optional[sqlite3.Connection] = None
        self._platform_matcher: Optional[PlatformMatcher] = None

        # Cache CMDB lookup data
        self._cmdb_vendors: Dict[int, Dict] = {}  # vendor_id -> {name, short_name}
        self._cmdb_device_types: Dict[int, Dict] = {}  # device_type_id -> {name, netmiko_driver, ...}

        # Track mappings from CMDB IDs to DCIM IDs
        self.site_map: Dict[str, int] = {}  # cmdb code -> dcim id
        self.mfg_map: Dict[int, int] = {}  # cmdb vendor_id -> dcim manufacturer_id
        self.platform_map: Dict[int, int] = {}  # cmdb device_type_id -> dcim platform_id
        self.role_map: Dict[int, int] = {}  # cmdb role_id -> dcim role_id

        # Stats
        self.stats = {
            "sites_imported": 0,
            "sites_skipped": 0,
            "manufacturers_imported": 0,
            "manufacturers_skipped": 0,
            "platforms_imported": 0,
            "platforms_skipped": 0,
            "platforms_matched": 0,
            "roles_imported": 0,
            "roles_skipped": 0,
            "devices_imported": 0,
            "devices_skipped": 0,
            "devices_failed": 0,
            "devices_platform_matched": 0,
        }

    @property
    def cmdb(self) -> sqlite3.Connection:
        """Get CMDB database connection."""
        if self._cmdb_conn is None:
            if not self.cmdb_path.exists():
                raise FileNotFoundError(f"CMDB database not found: {self.cmdb_path}")
            self._cmdb_conn = sqlite3.connect(str(self.cmdb_path))
            self._cmdb_conn.row_factory = sqlite3.Row
        return self._cmdb_conn

    @property
    def dcim(self) -> sqlite3.Connection:
        """Get DCIM database connection."""
        if self._dcim_conn is None:
            if not self.dcim_path.exists():
                raise FileNotFoundError(
                    f"DCIM database not found: {self.dcim_path}\n"
                    f"Run 'python -m vcollector.dcim.db_schema' to initialize it first."
                )
            self._dcim_conn = sqlite3.connect(str(self.dcim_path))
            self._dcim_conn.row_factory = sqlite3.Row
            self._dcim_conn.execute("PRAGMA foreign_keys = ON")
        return self._dcim_conn

    @property
    def platform_matcher(self) -> PlatformMatcher:
        """Get platform matcher (lazy init after platforms imported)."""
        if self._platform_matcher is None:
            self._platform_matcher = PlatformMatcher(self.dcim)
        return self._platform_matcher

    def _reload_platform_matcher(self):
        """Reload platform matcher after importing new platforms."""
        self._platform_matcher = PlatformMatcher(self.dcim)

    def close(self):
        """Close database connections."""
        if self._cmdb_conn:
            self._cmdb_conn.close()
        if self._dcim_conn:
            self._dcim_conn.close()

    def _now(self) -> str:
        """Get current timestamp."""
        return datetime.now().isoformat(sep=' ', timespec='seconds')

    def _load_cmdb_lookups(self):
        """Pre-load CMDB vendor and device_type data for efficient lookups."""
        # Load vendors
        for row in self.cmdb.execute("SELECT id, name, short_name FROM vendors").fetchall():
            self._cmdb_vendors[row['id']] = {
                'name': row['name'],
                'short_name': row['short_name'],
            }

        # Load device_types
        for row in self.cmdb.execute("""
            SELECT id, name, netmiko_driver, napalm_driver 
            FROM device_types
        """).fetchall():
            self._cmdb_device_types[row['id']] = {
                'name': row['name'],
                'netmiko_driver': row['netmiko_driver'],
                'napalm_driver': row['napalm_driver'],
            }

        print(f"Loaded {len(self._cmdb_vendors)} vendors, {len(self._cmdb_device_types)} device_types from CMDB")

    def clear_dcim_data(self):
        """Clear existing DCIM data (except default platforms/roles)."""
        if self.dry_run:
            print("[DRY RUN] Would clear existing device data")
            return

        cursor = self.dcim.cursor()
        cursor.execute("DELETE FROM dcim_device")
        cursor.execute("DELETE FROM dcim_site")
        self.dcim.commit()
        print("Cleared existing device and site data")

    def import_sites(self, site_filter: Optional[List[str]] = None):
        """Import sites from CMDB."""
        print("\n=== Importing Sites ===")

        query = "SELECT code, name, description FROM sites"
        if site_filter:
            placeholders = ",".join("?" * len(site_filter))
            query += f" WHERE code IN ({placeholders})"
            rows = self.cmdb.execute(query, site_filter).fetchall()
        else:
            rows = self.cmdb.execute(query).fetchall()

        for row in rows:
            code = row["code"]
            name = row["name"] or code
            slug = slugify(code)

            existing = self.dcim.execute(
                "SELECT id FROM dcim_site WHERE slug = ?", (slug,)
            ).fetchone()

            if existing:
                self.site_map[code] = existing["id"]
                self.site_map[code.lower()] = existing["id"]
                self.site_map[code.upper()] = existing["id"]
                self.stats["sites_skipped"] += 1
                print(f"  [SKIP] Site '{name}' ({slug}) already exists")
                continue

            if self.dry_run:
                print(f"  [DRY RUN] Would import site: {name} ({slug})")
                self.site_map[code] = -1
                self.site_map[code.lower()] = -1
                self.site_map[code.upper()] = -1
                self.stats["sites_imported"] += 1
                continue

            cursor = self.dcim.execute(
                """INSERT INTO dcim_site (name, slug, status, description, created_at, updated_at)
                   VALUES (?, ?, 'active', ?, ?, ?)""",
                (name, slug, row["description"], self._now(), self._now())
            )
            self.site_map[code] = cursor.lastrowid
            self.site_map[code.lower()] = cursor.lastrowid
            self.site_map[code.upper()] = cursor.lastrowid
            self.stats["sites_imported"] += 1
            print(f"  [OK] Imported site: {name} ({slug})")

        if not self.dry_run:
            self.dcim.commit()

    def import_manufacturers(self):
        """Import vendors as manufacturers."""
        print("\n=== Importing Manufacturers (from vendors) ===")

        rows = self.cmdb.execute(
            "SELECT id, name, short_name, description FROM vendors"
        ).fetchall()

        for row in rows:
            name = row["name"]
            slug = slugify(row["short_name"] or name)

            # Check for existing - also try normalized name
            normalized = normalize_vendor(name)
            existing = self.dcim.execute(
                """SELECT id FROM dcim_manufacturer 
                   WHERE slug = ? OR LOWER(name) = ? OR slug = ?""",
                (slug, normalized, normalized)
            ).fetchone()

            if existing:
                self.mfg_map[row["id"]] = existing["id"]
                self.stats["manufacturers_skipped"] += 1
                print(f"  [SKIP] Manufacturer '{name}' already exists (id={existing['id']})")
                continue

            if self.dry_run:
                print(f"  [DRY RUN] Would import manufacturer: {name} ({slug})")
                self.stats["manufacturers_imported"] += 1
                continue

            cursor = self.dcim.execute(
                """INSERT INTO dcim_manufacturer (name, slug, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, slug, row["description"], self._now(), self._now())
            )
            self.mfg_map[row["id"]] = cursor.lastrowid
            self.stats["manufacturers_imported"] += 1
            print(f"  [OK] Imported manufacturer: {name} ({slug})")

        if not self.dry_run:
            self.dcim.commit()

    def import_platforms(self):
        """Import device_types as platforms with smart matching."""
        print("\n=== Importing Platforms (from device_types) ===")

        rows = self.cmdb.execute(
            """SELECT id, name, description, netmiko_driver, napalm_driver, 
                      transport, default_port, requires_enable
               FROM device_types"""
        ).fetchall()

        for row in rows:
            name = row["name"]
            slug = slugify(name)
            netmiko = row["netmiko_driver"]

            # Smart matching: try multiple strategies
            existing = None

            # 1. Exact slug match
            existing = self.dcim.execute(
                "SELECT id, name FROM dcim_platform WHERE slug = ?", (slug,)
            ).fetchone()

            # 2. Netmiko driver match
            if not existing and netmiko:
                existing = self.dcim.execute(
                    "SELECT id, name FROM dcim_platform WHERE netmiko_device_type = ?",
                    (netmiko,)
                ).fetchone()

            # 3. Try NETMIKO_TO_PLATFORM mapped slug
            if not existing and netmiko:
                mapped_slug = NETMIKO_TO_PLATFORM.get(netmiko.lower())
                if mapped_slug:
                    existing = self.dcim.execute(
                        "SELECT id, name FROM dcim_platform WHERE slug = ?",
                        (mapped_slug,)
                    ).fetchone()

            # 4. Try name-based match
            if not existing:
                existing = self.dcim.execute(
                    "SELECT id, name FROM dcim_platform WHERE LOWER(name) = LOWER(?)",
                    (name,)
                ).fetchone()

            if existing:
                self.platform_map[row["id"]] = existing["id"]
                self.stats["platforms_matched"] += 1
                print(f"  [MATCH] '{name}' -> existing '{existing['name']}' (id={existing['id']})")
                continue

            if self.dry_run:
                print(f"  [DRY RUN] Would import platform: {name} ({netmiko})")
                self.stats["platforms_imported"] += 1
                continue

            # Find manufacturer by inferring from platform name or netmiko driver
            mfg_id = self._find_manufacturer_for_platform(name, netmiko)

            cursor = self.dcim.execute(
                """INSERT INTO dcim_platform 
                   (name, slug, manufacturer_id, description, netmiko_device_type, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, slug, mfg_id, row["description"], netmiko, self._now(), self._now())
            )
            self.platform_map[row["id"]] = cursor.lastrowid
            self.stats["platforms_imported"] += 1
            mfg_note = f" (mfg_id={mfg_id})" if mfg_id else ""
            print(f"  [OK] Imported platform: {name} ({netmiko}){mfg_note}")

        if not self.dry_run:
            self.dcim.commit()
            # Reload matcher with new platforms
            self._reload_platform_matcher()

    def _find_manufacturer_for_platform(self, name: str, netmiko: Optional[str]) -> Optional[int]:
        """Find manufacturer ID for a platform based on name or netmiko driver."""
        # Build list of vendor hints
        hints = []

        name_lower = name.lower()
        for vendor in ['cisco', 'arista', 'juniper', 'palo alto', 'fortinet', 'f5',
                       'dell', 'hp', 'mikrotik', 'ubiquiti']:
            if vendor in name_lower:
                hints.append(vendor)
                break

        # Check netmiko driver prefix
        if netmiko:
            netmiko_lower = netmiko.lower()
            if netmiko_lower.startswith('cisco'):
                hints.append('cisco')
            elif netmiko_lower.startswith('arista'):
                hints.append('arista')
            elif netmiko_lower.startswith('juniper'):
                hints.append('juniper')
            elif netmiko_lower.startswith('paloalto'):
                hints.append('palo alto')
            elif netmiko_lower.startswith('fortinet'):
                hints.append('fortinet')
            elif netmiko_lower.startswith('hp'):
                hints.append('hp')

        for hint in hints:
            normalized = normalize_vendor(hint)
            mfg = self.dcim.execute(
                """SELECT id FROM dcim_manufacturer 
                   WHERE slug = ? OR LOWER(name) LIKE ?""",
                (normalized, f"%{normalized}%")
            ).fetchone()
            if mfg:
                return mfg["id"]

        return None

    def import_roles(self):
        """Import device_roles."""
        print("\n=== Importing Device Roles ===")

        rows = self.cmdb.execute(
            "SELECT id, name, description, is_infrastructure FROM device_roles"
        ).fetchall()

        role_colors = {
            "router": "3498db",
            "switch": "2ecc71",
            "firewall": "e74c3c",
            "spine": "3498db",
            "leaf": "2ecc71",
            "core": "e67e22",
            "distribution": "f1c40f",
            "access": "27ae60",
        }

        for row in rows:
            name = row["name"]
            slug = slugify(name)
            color = role_colors.get(slug, "9e9e9e")

            existing = self.dcim.execute(
                "SELECT id FROM dcim_device_role WHERE slug = ?", (slug,)
            ).fetchone()

            if existing:
                self.role_map[row["id"]] = existing["id"]
                self.stats["roles_skipped"] += 1
                print(f"  [SKIP] Role '{name}' already exists")
                continue

            if self.dry_run:
                print(f"  [DRY RUN] Would import role: {name} ({slug})")
                self.stats["roles_imported"] += 1
                continue

            cursor = self.dcim.execute(
                """INSERT INTO dcim_device_role (name, slug, color, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, slug, color, row["description"], self._now(), self._now())
            )
            self.role_map[row["id"]] = cursor.lastrowid
            self.stats["roles_imported"] += 1
            print(f"  [OK] Imported role: {name} ({slug})")

        if not self.dry_run:
            self.dcim.commit()

    def import_devices(self, site_filter: Optional[List[str]] = None):
        """Import devices with smart platform matching."""
        print("\n=== Importing Devices ===")

        query = """
            SELECT d.id, d.name, d.normalized_name, d.site_code, 
                   d.vendor_id, d.device_type_id, d.model, d.os_version,
                   d.management_ip, d.ipv4_address, d.role_id,
                   d.processor_id, d.timestamp
            FROM devices d
        """

        if site_filter:
            placeholders = ",".join("?" * len(site_filter))
            query += f" WHERE d.site_code IN ({placeholders})"
            rows = self.cmdb.execute(query, site_filter).fetchall()
        else:
            rows = self.cmdb.execute(query).fetchall()

        # Ensure site_map is populated from existing sites
        if not self.site_map:
            existing_sites = self.dcim.execute(
                "SELECT id, slug FROM dcim_site"
            ).fetchall()
            for site in existing_sites:
                self.site_map[site["slug"]] = site["id"]

        for row in rows:
            name = row["name"]
            site_code = row["site_code"]

            # Resolve site_id
            site_id = self._resolve_site_id(site_code)
            if not site_id:
                self.stats["devices_skipped"] += 1
                print(f"  [SKIP] Device '{name}' - site '{site_code}' not found")
                continue

            # Check if device already exists
            existing = self.dcim.execute(
                "SELECT id FROM dcim_device WHERE name = ? AND site_id = ?",
                (name, site_id)
            ).fetchone()

            if existing:
                self.stats["devices_skipped"] += 1
                print(f"  [SKIP] Device '{name}' already exists at site")
                continue

            # Resolve platform_id using smart matching
            platform_id, platform_match_type = self._resolve_platform_id(row)
            role_id = self.role_map.get(row["role_id"]) if row["role_id"] else None
            primary_ip = row["management_ip"] or row["ipv4_address"]

            if self.dry_run:
                platform_note = f" [{platform_match_type}]" if platform_match_type else ""
                print(f"  [DRY RUN] Would import device: {name} ({primary_ip}){platform_note}")
                self.stats["devices_imported"] += 1
                if platform_match_type:
                    self.stats["devices_platform_matched"] += 1
                continue

            try:
                # Get primary serial
                serial_row = self.cmdb.execute(
                    "SELECT serial FROM device_serials WHERE device_id = ? AND is_primary = 1",
                    (row["id"],)
                ).fetchone()
                serial = serial_row["serial"] if serial_row else row["processor_id"]

                self.dcim.execute(
                    """INSERT INTO dcim_device 
                       (name, site_id, platform_id, role_id, status, 
                        primary_ip4, serial_number, description, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
                    (name, site_id, platform_id, role_id, primary_ip,
                     serial, row["model"], self._now(), self._now())
                )
                self.stats["devices_imported"] += 1
                if platform_match_type:
                    self.stats["devices_platform_matched"] += 1

                platform_note = f" [platform: {platform_match_type}]" if platform_match_type else " [no platform]"
                print(f"  [OK] Imported device: {name} ({primary_ip or 'no IP'}){platform_note}")
            except Exception as e:
                self.stats["devices_failed"] += 1
                print(f"  [FAIL] Device '{name}': {e}")

        if not self.dry_run:
            self.dcim.commit()

    def _resolve_site_id(self, site_code: str) -> Optional[int]:
        """Resolve site code to DCIM site_id."""
        if not site_code:
            return None

        # Try direct lookup
        site_id = self.site_map.get(site_code)
        if site_id:
            return site_id

        # Try as slug
        site_id = self.site_map.get(slugify(site_code))
        if site_id:
            return site_id

        # Try lowercase/uppercase
        site_id = self.site_map.get(site_code.lower())
        if site_id:
            return site_id

        # Direct database lookup
        site_row = self.dcim.execute(
            "SELECT id FROM dcim_site WHERE LOWER(slug) = LOWER(?)",
            (site_code,)
        ).fetchone()
        if site_row:
            site_id = site_row["id"]
            self.site_map[site_code] = site_id
            return site_id

        return None

    def _resolve_platform_id(self, device_row: sqlite3.Row) -> Tuple[Optional[int], Optional[str]]:
        """
        Resolve platform_id for a device using smart matching.
        Returns (platform_id, match_type) where match_type describes how it was matched.
        """
        device_type_id = device_row["device_type_id"]
        vendor_id = device_row["vendor_id"]
        model = device_row["model"]

        # 1. Try direct mapping from platform import
        if device_type_id and device_type_id in self.platform_map:
            return (self.platform_map[device_type_id], "device_type_map")

        # 2. Use platform matcher for smart matching
        # Get vendor name from CMDB
        vendor_name = None
        if vendor_id and vendor_id in self._cmdb_vendors:
            vendor_name = self._cmdb_vendors[vendor_id].get('name')

        # Get device_type info from CMDB
        device_type_name = None
        netmiko_driver = None
        if device_type_id and device_type_id in self._cmdb_device_types:
            dt = self._cmdb_device_types[device_type_id]
            device_type_name = dt.get('name')
            netmiko_driver = dt.get('netmiko_driver')

        match = self.platform_matcher.match(
            vendor=vendor_name,
            model=model,
            netmiko_driver=netmiko_driver,
            device_type_name=device_type_name
        )

        if match:
            return (match[0], f"smart:{match[1]}")

        return (None, None)

    def run(self, site_filter: Optional[List[str]] = None, clear: bool = False):
        """Run the full import process."""
        print(f"CMDB Source: {self.cmdb_path}")
        print(f"DCIM Target: {self.dcim_path}")
        if self.dry_run:
            print("MODE: Dry run (no changes will be made)")
        if site_filter:
            print(f"Site filter: {', '.join(site_filter)}")

        # Pre-load CMDB lookup tables
        self._load_cmdb_lookups()

        if clear:
            self.clear_dcim_data()

        # Import in dependency order
        self.import_sites(site_filter)
        self.import_manufacturers()
        self.import_platforms()
        self.import_roles()
        self.import_devices(site_filter)

        # Print summary
        print("\n" + "=" * 60)
        print("IMPORT SUMMARY")
        print("=" * 60)
        print(f"Sites:         {self.stats['sites_imported']} imported, {self.stats['sites_skipped']} skipped")
        print(f"Manufacturers: {self.stats['manufacturers_imported']} imported, {self.stats['manufacturers_skipped']} skipped")
        print(f"Platforms:     {self.stats['platforms_imported']} imported, {self.stats['platforms_matched']} matched existing, {self.stats['platforms_skipped']} skipped")
        print(f"Roles:         {self.stats['roles_imported']} imported, {self.stats['roles_skipped']} skipped")
        print(f"Devices:       {self.stats['devices_imported']} imported, {self.stats['devices_skipped']} skipped, {self.stats['devices_failed']} failed")
        print(f"               {self.stats['devices_platform_matched']} devices matched to platforms")

        self.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import devices from VelocityCMDB into VelocityCollector DCIM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Import all from default locations
  %(prog)s --dry-run                # Preview what would be imported
  %(prog)s --sites usa,eng          # Import only specific sites
  %(prog)s --clear                  # Clear existing data first
  %(prog)s --cmdb /path/to/db       # Use specific CMDB database
        """
    )

    parser.add_argument(
        "--cmdb",
        type=Path,
        default=DEFAULT_CMDB_PATH,
        help=f"Path to VelocityCMDB database (default: {DEFAULT_CMDB_PATH})"
    )

    parser.add_argument(
        "--dcim",
        type=Path,
        default=DEFAULT_DCIM_PATH,
        help=f"Path to DCIM database (default: {DEFAULT_DCIM_PATH})"
    )

    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be imported without making changes"
    )

    parser.add_argument(
        "--sites", "-s",
        type=str,
        help="Comma-separated list of site codes to import (default: all)"
    )

    parser.add_argument(
        "--clear", "-c",
        action="store_true",
        help="Clear existing device/site data before import"
    )

    args = parser.parse_args()

    site_filter = None
    if args.sites:
        site_filter = [s.strip() for s in args.sites.split(",")]

    try:
        importer = CMDBImporter(
            cmdb_path=args.cmdb,
            dcim_path=args.dcim,
            dry_run=args.dry_run
        )
        importer.run(site_filter=site_filter, clear=args.clear)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()