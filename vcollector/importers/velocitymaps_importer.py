"""
VelocityCollector - VelocityMaps Importer

Import discovered devices from VelocityMaps discovery_summary.json into DCIM.

Usage (GUI):
    Import menu → VelocityMaps → Select file → Select site → Preview → Import
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime


@dataclass
class DeviceImport:
    """Represents a device to be imported."""
    hostname: str
    ip: str
    vendor: str = ""
    sys_descr: str = ""
    fqdn: str = ""
    depth: int = 0

    # Inferred fields
    platform_slug: str = ""
    platform_name: str = ""
    manufacturer_slug: str = ""
    role_slug: str = ""
    role_name: str = ""

    # Import status
    status: str = "pending"  # pending, imported, skipped, error
    message: str = ""
    device_id: Optional[int] = None
    traceback: Optional[str] = None  # For debugging errors


class VelocityMapsImporter:
    """
    Import devices from VelocityMaps discovery output.

    Parses discovery_summary.json and maps to vcollector DCIM schema.
    """

    # Platform detection patterns (order matters - more specific first)
    PLATFORM_PATTERNS = [
        # Arista
        (r"Arista Networks EOS", "arista_eos", "Arista EOS", "arista"),

        # Cisco - specific variants
        (r"NX-OS", "cisco_nxos", "Cisco NX-OS", "cisco"),
        (r"IOS-XE", "cisco_xe", "Cisco IOS-XE", "cisco"),
        (r"IOS-XR", "cisco_xr", "Cisco IOS-XR", "cisco"),
        (r"vios_l2", "cisco_ios", "Cisco IOS", "cisco"),  # Virtual L2 switch
        (r"IOSv Software", "cisco_ios", "Cisco IOS", "cisco"),  # Virtual router
        (r"Cisco IOS Software", "cisco_ios", "Cisco IOS", "cisco"),

        # Juniper
        (r"Juniper Networks.*JUNOS", "juniper_junos", "Juniper Junos", "juniper"),
        (r"JUNOS", "juniper_junos", "Juniper Junos", "juniper"),

        # Palo Alto
        (r"Palo Alto Networks", "paloalto_panos", "Palo Alto PAN-OS", "paloalto"),

        # Fortinet
        (r"FortiGate", "fortinet", "Fortinet FortiOS", "fortinet"),

        # HP/Aruba
        (r"HPE OfficeConnect", "hp_comware", "HP Comware", "hpe"),
        (r"ArubaOS-CX", "aruba_osswitch", "Aruba OS-CX", "aruba"),
        (r"ProCurve", "hp_procurve", "HP ProCurve", "hpe"),

        # Linux
        (r"Linux", "linux", "Linux", "generic"),
    ]

    # Role inference from hostname patterns
    ROLE_PATTERNS = [
        (r"(^|[-_])rtr([-_]|$|\d)", "router", "Router"),
        (r"(^|[-_])router([-_]|$|\d)", "router", "Router"),
        (r"(^|[-_])gw([-_]|$|\d)", "router", "Router"),
        (r"(^|[-_])spine([-_]|$|\d)", "spine", "Spine"),
        (r"(^|[-_])leaf([-_]|$|\d)", "access", "Access"),
        (r"(^|[-_])tor([-_]|$|\d)", "access", "Access"),
        (r"(^|[-_])access([-_]|$|\d)", "access", "Access"),
        (r"(^|[-_])dist([-_]|$|\d)", "distribution", "Distribution"),
        (r"(^|[-_])distribution([-_]|$|\d)", "distribution", "Distribution"),
        (r"(^|[-_])core([-_]|$|\d)", "core", "Core"),
        (r"(^|[-_])fw([-_]|$|\d)", "firewall", "Firewall"),
        (r"(^|[-_])firewall([-_]|$|\d)", "firewall", "Firewall"),
        (r"(^|[-_])edge([-_]|$|\d)", "edge", "Edge"),
        (r"(^|[-_])wan([-_]|$|\d)", "router", "Router"),
        (r"(^|[-_])sw([-_]|$|\d)", "switch", "Switch"),
        (r"(^|[-_])switch([-_]|$|\d)", "switch", "Switch"),
    ]

    def __init__(self, repo=None):
        """
        Initialize importer.

        Args:
            repo: DCIMRepository instance (optional, for lookups)
        """
        self.repo = repo
        self.devices: List[DeviceImport] = []
        self.source_path: Optional[Path] = None
        self.discovery_data: Dict[str, Any] = {}
        self.clean_hostnames: bool = True  # Strip domain suffix from hostnames

    def load(self, path: str | Path) -> int:
        """
        Load discovery_summary.json file.

        Args:
            path: Path to discovery_summary.json

        Returns:
            Number of devices loaded
        """
        self.source_path = Path(path)

        with open(self.source_path, 'r') as f:
            self.discovery_data = json.load(f)

        self.devices = []

        for device_data in self.discovery_data.get('devices', []):
            device = self._parse_device(device_data)
            if device:
                self.devices.append(device)

        return len(self.devices)

    def reload(self) -> int:
        """
        Reload devices from already-loaded discovery data.

        Call after changing options like clean_hostnames to re-parse.

        Returns:
            Number of devices loaded
        """
        if not self.discovery_data:
            return 0

        self.devices = []

        for device_data in self.discovery_data.get('devices', []):
            device = self._parse_device(device_data)
            if device:
                self.devices.append(device)

        return len(self.devices)

    def _parse_device(self, data: Dict[str, Any]) -> Optional[DeviceImport]:
        """Parse a device entry from discovery data."""
        hostname = data.get('hostname', '').strip()
        ip = data.get('ip', '').strip()

        if not hostname or not ip:
            return None

        # Optionally strip domain suffix
        if self.clean_hostnames and '.' in hostname:
            hostname = hostname.split('.')[0]

        device = DeviceImport(
            hostname=hostname,
            ip=ip,
            vendor=data.get('vendor', ''),
            sys_descr=data.get('sysDescr', ''),
            fqdn=data.get('fqdn', ''),
            depth=data.get('depth', 0),
        )

        # Infer platform from sysDescr
        platform_slug, platform_name, mfg_slug = self._infer_platform(device.sys_descr)
        device.platform_slug = platform_slug
        device.platform_name = platform_name
        device.manufacturer_slug = mfg_slug or device.vendor.lower()

        # Infer role from hostname
        role_slug, role_name = self._infer_role(hostname)
        device.role_slug = role_slug
        device.role_name = role_name

        return device

    def _infer_platform(self, sys_descr: str) -> Tuple[str, str, str]:
        """
        Infer platform from sysDescr.

        Returns:
            Tuple of (platform_slug, platform_name, manufacturer_slug)
        """
        if not sys_descr:
            return ("", "", "")

        for pattern, slug, name, mfg in self.PLATFORM_PATTERNS:
            if re.search(pattern, sys_descr, re.IGNORECASE):
                return (slug, name, mfg)

        return ("", "", "")

    def _infer_role(self, hostname: str) -> Tuple[str, str]:
        """
        Infer device role from hostname.

        Returns:
            Tuple of (role_slug, role_name)
        """
        hostname_lower = hostname.lower()

        for pattern, slug, name in self.ROLE_PATTERNS:
            if re.search(pattern, hostname_lower):
                return (slug, name)

        # Default to switch if no pattern matches
        return ("switch", "Switch")

    def get_summary(self) -> Dict[str, Any]:
        """Get import summary statistics."""
        platforms = {}
        roles = {}
        manufacturers = {}

        for device in self.devices:
            # Count platforms
            p = device.platform_name or "Unknown"
            platforms[p] = platforms.get(p, 0) + 1

            # Count roles
            r = device.role_name or "Unknown"
            roles[r] = roles.get(r, 0) + 1

            # Count manufacturers
            m = device.manufacturer_slug or device.vendor or "unknown"
            manufacturers[m] = manufacturers.get(m, 0) + 1

        return {
            "total_devices": len(self.devices),
            "source_file": str(self.source_path) if self.source_path else None,
            "platforms": platforms,
            "roles": roles,
            "manufacturers": manufacturers,
        }

    def import_to_site(self, site_id: int,
                       skip_existing: bool = True,
                       create_missing_platforms: bool = False) -> Dict[str, Any]:
        """
        Import devices to specified site.

        Args:
            site_id: Target site ID
            skip_existing: Skip devices that already exist (by name or IP)
            create_missing_platforms: Create platforms if not found

        Returns:
            Import results dict
        """
        if not self.repo:
            raise ValueError("Repository not configured")

        results = {
            "imported": 0,
            "skipped": 0,
            "errors": 0,
            "devices": [],
        }

        # Cache lookups
        platform_cache = {}
        role_cache = {}

        for device in self.devices:
            try:
                # Check if device already exists
                if skip_existing:
                    existing = self._find_existing_device(device, site_id)
                    if existing:
                        device.status = "skipped"
                        device.message = f"Already exists: {existing.name}"
                        results["skipped"] += 1
                        results["devices"].append(device)
                        continue

                # Resolve platform
                platform_id = None
                if device.platform_slug:
                    if device.platform_slug not in platform_cache:
                        platform = self.repo.get_platform(slug=device.platform_slug)
                        platform_cache[device.platform_slug] = platform.id if platform else None
                    platform_id = platform_cache[device.platform_slug]

                # Resolve role
                role_id = None
                if device.role_slug:
                    if device.role_slug not in role_cache:
                        role = self.repo.get_device_role(slug=device.role_slug)
                        role_cache[device.role_slug] = role.id if role else None
                    role_id = role_cache[device.role_slug]

                # Create device
                device_id = self.repo.create_device(
                    name=device.hostname,
                    site_id=site_id,
                    platform_id=platform_id,
                    role_id=role_id,
                    primary_ip4=device.ip,
                    description=self._build_description(device),
                )

                device.status = "imported"
                device.device_id = device_id
                device.message = f"Created with ID {device_id}"
                results["imported"] += 1

            except Exception as e:
                import traceback
                device.status = "error"
                device.message = f"{type(e).__name__}: {str(e)}"
                # Store full traceback for debugging
                device.traceback = traceback.format_exc()
                results["errors"] += 1

            results["devices"].append(device)

        # Verify import by querying the database
        if results["imported"] > 0:
            verify_count = self.repo.get_device_count(site_id=site_id)
            results["verified_count"] = verify_count

            # Get the actual devices we just imported for verification
            imported_names = [d.hostname for d in self.devices if d.status == "imported"]
            verified_devices = []
            for name in imported_names[:5]:  # Check first 5
                dev = self.repo.get_device(name=name, site_id=site_id)
                if dev:
                    verified_devices.append(dev.name)
            results["verified_devices"] = verified_devices

        return results

    def _find_existing_device(self, device: DeviceImport, site_id: int):
        """Check if device already exists by name or IP."""
        # Check by name in site
        existing = self.repo.get_device(name=device.hostname, site_id=site_id)
        if existing:
            return existing

        # Check by IP using search (get_devices doesn't have direct IP filter)
        devices = self.repo.get_devices(search=device.ip)
        for d in devices:
            if d.primary_ip4 == device.ip:
                return d

        return None

    def _build_description(self, device: DeviceImport) -> str:
        """Build device description from discovery data."""
        parts = []

        if device.sys_descr:
            # Truncate long sysDescr
            desc = device.sys_descr[:200]
            if len(device.sys_descr) > 200:
                desc += "..."
            parts.append(desc)

        parts.append(f"Imported from VelocityMaps on {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        return " | ".join(parts)


# =============================================================================
# PyQt6 GUI Dialog
# =============================================================================

try:
    from PyQt6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
        QPushButton, QLabel, QLineEdit, QComboBox, QTableWidget,
        QTableWidgetItem, QFileDialog, QMessageBox, QGroupBox,
        QHeaderView, QCheckBox, QSplitter, QWidget,
        QTextEdit
    )
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor

    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False


if PYQT_AVAILABLE:


    class VelocityMapsImportDialog(QDialog):
        """
        VelocityMaps Import Dialog.

        Allows user to:
        1. Browse for discovery_summary.json
        2. Select target site
        3. Preview devices with inferred platforms/roles
        4. Import devices
        """

        def __init__(self, repo, parent=None):
            super().__init__(parent)
            self.repo = repo
            self.importer = VelocityMapsImporter(repo)

            self.setWindowTitle("Import from VelocityMaps")
            self.setMinimumSize(900, 600)

            self._setup_ui()
            self._load_sites()

        def _setup_ui(self):
            """Build the dialog UI."""
            layout = QVBoxLayout(self)

            # === File Selection ===
            file_group = QGroupBox("Source File")
            file_layout = QHBoxLayout(file_group)

            self.file_path = QLineEdit()
            self.file_path.setPlaceholderText("Select discovery_summary.json...")
            self.file_path.setReadOnly(True)
            file_layout.addWidget(self.file_path, stretch=1)

            browse_btn = QPushButton("Browse...")
            browse_btn.clicked.connect(self._browse_file)
            file_layout.addWidget(browse_btn)

            layout.addWidget(file_group)

            # === Site Selection ===
            site_group = QGroupBox("Target Site")
            site_layout = QFormLayout(site_group)

            self.site_combo = QComboBox()
            self.site_combo.setMinimumWidth(300)
            site_layout.addRow("Import to Site:", self.site_combo)

            self.skip_existing_cb = QCheckBox("Skip existing devices (match by name or IP)")
            self.skip_existing_cb.setChecked(True)
            site_layout.addRow("", self.skip_existing_cb)

            self.clean_hostnames_cb = QCheckBox("Clean hostnames (strip domain suffix)")
            self.clean_hostnames_cb.setChecked(True)
            self.clean_hostnames_cb.stateChanged.connect(self._on_clean_hostnames_changed)
            site_layout.addRow("", self.clean_hostnames_cb)

            layout.addWidget(site_group)

            # === Preview Table ===
            preview_group = QGroupBox("Device Preview")
            preview_layout = QVBoxLayout(preview_group)

            self.preview_table = QTableWidget()
            self.preview_table.setColumnCount(6)
            self.preview_table.setHorizontalHeaderLabels([
                "Hostname", "IP Address", "Platform", "Role", "Vendor", "Status"
            ])
            self.preview_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Stretch
            )
            self.preview_table.setAlternatingRowColors(True)
            self.preview_table.setSelectionBehavior(
                QTableWidget.SelectionBehavior.SelectRows
            )
            preview_layout.addWidget(self.preview_table)

            # Summary label
            self.summary_label = QLabel("No file loaded")
            self.summary_label.setStyleSheet("color: #888;")
            preview_layout.addWidget(self.summary_label)

            layout.addWidget(preview_group, stretch=1)

            # === Buttons ===
            btn_layout = QHBoxLayout()
            btn_layout.addStretch()

            self.import_btn = QPushButton("Import Devices")
            self.import_btn.setEnabled(False)
            self.import_btn.clicked.connect(self._do_import)
            btn_layout.addWidget(self.import_btn)

            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.reject)
            btn_layout.addWidget(close_btn)

            layout.addLayout(btn_layout)

        def _load_sites(self):
            """Load sites into combo box."""
            self.site_combo.clear()
            self.site_combo.addItem("-- Select Site --", None)

            sites = self.repo.get_sites(status='active')
            for site in sites:
                self.site_combo.addItem(f"{site.name} ({site.slug})", site.id)

        def _browse_file(self):
            """Open file browser for discovery_summary.json."""
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select VelocityMaps Discovery File",
                "",
                "JSON Files (*.json);;All Files (*)"
            )

            if path:
                self.file_path.setText(path)
                self._load_preview(path)

        def _load_preview(self, path: str):
            """Load and preview devices from file."""
            try:
                count = self.importer.load(path)
                self._populate_preview_table()

                summary = self.importer.get_summary()
                platform_str = ", ".join(
                    f"{k}: {v}" for k, v in summary['platforms'].items()
                )
                self.summary_label.setText(
                    f"Loaded {count} devices | Platforms: {platform_str}"
                )

                self.import_btn.setEnabled(count > 0)

            except Exception as e:
                QMessageBox.critical(
                    self, "Load Error",
                    f"Failed to load file:\n{e}"
                )
                self.summary_label.setText("Error loading file")
                self.import_btn.setEnabled(False)

        def _on_clean_hostnames_changed(self, state):
            """Handle clean hostnames checkbox change - refresh preview."""
            if not self.importer.discovery_data:
                return

            self.importer.clean_hostnames = (state == Qt.CheckState.Checked.value)
            self.importer.reload()
            self._populate_preview_table()

            # Update summary
            summary = self.importer.get_summary()
            platform_str = ", ".join(
                f"{k}: {v}" for k, v in summary['platforms'].items()
            )
            self.summary_label.setText(
                f"Loaded {len(self.importer.devices)} devices | Platforms: {platform_str}"
            )

        def _populate_preview_table(self):
            """Fill preview table with devices."""
            self.preview_table.setRowCount(len(self.importer.devices))

            for row, device in enumerate(self.importer.devices):
                self.preview_table.setItem(
                    row, 0, QTableWidgetItem(device.hostname)
                )
                self.preview_table.setItem(
                    row, 1, QTableWidgetItem(device.ip)
                )
                self.preview_table.setItem(
                    row, 2, QTableWidgetItem(device.platform_name or "(unknown)")
                )
                self.preview_table.setItem(
                    row, 3, QTableWidgetItem(device.role_name or "(unknown)")
                )
                self.preview_table.setItem(
                    row, 4, QTableWidgetItem(device.vendor or device.manufacturer_slug)
                )

                status_item = QTableWidgetItem(device.status)
                self.preview_table.setItem(row, 5, status_item)

        def _do_import(self):
            """Execute the import."""
            site_id = self.site_combo.currentData()

            if not site_id:
                QMessageBox.warning(
                    self, "No Site Selected",
                    "Please select a target site for the import."
                )
                return

            # Confirm
            count = len(self.importer.devices)
            site_name = self.site_combo.currentText()

            reply = QMessageBox.question(
                self, "Confirm Import",
                f"Import {count} devices to {site_name}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

            # Run import synchronously (SQLite doesn't like cross-thread access)
            self.import_btn.setEnabled(False)
            self.setCursor(Qt.CursorShape.WaitCursor)

            try:
                results = self.importer.import_to_site(
                    site_id,
                    skip_existing=self.skip_existing_cb.isChecked()
                )
                self._import_finished(results)
            except Exception as e:
                self._import_error(str(e))
            finally:
                self.setCursor(Qt.CursorShape.ArrowCursor)
                self.import_btn.setEnabled(True)

        def _import_finished(self, results: dict):
            """Handle import completion."""
            # Update table with results
            self._populate_preview_table()

            # Color-code status column
            for row, device in enumerate(self.importer.devices):
                status_item = self.preview_table.item(row, 5)
                if device.status == "imported":
                    status_item.setBackground(QColor("#2d5a2d"))
                    status_item.setText("✓ Imported")
                elif device.status == "skipped":
                    status_item.setBackground(QColor("#5a5a2d"))
                    status_item.setText("○ Skipped")
                elif device.status == "error":
                    status_item.setBackground(QColor("#5a2d2d"))
                    status_item.setText("✗ Error")

            # Show summary with verification
            verified_msg = ""
            if "verified_count" in results:
                verified_msg = f"\n\nVerified: {results['verified_count']} devices in site"
            if "verified_devices" in results and results["verified_devices"]:
                verified_msg += f"\nSample: {', '.join(results['verified_devices'][:3])}"

            # Show error details if any
            error_msg = ""
            if results["errors"] > 0:
                error_devices = [d for d in self.importer.devices if d.status == "error"]
                if error_devices:
                    error_msg = "\n\nErrors:"
                    for ed in error_devices[:3]:  # Show first 3 errors
                        error_msg += f"\n  • {ed.hostname}: {ed.message}"
                    if len(error_devices) > 3:
                        error_msg += f"\n  ... and {len(error_devices) - 3} more"

            QMessageBox.information(
                self, "Import Complete",
                f"Import finished:\n\n"
                f"  Imported: {results['imported']}\n"
                f"  Skipped: {results['skipped']}\n"
                f"  Errors: {results['errors']}"
                f"{verified_msg}"
                f"{error_msg}"
            )

        def _import_error(self, error: str):
            """Handle import error."""
            QMessageBox.critical(
                self, "Import Error",
                f"Import failed:\n{error}"
            )


# =============================================================================
# CLI Support (optional, for testing)
# =============================================================================

def main():
    """CLI entry point for testing."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Import devices from VelocityMaps discovery"
    )
    parser.add_argument(
        "file",
        help="Path to discovery_summary.json"
    )
    parser.add_argument(
        "--preview", "-p",
        action="store_true",
        help="Preview only, don't import"
    )
    parser.add_argument(
        "--test-db",
        help="Test import to a temp database with specified site name"
    )

    args = parser.parse_args()

    importer = VelocityMapsImporter()
    count = importer.load(args.file)

    print(f"\nLoaded {count} devices from {args.file}\n")

    summary = importer.get_summary()

    print("Platforms detected:")
    for platform, cnt in summary['platforms'].items():
        print(f"  {platform}: {cnt}")

    print("\nRoles inferred:")
    for role, cnt in summary['roles'].items():
        print(f"  {role}: {cnt}")

    print("\nDevices:")
    print("-" * 80)
    for device in importer.devices:
        print(f"  {device.hostname:20} {device.ip:15} {device.platform_slug:15} {device.role_slug}")

    # Test actual DB import if requested
    if args.test_db:
        print(f"\n{'='*80}")
        print(f"Testing DB import to site: {args.test_db}")
        print(f"{'='*80}\n")

        try:
            # Import the repo
            from vcollector.db.dcim_repo import DCIMRepository
            from vcollector.db.db_schema import DCIMDatabase
            import tempfile
            import os

            # Create temp DB
            temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
            temp_db.close()
            print(f"Using temp DB: {temp_db.name}")

            # Initialize schema
            db = DCIMDatabase(temp_db.name)
            db.init_schema(include_defaults=True)
            db.close()

            # Create repo and site
            repo = DCIMRepository(temp_db.name)
            site_id = repo.create_site(name=args.test_db, slug=args.test_db.lower().replace(' ', '-'))
            print(f"Created site '{args.test_db}' with ID: {site_id}")

            # Do the import
            importer.repo = repo
            results = importer.import_to_site(site_id)

            print(f"\nImport results:")
            print(f"  Imported: {results['imported']}")
            print(f"  Skipped: {results['skipped']}")
            print(f"  Errors: {results['errors']}")

            if 'verified_count' in results:
                print(f"  Verified count in DB: {results['verified_count']}")

            if 'verified_devices' in results:
                print(f"  Verified devices: {results['verified_devices']}")

            # Show any errors
            for device in importer.devices:
                if device.status == "error":
                    print(f"\n  ERROR - {device.hostname}: {device.message}")
                    if device.traceback:
                        print(f"  Traceback:\n{device.traceback}")

            # Verify by direct query
            print(f"\nDirect verification query:")
            devices = repo.get_devices(site_id=site_id)
            print(f"  Found {len(devices)} devices in site")
            for d in devices[:5]:
                print(f"    - {d.name} ({d.primary_ip4})")

            repo.close()

            # Cleanup
            os.unlink(temp_db.name)
            print(f"\nCleaned up temp DB")

        except ImportError as e:
            print(f"Could not import vcollector modules: {e}")
            print("Run from within the vcollector project directory")


if __name__ == "__main__":
    main()