"""
VelocityCollector Device Dialogs

Dialog windows for viewing and editing device records.
- DeviceDetailDialog: Read-only view of device details
- DeviceEditDialog: Create/Edit form with validation
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QTextEdit, QDialogButtonBox, QPushButton,
    QMessageBox, QFrame, QGroupBox, QGridLayout, QWidget, QTabWidget
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIntValidator, QRegularExpressionValidator

from vcollector.dcim.dcim_repo import (
    DCIMRepository, Device, Site, Platform, DeviceRole, DeviceStatus
)

from typing import Optional, List
from datetime import datetime
import re


class DeviceDetailDialog(QDialog):
    """
    Read-only dialog showing full device details.

    Used for quick inspection without risk of accidental edits.
    """

    # Signal to request opening edit dialog
    edit_requested = pyqtSignal(int)  # device_id

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        self.device = device
        self.setWindowTitle(f"Device: {device.name}")
        self.setMinimumWidth(550)
        self.setMinimumHeight(400)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Use tabs for organization
        tabs = QTabWidget()

        # === General Tab ===
        general_tab = QWidget()
        general_layout = QFormLayout(general_tab)
        general_layout.setSpacing(8)
        general_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Identity section
        general_layout.addRow("Name:", self._label(self.device.name))
        general_layout.addRow("Status:", self._status_label(self.device.status))
        general_layout.addRow("Site:", self._label(self.device.site_name))
        general_layout.addRow("Role:", self._label(self.device.role_name))

        # Add separator
        general_layout.addRow(self._separator())

        # Network section
        general_layout.addRow("Primary IPv4:", self._label(self.device.primary_ip4))
        general_layout.addRow("Primary IPv6:", self._label(self.device.primary_ip6))
        general_layout.addRow("OOB IP:", self._label(self.device.oob_ip))
        general_layout.addRow("SSH Port:", self._label(str(self.device.ssh_port or 22)))

        tabs.addTab(general_tab, "General")

        # === Platform Tab ===
        platform_tab = QWidget()
        platform_layout = QFormLayout(platform_tab)
        platform_layout.setSpacing(8)
        platform_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        platform_layout.addRow("Platform:", self._label(self.device.platform_name))
        platform_layout.addRow("Manufacturer:", self._label(self.device.manufacturer_name))
        platform_layout.addRow("Netmiko Type:", self._label(self.device.netmiko_device_type))
        platform_layout.addRow("Paging Command:", self._label(self.device.paging_disable_command))

        platform_layout.addRow(self._separator())

        platform_layout.addRow("Serial Number:", self._label(self.device.serial_number))
        platform_layout.addRow("Asset Tag:", self._label(self.device.asset_tag))

        tabs.addTab(platform_tab, "Platform")

        # === Metadata Tab ===
        meta_tab = QWidget()
        meta_layout = QVBoxLayout(meta_tab)

        # Timestamps
        time_form = QFormLayout()
        time_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        time_form.addRow("Created:", self._label(self._format_timestamp(self.device.created_at)))
        time_form.addRow("Updated:", self._label(self._format_timestamp(self.device.updated_at)))
        time_form.addRow("Last Collected:", self._label(self._format_timestamp(self.device.last_collected_at)))
        time_form.addRow("NetBox ID:", self._label(str(self.device.netbox_id) if self.device.netbox_id else None))
        time_form.addRow("Credential ID:",
                         self._label(str(self.device.credential_id) if self.device.credential_id else None))
        meta_layout.addLayout(time_form)

        # Description
        if self.device.description:
            meta_layout.addWidget(QLabel("Description:"))
            desc = QTextEdit()
            desc.setPlainText(self.device.description)
            desc.setReadOnly(True)
            desc.setMaximumHeight(60)
            meta_layout.addWidget(desc)

        # Comments
        if self.device.comments:
            meta_layout.addWidget(QLabel("Comments:"))
            comments = QTextEdit()
            comments.setPlainText(self.device.comments)
            comments.setReadOnly(True)
            comments.setMaximumHeight(60)
            meta_layout.addWidget(comments)

        meta_layout.addStretch()
        tabs.addTab(meta_tab, "Metadata")

        layout.addWidget(tabs)

        # Button row
        button_layout = QHBoxLayout()

        edit_btn = QPushButton("Edit")
        edit_btn.setProperty("secondary", True)
        edit_btn.clicked.connect(self._on_edit)
        button_layout.addWidget(edit_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _label(self, text: Optional[str]) -> QLabel:
        """Create a label with default placeholder for empty values."""
        label = QLabel(text or "—")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _status_label(self, status: Optional[str]) -> QLabel:
        """Create a colored status label."""
        label = QLabel(status or "—")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        colors = {
            'active': '#2ecc71',
            'planned': '#3498db',
            'staged': '#f39c12',
            'failed': '#e74c3c',
            'offline': '#95a5a6',
            'decommissioning': '#9b59b6',
            'inventory': '#1abc9c',
        }
        if status in colors:
            label.setStyleSheet(f"color: {colors[status]}; font-weight: bold;")

        return label

    def _separator(self) -> QFrame:
        """Create a horizontal separator line."""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _format_timestamp(self, ts: Optional[str]) -> str:
        """Format timestamp for display."""
        if not ts:
            return "—"
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            return ts

    def _on_edit(self):
        """Handle edit button click."""
        self.edit_requested.emit(self.device.id)
        self.accept()


class DeviceEditDialog(QDialog):
    """
    Create/Edit dialog for device records.

    Usage:
        # Edit existing device
        dialog = DeviceEditDialog(repo, device=existing_device)

        # Create new device
        dialog = DeviceEditDialog(repo, device=None)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Device was saved
            device_id = dialog.saved_device_id
    """

    # Signal emitted when device is saved (new or updated)
    device_saved = pyqtSignal(int)  # device_id

    def __init__(self, repo: DCIMRepository, device: Optional[Device] = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.device = device
        self.is_edit_mode = device is not None
        self.saved_device_id: Optional[int] = None

        # Cache for lookups
        self._sites: List[Site] = []
        self._platforms: List[Platform] = []
        self._roles: List[DeviceRole] = []

        self.setWindowTitle("Edit Device" if self.is_edit_mode else "New Device")
        self.setMinimumWidth(500)
        self.setMinimumHeight(550)

        self.init_ui()
        self.load_lookups()

        if self.is_edit_mode:
            self.populate_form()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Use tabs for organization
        tabs = QTabWidget()

        # === Identity Tab ===
        identity_tab = QWidget()
        identity_layout = QFormLayout(identity_tab)
        identity_layout.setSpacing(10)
        identity_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Name (required)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Device hostname (required)")
        self.name_input.setMaxLength(100)
        identity_layout.addRow("Name: *", self.name_input)

        # Site (required)
        self.site_combo = QComboBox()
        identity_layout.addRow("Site: *", self.site_combo)

        # Status
        self.status_combo = QComboBox()
        self.status_combo.addItems([s.value for s in DeviceStatus])
        identity_layout.addRow("Status:", self.status_combo)

        # Role
        self.role_combo = QComboBox()
        identity_layout.addRow("Role:", self.role_combo)

        # Platform
        self.platform_combo = QComboBox()
        identity_layout.addRow("Platform:", self.platform_combo)

        tabs.addTab(identity_tab, "Identity")

        # === Network Tab ===
        network_tab = QWidget()
        network_layout = QFormLayout(network_tab)
        network_layout.setSpacing(10)
        network_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Primary IPv4
        self.ip4_input = QLineEdit()
        self.ip4_input.setPlaceholderText("e.g., 192.168.1.1 or 192.168.1.1/24")
        network_layout.addRow("Primary IPv4:", self.ip4_input)

        # Primary IPv6
        self.ip6_input = QLineEdit()
        self.ip6_input.setPlaceholderText("e.g., 2001:db8::1")
        network_layout.addRow("Primary IPv6:", self.ip6_input)

        # OOB IP
        self.oob_input = QLineEdit()
        self.oob_input.setPlaceholderText("Out-of-band management IP")
        network_layout.addRow("OOB IP:", self.oob_input)

        # SSH Port
        self.ssh_port_input = QSpinBox()
        self.ssh_port_input.setRange(1, 65535)
        self.ssh_port_input.setValue(22)
        network_layout.addRow("SSH Port:", self.ssh_port_input)

        # Credential override
        self.credential_input = QSpinBox()
        self.credential_input.setRange(0, 9999)
        self.credential_input.setValue(0)
        self.credential_input.setSpecialValueText("Default")
        network_layout.addRow("Credential ID:", self.credential_input)

        tabs.addTab(network_tab, "Network")

        # === Hardware Tab ===
        hardware_tab = QWidget()
        hardware_layout = QFormLayout(hardware_tab)
        hardware_layout.setSpacing(10)
        hardware_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Serial Number
        self.serial_input = QLineEdit()
        self.serial_input.setPlaceholderText("Hardware serial number")
        hardware_layout.addRow("Serial Number:", self.serial_input)

        # Asset Tag
        self.asset_tag_input = QLineEdit()
        self.asset_tag_input.setPlaceholderText("Internal asset tag")
        hardware_layout.addRow("Asset Tag:", self.asset_tag_input)

        # NetBox ID (read-only in edit, hidden in create)
        self.netbox_id_input = QLineEdit()
        self.netbox_id_input.setPlaceholderText("NetBox sync ID")
        if self.is_edit_mode:
            self.netbox_id_input.setReadOnly(True)
            self.netbox_id_input.setStyleSheet("background-color: #3a3a3a;")
        hardware_layout.addRow("NetBox ID:", self.netbox_id_input)

        tabs.addTab(hardware_tab, "Hardware")

        # === Notes Tab ===
        notes_tab = QWidget()
        notes_layout = QVBoxLayout(notes_tab)

        notes_layout.addWidget(QLabel("Description:"))
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("Brief description of the device")
        self.description_input.setMaximumHeight(80)
        notes_layout.addWidget(self.description_input)

        notes_layout.addWidget(QLabel("Comments:"))
        self.comments_input = QTextEdit()
        self.comments_input.setPlaceholderText("Additional notes, maintenance info, etc.")
        notes_layout.addWidget(self.comments_input)

        tabs.addTab(notes_tab, "Notes")

        layout.addWidget(tabs)

        # Validation message area
        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #e74c3c;")
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)

        # Button row
        button_layout = QHBoxLayout()

        # Delete button (edit mode only)
        if self.is_edit_mode:
            delete_btn = QPushButton("Delete")
            delete_btn.setStyleSheet("background-color: #c0392b;")
            delete_btn.clicked.connect(self._on_delete)
            button_layout.addWidget(delete_btn)

        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

    def load_lookups(self):
        """Load dropdown options from database."""
        # Sites
        self._sites = self.repo.get_sites()
        self.site_combo.clear()
        self.site_combo.addItem("— Select Site —", None)
        for site in self._sites:
            self.site_combo.addItem(f"{site.name} ({site.slug})", site.id)

        # Platforms
        self._platforms = self.repo.get_platforms()
        self.platform_combo.clear()
        self.platform_combo.addItem("— None —", None)
        for platform in self._platforms:
            display = f"{platform.name}"
            if platform.manufacturer_name:
                display = f"{platform.manufacturer_name} - {platform.name}"
            self.platform_combo.addItem(display, platform.id)

        # Roles
        self._roles = self.repo.get_device_roles()
        self.role_combo.clear()
        self.role_combo.addItem("— None —", None)
        for role in self._roles:
            self.role_combo.addItem(role.name, role.id)

    def populate_form(self):
        """Populate form fields from existing device."""
        if not self.device:
            return

        # Identity
        self.name_input.setText(self.device.name or "")

        # Site combo
        site_idx = self.site_combo.findData(self.device.site_id)
        if site_idx >= 0:
            self.site_combo.setCurrentIndex(site_idx)

        # Status
        status_idx = self.status_combo.findText(self.device.status or "active")
        if status_idx >= 0:
            self.status_combo.setCurrentIndex(status_idx)

        # Role combo
        role_idx = self.role_combo.findData(self.device.role_id)
        if role_idx >= 0:
            self.role_combo.setCurrentIndex(role_idx)

        # Platform combo
        platform_idx = self.platform_combo.findData(self.device.platform_id)
        if platform_idx >= 0:
            self.platform_combo.setCurrentIndex(platform_idx)

        # Network
        self.ip4_input.setText(self.device.primary_ip4 or "")
        self.ip6_input.setText(self.device.primary_ip6 or "")
        self.oob_input.setText(self.device.oob_ip or "")
        self.ssh_port_input.setValue(self.device.ssh_port or 22)
        self.credential_input.setValue(self.device.credential_id or 0)

        # Hardware
        self.serial_input.setText(self.device.serial_number or "")
        self.asset_tag_input.setText(self.device.asset_tag or "")
        self.netbox_id_input.setText(str(self.device.netbox_id) if self.device.netbox_id else "")

        # Notes
        self.description_input.setPlainText(self.device.description or "")
        self.comments_input.setPlainText(self.device.comments or "")

    def validate(self) -> bool:
        """Validate form inputs. Returns True if valid."""
        errors = []

        # Name required
        name = self.name_input.text().strip()
        if not name:
            errors.append("Device name is required")
        elif len(name) > 100:
            errors.append("Device name must be 100 characters or less")

        # Site required
        site_id = self.site_combo.currentData()
        if not site_id:
            errors.append("Site selection is required")

        # Validate IPv4 format if provided
        ip4 = self.ip4_input.text().strip()
        if ip4 and not self._validate_ip4(ip4):
            errors.append("Invalid IPv4 address format")

        # Validate IPv6 format if provided
        ip6 = self.ip6_input.text().strip()
        if ip6 and not self._validate_ip6(ip6):
            errors.append("Invalid IPv6 address format")

        # OOB IP validation
        oob = self.oob_input.text().strip()
        if oob and not self._validate_ip4(oob) and not self._validate_ip6(oob):
            errors.append("Invalid OOB IP address format")

        if errors:
            self.validation_label.setText("• " + "\n• ".join(errors))
            return False

        self.validation_label.setText("")
        return True

    def _validate_ip4(self, ip: str) -> bool:
        """Validate IPv4 address (with optional CIDR)."""
        # Strip CIDR if present
        if '/' in ip:
            ip, cidr = ip.split('/', 1)
            try:
                cidr_int = int(cidr)
                if not (0 <= cidr_int <= 32):
                    return False
            except ValueError:
                return False

        # Validate octets
        parts = ip.split('.')
        if len(parts) != 4:
            return False

        for part in parts:
            try:
                num = int(part)
                if not (0 <= num <= 255):
                    return False
            except ValueError:
                return False

        return True

    def _validate_ip6(self, ip: str) -> bool:
        """Basic IPv6 validation."""
        # Strip CIDR if present
        if '/' in ip:
            ip, cidr = ip.split('/', 1)
            try:
                cidr_int = int(cidr)
                if not (0 <= cidr_int <= 128):
                    return False
            except ValueError:
                return False

        # Very basic check - proper validation would use ipaddress module
        if '::' in ip or ':' in ip:
            return True
        return False

    def _collect_form_data(self) -> dict:
        """Collect form data into a dict for save."""
        data = {
            'name': self.name_input.text().strip(),
            'site_id': self.site_combo.currentData(),
            'status': self.status_combo.currentText(),
            'role_id': self.role_combo.currentData(),
            'platform_id': self.platform_combo.currentData(),
            'primary_ip4': self.ip4_input.text().strip() or None,
            'primary_ip6': self.ip6_input.text().strip() or None,
            'oob_ip': self.oob_input.text().strip() or None,
            'ssh_port': self.ssh_port_input.value(),
            'credential_id': self.credential_input.value() or None,
            'serial_number': self.serial_input.text().strip() or None,
            'asset_tag': self.asset_tag_input.text().strip() or None,
            'description': self.description_input.toPlainText().strip() or None,
            'comments': self.comments_input.toPlainText().strip() or None,
        }
        return data

    def _on_save(self):
        """Handle save button click."""
        if not self.validate():
            return

        data = self._collect_form_data()

        try:
            if self.is_edit_mode:
                # Update existing device
                # Remove name and site_id from update (they're part of unique constraint)
                update_data = {k: v for k, v in data.items() if k not in ('name', 'site_id')}

                # Only update name/site if actually changed
                if data['name'] != self.device.name:
                    update_data['name'] = data['name']
                if data['site_id'] != self.device.site_id:
                    update_data['site_id'] = data['site_id']

                success = self.repo.update_device(self.device.id, **update_data)
                if success:
                    self.saved_device_id = self.device.id
                    self.device_saved.emit(self.device.id)
                    self.accept()
                else:
                    self.validation_label.setText("Failed to update device")
            else:
                # Create new device
                name = data.pop('name')
                site_id = data.pop('site_id')
                device_id = self.repo.create_device(name, site_id, **data)
                self.saved_device_id = device_id
                self.device_saved.emit(device_id)
                self.accept()

        except Exception as e:
            error_msg = str(e)
            if "UNIQUE constraint failed" in error_msg:
                if "asset_tag" in error_msg:
                    self.validation_label.setText("Asset tag already exists")
                elif "name" in error_msg:
                    self.validation_label.setText("Device name already exists at this site")
                else:
                    self.validation_label.setText("Duplicate value detected")
            else:
                self.validation_label.setText(f"Error saving device: {error_msg}")

    def _on_delete(self):
        """Handle delete button click."""
        if not self.device:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete device '{self.device.name}'?\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.repo.delete_device(self.device.id)
                if success:
                    self.saved_device_id = None  # Indicates deletion
                    self.accept()
                else:
                    self.validation_label.setText("Failed to delete device")
            except Exception as e:
                self.validation_label.setText(f"Error deleting device: {e}")


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    # Test with repository
    repo = DCIMRepository()

    # Test edit dialog with first device
    devices = repo.get_devices(limit=1)
    if devices:
        device = repo.get_device(device_id=devices[0].id)

        # Test detail dialog
        detail = DeviceDetailDialog(device)
        detail.edit_requested.connect(lambda id: print(f"Edit requested for device {id}"))

        if detail.exec() == QDialog.DialogCode.Accepted:
            print("Detail dialog closed")

        # Test edit dialog
        edit = DeviceEditDialog(repo, device=device)
        edit.device_saved.connect(lambda id: print(f"Device saved: {id}"))

        if edit.exec() == QDialog.DialogCode.Accepted:
            print(f"Edit dialog saved device: {edit.saved_device_id}")
    else:
        # Test create dialog
        create = DeviceEditDialog(repo, device=None)
        create.device_saved.connect(lambda id: print(f"New device created: {id}"))

        if create.exec() == QDialog.DialogCode.Accepted:
            print(f"Create dialog saved device: {create.saved_device_id}")

    repo.close()