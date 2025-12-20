"""
VelocityCollector DCIM Repository

Data access layer for DCIM objects. Provides CRUD operations and
query methods for sites, platforms, and devices.

Usage:
    from vcollector.db.dcim_repo import DCIMRepository

    repo = DCIMRepository()

    # Create a site
    site_id = repo.create_site(name="US-East", slug="us-east")

    # Get devices by site
    devices = repo.get_devices(site_slug="us-east")

    # Get device with full details
    device = repo.get_device_detail(device_id=42)
"""

import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from enum import Enum


class DeviceStatus(str, Enum):
    """Device operational status - matches NetBox choices."""
    ACTIVE = "active"
    PLANNED = "planned"
    STAGED = "staged"
    FAILED = "failed"
    OFFLINE = "offline"
    DECOMMISSIONING = "decommissioning"
    INVENTORY = "inventory"


class SiteStatus(str, Enum):
    """Site operational status - matches NetBox choices."""
    ACTIVE = "active"
    PLANNED = "planned"
    STAGING = "staging"
    DECOMMISSIONING = "decommissioning"
    RETIRED = "retired"


# =============================================================================
# Data Classes - Match database schema
# =============================================================================

@dataclass
class Site:
    """Site model."""
    id: Optional[int] = None
    name: str = ""
    slug: str = ""
    status: str = "active"
    description: Optional[str] = None
    physical_address: Optional[str] = None
    facility: Optional[str] = None
    time_zone: Optional[str] = None
    netbox_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Manufacturer:
    """Manufacturer model."""
    id: Optional[int] = None
    name: str = ""
    slug: str = ""
    description: Optional[str] = None
    netbox_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Platform:
    """Platform model with collection-specific fields."""
    id: Optional[int] = None
    name: str = ""
    slug: str = ""
    manufacturer_id: Optional[int] = None
    description: Optional[str] = None
    netmiko_device_type: Optional[str] = None
    paging_disable_command: Optional[str] = None
    netbox_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Joined fields (from queries)
    manufacturer_name: Optional[str] = None
    manufacturer_slug: Optional[str] = None


@dataclass
class DeviceRole:
    """Device role model."""
    id: Optional[int] = None
    name: str = ""
    slug: str = ""
    color: str = "9e9e9e"
    description: Optional[str] = None
    netbox_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Device:
    """Device model."""
    id: Optional[int] = None
    name: str = ""
    site_id: Optional[int] = None
    platform_id: Optional[int] = None
    role_id: Optional[int] = None
    status: str = "active"
    serial_number: Optional[str] = None
    asset_tag: Optional[str] = None
    primary_ip4: Optional[str] = None
    primary_ip6: Optional[str] = None
    oob_ip: Optional[str] = None
    credential_id: Optional[int] = None
    credential_tested_at: Optional[str] = None      # v2: Last credential test timestamp
    credential_test_result: Optional[str] = None    # v2: 'untested', 'success', 'failed'
    ssh_port: int = 22
    description: Optional[str] = None
    comments: Optional[str] = None
    netbox_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_collected_at: Optional[str] = None
    # Joined fields (from v_device_detail view)
    site_name: Optional[str] = None
    site_slug: Optional[str] = None
    platform_name: Optional[str] = None
    platform_slug: Optional[str] = None
    netmiko_device_type: Optional[str] = None
    paging_disable_command: Optional[str] = None
    manufacturer_id: Optional[int] = None
    manufacturer_name: Optional[str] = None
    manufacturer_slug: Optional[str] = None
    role_name: Optional[str] = None
    role_slug: Optional[str] = None


# =============================================================================
# Repository
# =============================================================================

