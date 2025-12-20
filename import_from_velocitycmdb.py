#!/usr/bin/env python3
"""
Import devices from VelocityCMDB into VelocityCollector DCIM database.

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
from typing import Optional, Dict, List, Any

# Default paths
DEFAULT_CMDB_PATH = Path.home() / ".velocitycmdb" / "data" / "assets.db"
DEFAULT_DCIM_PATH = Path.home() / ".vcollector" / "dcim.db"


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    if not text:
        return ""
    # Lowercase, replace spaces/special chars with hyphens
    slug = text.lower().strip()
    slug = slug.replace(" ", "-").replace("_", "-")
    # Remove consecutive hyphens
    while "--" in slug:
        slug = slug.replace("--", "-")
    # Remove non-alphanumeric except hyphens
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    return slug.strip("-")


def netmiko_from_cmdb_driver(cmdb_driver: Optional[str]) -> Optional[str]:
    """Map CMDB netmiko_driver to our platform slug."""
    if not cmdb_driver:
        return None
    # CMDB stores netmiko driver names directly, which we can use
    return cmdb_driver


class CMDBImporter:
    """Import data from VelocityCMDB to VelocityCollector DCIM."""

    def __init__(self, cmdb_path: Path, dcim_path: Path, dry_run: bool = False):
        self.cmdb_path = cmdb_path
        self.dcim_path = dcim_path
        self.dry_run = dry_run

        self._cmdb_conn: Optional[sqlite3.Connection] = None
        self._dcim_conn: Optional[sqlite3.Connection] = None

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
            "roles_imported": 0,
            "roles_skipped": 0,
            "devices_imported": 0,
            "devices_skipped": 0,
            "devices_failed": 0,
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

    def close(self):
        """Close database connections."""
        if self._cmdb_conn:
            self._cmdb_conn.close()
        if self._dcim_conn:
            self._dcim_conn.close()

    def _now(self) -> str:
        """Get current timestamp."""
        return datetime.now().isoformat(sep=' ', timespec='seconds')

    def clear_dcim_data(self):
        """Clear existing DCIM data (except default platforms/roles)."""
        if self.dry_run:
            print("[DRY RUN] Would clear existing device data")
            return

        cursor = self.dcim.cursor()
        # Delete in order respecting foreign keys
        cursor.execute("DELETE FROM dcim_device")
        cursor.execute("DELETE FROM dcim_site")
        # Don't delete manufacturers/platforms/roles - keep defaults
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

            # Check if site already exists
            existing = self.dcim.execute(
                "SELECT id FROM dcim_site WHERE slug = ?", (slug,)
            ).fetchone()

            if existing:
                self.site_map[code] = existing["id"]
                # Also map lowercase version
                self.site_map[code.lower()] = existing["id"]
                self.site_map[code.upper()] = existing["id"]
                self.stats["sites_skipped"] += 1
                print(f"  [SKIP] Site '{name}' ({slug}) already exists")
                continue

            if self.dry_run:
                print(f"  [DRY RUN] Would import site: {name} ({slug})")
                # In dry run, still populate map for device import preview
                self.site_map[code] = -1  # Placeholder ID
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
            # Also map lowercase/uppercase versions
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

            # Check if manufacturer already exists
            existing = self.dcim.execute(
                "SELECT id FROM dcim_manufacturer WHERE slug = ? OR name = ?",
                (slug, name)
            ).fetchone()

            if existing:
                self.mfg_map[row["id"]] = existing["id"]
                self.stats["manufacturers_skipped"] += 1
                print(f"  [SKIP] Manufacturer '{name}' already exists")
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
        """Import device_types as platforms."""
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

            # Check if platform already exists
            existing = self.dcim.execute(
                "SELECT id FROM dcim_platform WHERE slug = ? OR netmiko_device_type = ?",
                (slug, netmiko)
            ).fetchone()

            if existing:
                self.platform_map[row["id"]] = existing["id"]
                self.stats["platforms_skipped"] += 1
                print(f"  [SKIP] Platform '{name}' already exists")
                continue

            if self.dry_run:
                print(f"  [DRY RUN] Would import platform: {name} ({netmiko})")
                self.stats["platforms_imported"] += 1
                continue

            # Try to find manufacturer by matching name patterns
            mfg_id = None
            name_lower = name.lower()
            if "cisco" in name_lower:
                mfg = self.dcim.execute(
                    "SELECT id FROM dcim_manufacturer WHERE slug = 'cisco'"
                ).fetchone()
                if mfg:
                    mfg_id = mfg["id"]
            elif "arista" in name_lower:
                mfg = self.dcim.execute(
                    "SELECT id FROM dcim_manufacturer WHERE slug = 'arista'"
                ).fetchone()
                if mfg:
                    mfg_id = mfg["id"]
            elif "juniper" in name_lower:
                mfg = self.dcim.execute(
                    "SELECT id FROM dcim_manufacturer WHERE slug = 'juniper'"
                ).fetchone()
                if mfg:
                    mfg_id = mfg["id"]

            cursor = self.dcim.execute(
                """INSERT INTO dcim_platform 
                   (name, slug, manufacturer_id, description, netmiko_device_type, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, slug, mfg_id, row["description"], netmiko, self._now(), self._now())
            )
            self.platform_map[row["id"]] = cursor.lastrowid
            self.stats["platforms_imported"] += 1
            print(f"  [OK] Imported platform: {name} ({netmiko})")

        if not self.dry_run:
            self.dcim.commit()

    def import_roles(self):
        """Import device_roles."""
        print("\n=== Importing Device Roles ===")

        rows = self.cmdb.execute(
            "SELECT id, name, description, is_infrastructure FROM device_roles"
        ).fetchall()

        # Color mapping for common roles
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

            # Check if role already exists
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
        """Import devices."""
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

        # Also need to handle mapping when sites were skipped (already existed)
        # Build site_map from existing sites if not already populated
        if not self.site_map:
            existing_sites = self.dcim.execute(
                "SELECT id, slug FROM dcim_site"
            ).fetchall()
            for site in existing_sites:
                # Map by slug (which we created from site code)
                self.site_map[site["slug"]] = site["id"]

        for row in rows:
            name = row["name"]
            site_code = row["site_code"]

            # Get site_id - try the code directly, as slug, and lowercase variants
            site_id = self.site_map.get(site_code)
            if not site_id:
                site_id = self.site_map.get(slugify(site_code))
            if not site_id and site_code:
                # Try lowercase
                site_id = self.site_map.get(site_code.lower())
            if not site_id and site_code:
                # Try looking up by slug in database directly (case-insensitive)
                site_row = self.dcim.execute(
                    "SELECT id FROM dcim_site WHERE LOWER(slug) = LOWER(?)",
                    (site_code,)
                ).fetchone()
                if site_row:
                    site_id = site_row["id"]
                    self.site_map[site_code] = site_id  # Cache it

            if not site_id:
                # Site doesn't exist in DCIM, skip device
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

            # Map foreign keys
            platform_id = self.platform_map.get(row["device_type_id"]) if row["device_type_id"] else None
            role_id = self.role_map.get(row["role_id"]) if row["role_id"] else None

            # Use management_ip or ipv4_address
            primary_ip = row["management_ip"] or row["ipv4_address"]

            if self.dry_run:
                print(f"  [DRY RUN] Would import device: {name} ({primary_ip})")
                self.stats["devices_imported"] += 1
                continue

            try:
                # Get primary serial from device_serials table if available
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
                print(f"  [OK] Imported device: {name} ({primary_ip or 'no IP'})")
            except Exception as e:
                self.stats["devices_failed"] += 1
                print(f"  [FAIL] Device '{name}': {e}")

        if not self.dry_run:
            self.dcim.commit()

    def run(self, site_filter: Optional[List[str]] = None, clear: bool = False):
        """Run the full import process."""
        print(f"CMDB Source: {self.cmdb_path}")
        print(f"DCIM Target: {self.dcim_path}")
        if self.dry_run:
            print("MODE: Dry run (no changes will be made)")
        if site_filter:
            print(f"Site filter: {', '.join(site_filter)}")

        if clear:
            self.clear_dcim_data()

        # Import in dependency order
        self.import_sites(site_filter)
        self.import_manufacturers()
        self.import_platforms()
        self.import_roles()
        self.import_devices(site_filter)

        # Print summary
        print("\n" + "=" * 50)
        print("IMPORT SUMMARY")
        print("=" * 50)
        print(f"Sites:         {self.stats['sites_imported']} imported, {self.stats['sites_skipped']} skipped")
        print(
            f"Manufacturers: {self.stats['manufacturers_imported']} imported, {self.stats['manufacturers_skipped']} skipped")
        print(f"Platforms:     {self.stats['platforms_imported']} imported, {self.stats['platforms_skipped']} skipped")
        print(f"Roles:         {self.stats['roles_imported']} imported, {self.stats['roles_skipped']} skipped")
        print(
            f"Devices:       {self.stats['devices_imported']} imported, {self.stats['devices_skipped']} skipped, {self.stats['devices_failed']} failed")

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

    # Parse site filter
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
        sys.exit(1)


if __name__ == "__main__":
    main()