class DCIMRepository:
    """
    Data access layer for DCIM objects.

    Provides CRUD operations and query methods for sites, manufacturers,
    platforms, device roles, and devices.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize repository.

        Args:
            db_path: Path to SQLite database. If None, uses default location.
        """
        if db_path is None:
            db_path = Path.home() / ".vcollector" / "dcim.db"

        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Get database connection."""
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

    def _row_to_dataclass(self, row: sqlite3.Row, cls):
        """Convert a sqlite Row to a dataclass instance."""
        if row is None:
            return None
        data = {k: row[k] for k in row.keys() if k in cls.__dataclass_fields__}
        return cls(**data)

    def _now(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.now().isoformat(sep=' ', timespec='seconds')

    # =========================================================================
    # Sites
    # =========================================================================

    def create_site(self, name: str, slug: str, **kwargs) -> int:
        """Create a new site."""
        fields = ['name', 'slug', 'created_at', 'updated_at']
        values = [name, slug, self._now(), self._now()]

        for key in ['status', 'description', 'physical_address', 'facility',
                    'time_zone', 'netbox_id']:
            if key in kwargs and kwargs[key] is not None:
                fields.append(key)
                values.append(kwargs[key])

        placeholders = ', '.join(['?' for _ in fields])
        field_names = ', '.join(fields)

        cursor = self.conn.execute(
            f"INSERT INTO dcim_site ({field_names}) VALUES ({placeholders})",
            values
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_site(self, site_id: Optional[int] = None,
                 slug: Optional[str] = None,
                 netbox_id: Optional[int] = None) -> Optional[Site]:
        """Get a site by ID, slug, or NetBox ID."""
        if site_id:
            row = self.conn.execute(
                "SELECT * FROM dcim_site WHERE id = ?", (site_id,)
            ).fetchone()
        elif slug:
            row = self.conn.execute(
                "SELECT * FROM dcim_site WHERE slug = ?", (slug,)
            ).fetchone()
        elif netbox_id:
            row = self.conn.execute(
                "SELECT * FROM dcim_site WHERE netbox_id = ?", (netbox_id,)
            ).fetchone()
        else:
            return None

        return self._row_to_dataclass(row, Site)

    def get_sites(self, status: Optional[str] = None) -> List[Site]:
        """Get all sites, optionally filtered by status."""
        if status:
            rows = self.conn.execute(
                "SELECT * FROM dcim_site WHERE status = ? ORDER BY name",
                (status,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM dcim_site ORDER BY name"
            ).fetchall()

        return [self._row_to_dataclass(row, Site) for row in rows]

    def get_sites_with_counts(self) -> List[Dict[str, Any]]:
        """Get sites with device counts."""
        rows = self.conn.execute(
            "SELECT * FROM v_site_device_counts ORDER BY name"
        ).fetchall()
        return [dict(row) for row in rows]

    def update_site(self, site_id: int, **kwargs) -> bool:
        """Update a site."""
        if not kwargs:
            return False

        kwargs['updated_at'] = self._now()

        set_clause = ', '.join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [site_id]

        cursor = self.conn.execute(
            f"UPDATE dcim_site SET {set_clause} WHERE id = ?",
            values
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_site(self, site_id: int) -> bool:
        """Delete a site (cascades to devices)."""
        cursor = self.conn.execute(
            "DELETE FROM dcim_site WHERE id = ?", (site_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # =========================================================================
    # Manufacturers
    # =========================================================================

    def create_manufacturer(self, name: str, slug: str, **kwargs) -> int:
        """Create a new manufacturer."""
        fields = ['name', 'slug', 'created_at', 'updated_at']
        values = [name, slug, self._now(), self._now()]

        for key in ['description', 'netbox_id']:
            if key in kwargs and kwargs[key] is not None:
                fields.append(key)
                values.append(kwargs[key])

        placeholders = ', '.join(['?' for _ in fields])
        field_names = ', '.join(fields)

        cursor = self.conn.execute(
            f"INSERT INTO dcim_manufacturer ({field_names}) VALUES ({placeholders})",
            values
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_manufacturer(self, manufacturer_id: Optional[int] = None,
                         slug: Optional[str] = None) -> Optional[Manufacturer]:
        """Get a manufacturer by ID or slug."""
        if manufacturer_id:
            row = self.conn.execute(
                "SELECT * FROM dcim_manufacturer WHERE id = ?", (manufacturer_id,)
            ).fetchone()
        elif slug:
            row = self.conn.execute(
                "SELECT * FROM dcim_manufacturer WHERE slug = ?", (slug,)
            ).fetchone()
        else:
            return None

        return self._row_to_dataclass(row, Manufacturer)

    def get_manufacturers(self) -> List[Manufacturer]:
        """Get all manufacturers."""
        rows = self.conn.execute(
            "SELECT * FROM dcim_manufacturer ORDER BY name"
        ).fetchall()
        return [self._row_to_dataclass(row, Manufacturer) for row in rows]

    # =========================================================================
    # Platforms
    # =========================================================================

    def create_platform(self, name: str, slug: str, **kwargs) -> int:
        """Create a new platform."""
        fields = ['name', 'slug', 'created_at', 'updated_at']
        values = [name, slug, self._now(), self._now()]

        for key in ['manufacturer_id', 'description', 'netmiko_device_type',
                    'paging_disable_command', 'netbox_id']:
            if key in kwargs and kwargs[key] is not None:
                fields.append(key)
                values.append(kwargs[key])

        placeholders = ', '.join(['?' for _ in fields])
        field_names = ', '.join(fields)

        cursor = self.conn.execute(
            f"INSERT INTO dcim_platform ({field_names}) VALUES ({placeholders})",
            values
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_platform(self, platform_id: Optional[int] = None,
                     slug: Optional[str] = None) -> Optional[Platform]:
        """Get a platform by ID or slug."""
        query = """
            SELECT p.*, m.name as manufacturer_name, m.slug as manufacturer_slug
            FROM dcim_platform p
            LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
            WHERE p.{} = ?
        """

        if platform_id:
            row = self.conn.execute(
                query.format('id'), (platform_id,)
            ).fetchone()
        elif slug:
            row = self.conn.execute(
                query.format('slug'), (slug,)
            ).fetchone()
        else:
            return None

        return self._row_to_dataclass(row, Platform)

    def get_platforms(self, manufacturer_id: Optional[int] = None) -> List[Platform]:
        """Get all platforms, optionally filtered by manufacturer."""
        query = """
            SELECT p.*, m.name as manufacturer_name, m.slug as manufacturer_slug
            FROM dcim_platform p
            LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
        """

        if manufacturer_id:
            query += " WHERE p.manufacturer_id = ?"
            rows = self.conn.execute(query + " ORDER BY m.name, p.name",
                                     (manufacturer_id,)).fetchall()
        else:
            rows = self.conn.execute(query + " ORDER BY m.name, p.name").fetchall()

        return [self._row_to_dataclass(row, Platform) for row in rows]

    def get_platforms_with_counts(self) -> List[Dict[str, Any]]:
        """Get platforms with device counts."""
        rows = self.conn.execute(
            "SELECT * FROM v_platform_device_counts ORDER BY manufacturer_name, name"
        ).fetchall()
        return [dict(row) for row in rows]

    def update_platform(self, platform_id: int, **kwargs) -> bool:
        """Update a platform."""
        if not kwargs:
            return False

        kwargs['updated_at'] = self._now()

        set_clause = ', '.join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [platform_id]

        cursor = self.conn.execute(
            f"UPDATE dcim_platform SET {set_clause} WHERE id = ?",
            values
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # =========================================================================
    # Device Roles
    # =========================================================================

    def create_device_role(self, name: str, slug: str, **kwargs) -> int:
        """Create a new device role."""
        fields = ['name', 'slug', 'created_at', 'updated_at']
        values = [name, slug, self._now(), self._now()]

        for key in ['color', 'description', 'netbox_id']:
            if key in kwargs and kwargs[key] is not None:
                fields.append(key)
                values.append(kwargs[key])

        placeholders = ', '.join(['?' for _ in fields])
        field_names = ', '.join(fields)

        cursor = self.conn.execute(
            f"INSERT INTO dcim_device_role ({field_names}) VALUES ({placeholders})",
            values
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_device_role(self, role_id: Optional[int] = None,
                        slug: Optional[str] = None) -> Optional[DeviceRole]:
        """Get a device role by ID or slug."""
        if role_id:
            row = self.conn.execute(
                "SELECT * FROM dcim_device_role WHERE id = ?", (role_id,)
            ).fetchone()
        elif slug:
            row = self.conn.execute(
                "SELECT * FROM dcim_device_role WHERE slug = ?", (slug,)
            ).fetchone()
        else:
            return None

        return self._row_to_dataclass(row, DeviceRole)

    """
    Missing CRUD methods for DCIMRepository

    Add these methods to your dcim_repo.py file.
    Insert them in the appropriate sections as indicated.
    """

    # =============================================================================
    # ADD TO: Device Roles section (after get_device_roles method, around line 473)
    # =============================================================================

    def update_device_role(self, role_id: int, **kwargs) -> bool:
        """Update a device role."""
        if not kwargs:
            return False

        kwargs['updated_at'] = self._now()

        set_clause = ', '.join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [role_id]

        cursor = self.conn.execute(
            f"UPDATE dcim_device_role SET {set_clause} WHERE id = ?",
            values
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_device_role(self, role_id: int) -> bool:
        """Delete a device role. Devices using this role will have role_id set to NULL."""
        cursor = self.conn.execute(
            "DELETE FROM dcim_device_role WHERE id = ?", (role_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0


    def delete_platform(self, platform_id: int) -> bool:
        """Delete a platform. Devices using this platform will have platform_id set to NULL."""
        cursor = self.conn.execute(
            "DELETE FROM dcim_platform WHERE id = ?", (platform_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0




    def get_device_roles(self) -> List[DeviceRole]:
        """Get all device roles."""
        rows = self.conn.execute(
            "SELECT * FROM dcim_device_role ORDER BY name"
        ).fetchall()
        return [self._row_to_dataclass(row, DeviceRole) for row in rows]

    # =========================================================================
    # Devices
    # =========================================================================

    def create_device(self, name: str, site_id: int, **kwargs) -> int:
        """Create a new device."""
        fields = ['name', 'site_id', 'created_at', 'updated_at']
        values = [name, site_id, self._now(), self._now()]

        for key in ['platform_id', 'role_id', 'status', 'serial_number',
                    'asset_tag', 'primary_ip4', 'primary_ip6', 'oob_ip',
                    'credential_id', 'credential_tested_at', 'credential_test_result',
                    'ssh_port', 'description', 'comments', 'netbox_id']:
            if key in kwargs and kwargs[key] is not None:
                fields.append(key)
                values.append(kwargs[key])

        placeholders = ', '.join(['?' for _ in fields])
        field_names = ', '.join(fields)

        cursor = self.conn.execute(
            f"INSERT INTO dcim_device ({field_names}) VALUES ({placeholders})",
            values
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_device(self, device_id: Optional[int] = None,
                   name: Optional[str] = None,
                   site_id: Optional[int] = None,
                   netbox_id: Optional[int] = None) -> Optional[Device]:
        """
        Get a device by ID, name+site, or NetBox ID.

        For name lookup, site_id is required since names are only unique per site.
        """
        if device_id:
            row = self.conn.execute(
                "SELECT * FROM v_device_detail WHERE id = ?", (device_id,)
            ).fetchone()
        elif name and site_id:
            row = self.conn.execute(
                "SELECT * FROM v_device_detail WHERE name = ? AND site_id = ?",
                (name, site_id)
            ).fetchone()
        elif netbox_id:
            row = self.conn.execute(
                "SELECT * FROM v_device_detail WHERE netbox_id = ?", (netbox_id,)
            ).fetchone()
        else:
            return None

        return self._row_to_dataclass(row, Device)

    def get_devices(self,
                    site_id: Optional[int] = None,
                    site_slug: Optional[str] = None,
                    platform_id: Optional[int] = None,
                    platform_slug: Optional[str] = None,
                    role_id: Optional[int] = None,
                    role_slug: Optional[str] = None,
                    status: Optional[str] = None,
                    search: Optional[str] = None,
                    limit: Optional[int] = None,
                    offset: int = 0) -> List[Device]:
        """
        Get devices with optional filtering.

        Args:
            site_id: Filter by site ID
            site_slug: Filter by site slug
            platform_id: Filter by platform ID
            platform_slug: Filter by platform slug
            role_id: Filter by role ID
            role_slug: Filter by role slug
            status: Filter by status
            search: Search in name, primary_ip4, description
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            List of Device objects with joined details
        """
        query = "SELECT * FROM v_device_detail WHERE 1=1"
        params = []

        if site_id:
            query += " AND site_id = ?"
            params.append(site_id)
        elif site_slug:
            query += " AND site_slug = ?"
            params.append(site_slug)

        if platform_id:
            query += " AND platform_id = ?"
            params.append(platform_id)
        elif platform_slug:
            query += " AND platform_slug = ?"
            params.append(platform_slug)

        if role_id:
            query += " AND role_id = ?"
            params.append(role_id)
        elif role_slug:
            query += " AND role_slug = ?"
            params.append(role_slug)

        if status:
            query += " AND status = ?"
            params.append(status)

        if search:
            query += " AND (name LIKE ? OR primary_ip4 LIKE ? OR description LIKE ?)"
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])

        query += " ORDER BY site_name, name"

        if limit:
            query += f" LIMIT {limit} OFFSET {offset}"

        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_dataclass(row, Device) for row in rows]

    def get_device_count(self,
                         site_id: Optional[int] = None,
                         platform_id: Optional[int] = None,
                         status: Optional[str] = None) -> int:
        """Get count of devices with optional filtering."""
        query = "SELECT COUNT(*) FROM dcim_device WHERE 1=1"
        params = []

        if site_id:
            query += " AND site_id = ?"
            params.append(site_id)
        if platform_id:
            query += " AND platform_id = ?"
            params.append(platform_id)
        if status:
            query += " AND status = ?"
            params.append(status)

        return self.conn.execute(query, params).fetchone()[0]

    def update_device(self, device_id: int, **kwargs) -> bool:
        """Update a device."""
        if not kwargs:
            return False

        kwargs['updated_at'] = self._now()

        set_clause = ', '.join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [device_id]

        cursor = self.conn.execute(
            f"UPDATE dcim_device SET {set_clause} WHERE id = ?",
            values
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def update_device_last_collected(self, device_id: int) -> bool:
        """Update device's last_collected_at timestamp."""
        return self.update_device(device_id, last_collected_at=self._now())

    def delete_device(self, device_id: int) -> bool:
        """Delete a device."""
        cursor = self.conn.execute(
            "DELETE FROM dcim_device WHERE id = ?", (device_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def bulk_create_devices(self, devices: List[Dict[str, Any]]) -> int:
        """
        Bulk create devices.

        Args:
            devices: List of dicts with device data. Each must have 'name' and 'site_id'.

        Returns:
            Number of devices created
        """
        count = 0
        for device_data in devices:
            try:
                name = device_data.pop('name')
                site_id = device_data.pop('site_id')
                self.create_device(name, site_id, **device_data)
                count += 1
            except Exception:
                # Skip failures (e.g., duplicates)
                pass
        return count

    # =========================================================================
    # Credential Testing
    # =========================================================================

    def get_devices_by_credential_status(
        self,
        test_result: Optional[str] = None,
        has_credential: Optional[bool] = None,
        untested_only: bool = False,
        status: str = 'active',
    ) -> List[Device]:
        """
        Get devices filtered by credential status.

        Args:
            test_result: Filter by test result ('success', 'failed', 'untested')
            has_credential: True = only with credential_id, False = only without
            untested_only: Shortcut for test_result='untested'
            status: Device status filter (default: 'active')

        Returns:
            List of Device objects
        """
        query = "SELECT * FROM v_device_detail WHERE status = ?"
        params = [status]

        if untested_only:
            test_result = 'untested'

        if test_result:
            if test_result == 'untested':
                # Handle NULL as untested
                query += " AND (credential_test_result = ? OR credential_test_result IS NULL)"
            else:
                query += " AND credential_test_result = ?"
            params.append(test_result)

        if has_credential is True:
            query += " AND credential_id IS NOT NULL"
        elif has_credential is False:
            query += " AND credential_id IS NULL"

        query += " ORDER BY site_name, name"

        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_dataclass(row, Device) for row in rows]

    def get_credential_coverage_stats(self) -> Dict[str, int]:
        """
        Get credential coverage statistics for active devices.

        Returns:
            Dict with counts: total_active, with_credential, without_credential,
            test_success, test_failed, test_untested
        """
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
        stats['test_success'] = self.conn.execute(
            "SELECT COUNT(*) FROM dcim_device WHERE status = 'active' AND credential_test_result = 'success'"
        ).fetchone()[0]

        stats['test_failed'] = self.conn.execute(
            "SELECT COUNT(*) FROM dcim_device WHERE status = 'active' AND credential_test_result = 'failed'"
        ).fetchone()[0]

        # Untested includes NULL
        stats['test_untested'] = self.conn.execute(
            """SELECT COUNT(*) FROM dcim_device 
               WHERE status = 'active' AND (credential_test_result = 'untested' OR credential_test_result IS NULL)"""
        ).fetchone()[0]

        return stats

    def update_device_credential_test(
        self,
        device_id: int,
        credential_id: Optional[int],
        test_result: str,
    ) -> bool:
        """
        Update device credential test results.

        Args:
            device_id: Device ID
            credential_id: Working credential ID (None if no match)
            test_result: 'success' or 'failed'

        Returns:
            True if updated successfully
        """
        return self.update_device(
            device_id,
            credential_id=credential_id,
            credential_tested_at=self._now(),
            credential_test_result=test_result,
        )

    def get_devices_needing_credential_test(
        self,
        hours_since_test: int = 24,
        include_failed: bool = True,
        limit: Optional[int] = None,
    ) -> List[Device]:
        """
        Get devices that need credential testing.

        Args:
            hours_since_test: Re-test devices tested more than this many hours ago
            include_failed: Include devices with failed test results
            limit: Maximum devices to return

        Returns:
            List of Device objects needing testing
        """
        query = """
            SELECT * FROM v_device_detail 
            WHERE status = 'active' 
            AND primary_ip4 IS NOT NULL
            AND (
                credential_test_result IS NULL 
                OR credential_test_result = 'untested'
        """
        params = []

        if include_failed:
            query += " OR credential_test_result = 'failed'"

        # Include devices not tested recently
        query += """
                OR (credential_tested_at IS NOT NULL 
                    AND datetime(credential_tested_at) < datetime('now', ?))
            )
            ORDER BY 
                CASE WHEN credential_test_result IS NULL OR credential_test_result = 'untested' THEN 0 ELSE 1 END,
                credential_tested_at
        """
        params.append(f'-{hours_since_test} hours')

        if limit:
            query += f" LIMIT {limit}"

        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_dataclass(row, Device) for row in rows]

    # =========================================================================
    # Statistics
    # =========================================================================
    def get_stats(self) -> Dict[str, int]:
        """Get overall statistics."""
        stats = {}

        stats['total_sites'] = self.conn.execute(
            "SELECT COUNT(*) FROM dcim_site"
        ).fetchone()[0]

        stats['active_sites'] = self.conn.execute(
            "SELECT COUNT(*) FROM dcim_site WHERE status = 'active'"
        ).fetchone()[0]

        stats['total_devices'] = self.conn.execute(
            "SELECT COUNT(*) FROM dcim_device"
        ).fetchone()[0]

        stats['active_devices'] = self.conn.execute(
            "SELECT COUNT(*) FROM dcim_device WHERE status = 'active'"
        ).fetchone()[0]

        stats['total_platforms'] = self.conn.execute(
            "SELECT COUNT(*) FROM dcim_platform"
        ).fetchone()[0]

        stats['total_manufacturers'] = self.conn.execute(
            "SELECT COUNT(*) FROM dcim_manufacturer"
        ).fetchone()[0]

        stats['total_roles'] = self.conn.execute(
            "SELECT COUNT(*) FROM dcim_device_role"
        ).fetchone()[0]

        return stats

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()