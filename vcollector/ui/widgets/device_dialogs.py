"""
VelocityCollector Device Dialogs

Dialog windows for viewing and editing device records.
- DeviceDetailDialog: Read-only view of device details with credential status
- DeviceEditDialog: Create/Edit form with credential dropdown and test button
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QTextEdit, QDialogButtonBox, QPushButton,
    QMessageBox, QFrame, QGroupBox, QGridLayout, QWidget, QTabWidget,
    QProgressDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QIntValidator, QRegularExpressionValidator, QColor

from vcollector.dcim.dcim_repo import (
    DCIMRepository, Device, Site, Platform, DeviceRole, DeviceStatus
)

from typing import Optional, List
from datetime import datetime
import re


class CredentialTestThread(QThread):
    """Background thread for testing a single credential."""

    finished_test = pyqtSignal(bool, str)  # success, message

    def __init__(self, device: Device, credential_id: int, vault_password: str):
        super().__init__()
        self.device = device
        self.credential_id = credential_id
        self.vault_password = vault_password

    def run(self):
        try:
            from vcollector.vault.resolver import CredentialResolver
            from vcollector.ssh.client import SSHClient, SSHClientOptions

            resolver = CredentialResolver()
            if not resolver.unlock_vault(self.vault_password):
                self.finished_test.emit(False, "Invalid vault password")
                return

            try:
                # Get credentials list and find the one we need
                creds_list = resolver.list_credentials()
                cred_info = next((c for c in creds_list if c.id == self.credential_id), None)

                if not cred_info:
                    self.finished_test.emit(False, f"Credential ID {self.credential_id} not found")
                    return

                ssh_creds = resolver.get_ssh_credentials(credential_name=cred_info.name)
                if not ssh_creds:
                    self.finished_test.emit(False, "Failed to load credential")
                    return

                # Test connection
                host = self.device.primary_ip4
                port = self.device.ssh_port or 22

                options = SSHClientOptions(
                    host=host,
                    port=port,
                    username=ssh_creds.username,
                    password=ssh_creds.password,
                    key_content=ssh_creds.key_content,
                    key_password=ssh_creds.key_passphrase,
                    timeout=15,
                    shell_timeout=5,
                )

                client = SSHClient(options)
                client.connect()
                prompt = client.find_prompt()
                client.disconnect()

                self.finished_test.emit(True, f"Connection successful! Prompt: {prompt}")

            finally:
                resolver.lock_vault()

        except Exception as e:
            self.finished_test.emit(False, str(e))


class DeviceDetailDialog(QDialog):
    """
    Read-only dialog showing full device details.

    Used for quick inspection without risk of accidental edits.
    Now includes credential status information.
    """

    # Signal to request opening edit dialog
    edit_requested = pyqtSignal(int)  # device_id

    def __init__(self, device: Device, repo: Optional[DCIMRepository] = None, parent=None):
        super().__init__(parent)
        self.device = device
        self.repo = repo
        self.setWindowTitle(f"Device: {device.name}")
        self.setMinimumWidth(550)
        self.setMinimumHeight(450)
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

        # === Credentials Tab (NEW) ===
        cred_tab = QWidget()
        cred_layout = QFormLayout(cred_tab)
        cred_layout.setSpacing(8)
        cred_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Credential info
        cred_name = self._get_credential_name(self.device.credential_id)
        cred_layout.addRow("Assigned Credential:", self._label(cred_name))

        # Test result with color
        test_result = self.device.credential_test_result or "untested"
        test_label = self._cred_status_label(test_result)
        cred_layout.addRow("Test Result:", test_label)

        # Last tested
        cred_layout.addRow("Last Tested:",
                          self._label(self._format_timestamp(self.device.credential_tested_at)))

        cred_layout.addRow(self._separator())

        # Test button
        if self.device.credential_id and self.device.primary_ip4:
            test_btn = QPushButton("Test Credential Now")
            test_btn.clicked.connect(self._on_test_credential)
            cred_layout.addRow("", test_btn)

        tabs.addTab(cred_tab, "Credentials")

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

    def _get_credential_name(self, credential_id: Optional[int]) -> str:
        """Get credential name by ID."""
        if not credential_id:
            return "— None —"

        try:
            from vcollector.vault.resolver import CredentialResolver
            resolver = CredentialResolver()
            creds = resolver.list_credentials()
            for cred in creds:
                if cred.id == credential_id:
                    return f"{cred.name} (ID: {credential_id})"
            return f"ID: {credential_id} (not found)"
        except Exception:
            return f"ID: {credential_id}"

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

    def _cred_status_label(self, test_result: str) -> QLabel:
        """Create a colored credential status label."""
        display_text = test_result.title()
        if test_result == 'success':
            display_text = "✓ Success"
        elif test_result == 'failed':
            display_text = "✗ Failed"
        elif test_result == 'untested':
            display_text = "? Untested"

        label = QLabel(display_text)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        colors = {
            'success': '#2ecc71',
            'failed': '#e74c3c',
            'untested': '#95a5a6',
        }
        if test_result in colors:
            label.setStyleSheet(f"color: {colors[test_result]}; font-weight: bold;")

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

    def _on_test_credential(self):
        """Test the assigned credential."""
        from PyQt6.QtWidgets import QInputDialog, QLineEdit

        password, ok = QInputDialog.getText(
            self, "Vault Password",
            "Enter vault password to test credential:",
            QLineEdit.EchoMode.Password
        )

        if not ok or not password:
            return

        # Show progress
        progress = QProgressDialog("Testing credential...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        # Run test in background
        self._test_thread = CredentialTestThread(
            self.device, self.device.credential_id, password
        )
        self._test_thread.finished_test.connect(
            lambda success, msg: self._on_test_finished(success, msg, progress)
        )
        self._test_thread.start()

    def _on_test_finished(self, success: bool, message: str, progress: QProgressDialog):
        """Handle credential test completion."""
        progress.close()

        if success:
            QMessageBox.information(self, "Test Successful", message)
            # Update the device in database if repo available
            if self.repo:
                self.repo.update_device_credential_test(
                    self.device.id,
                    self.device.credential_id,
                    'success'
                )
        else:
            QMessageBox.warning(self, "Test Failed", f"Credential test failed:\n\n{message}")
            if self.repo:
                self.repo.update_device_credential_test(
                    self.device.id,
                    self.device.credential_id,
                    'failed'
                )


class DeviceEditDialog(QDialog):
    """
    Create/Edit dialog for device records.

    Now includes credential dropdown with test button.

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
        self._credentials: List = []  # CredentialInfo objects

        self.setWindowTitle("Edit Device" if self.is_edit_mode else "New Device")
        self.setMinimumWidth(520)
        self.setMinimumHeight(580)

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

        tabs.addTab(network_tab, "Network")

        # === Credentials Tab (NEW - replaces simple ID input) ===
        cred_tab = QWidget()
        cred_layout = QFormLayout(cred_tab)
        cred_layout.setSpacing(10)
        cred_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Credential dropdown
        cred_row = QHBoxLayout()
        self.credential_combo = QComboBox()
        self.credential_combo.setMinimumWidth(200)
        cred_row.addWidget(self.credential_combo, 1)

        # Test button
        self.test_cred_btn = QPushButton("Test")
        self.test_cred_btn.setFixedWidth(60)
        self.test_cred_btn.setToolTip("Test selected credential against this device")
        self.test_cred_btn.clicked.connect(self._on_test_credential)
        cred_row.addWidget(self.test_cred_btn)

        cred_layout.addRow("Credential:", cred_row)

        # Credential status display (edit mode only)
        self.cred_status_label = QLabel("")
        cred_layout.addRow("Status:", self.cred_status_label)

        # Last tested
        self.cred_tested_label = QLabel("—")
        cred_layout.addRow("Last Tested:", self.cred_tested_label)

        # Help text
        help_label = QLabel(
            "Select a credential for SSH authentication.\n"
            "Use 'Discover Credentials' in the Devices view to\n"
            "automatically find working credentials."
        )
        help_label.setStyleSheet("color: #888; font-style: italic;")
        cred_layout.addRow("", help_label)

        tabs.addTab(cred_tab, "Credentials")

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

        # Credentials (from vault)
        self._load_credentials()

    def _load_credentials(self):
        """Load credentials from vault (encrypted list only, no secrets)."""
        self.credential_combo.clear()
        self.credential_combo.addItem("— None (use job default) —", None)

        try:
            from vcollector.vault.resolver import CredentialResolver
            resolver = CredentialResolver()

            if resolver.is_initialized():
                self._credentials = resolver.list_credentials()

                for cred in self._credentials:
                    # Mark default credential
                    suffix = " (default)" if cred.is_default else ""
                    self.credential_combo.addItem(f"{cred.name}{suffix}", cred.id)
            else:
                # Vault not initialized
                self.credential_combo.addItem("— Vault not initialized —", None)
                self.test_cred_btn.setEnabled(False)

        except Exception as e:
            # If vault can't be accessed, still allow manual ID entry
            self.credential_combo.addItem(f"— Error loading: {e} —", None)

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

        # Credential
        if self.device.credential_id:
            cred_idx = self.credential_combo.findData(self.device.credential_id)
            if cred_idx >= 0:
                self.credential_combo.setCurrentIndex(cred_idx)
            else:
                # Credential not in list - add it manually
                self.credential_combo.addItem(f"ID: {self.device.credential_id}", self.device.credential_id)
                self.credential_combo.setCurrentIndex(self.credential_combo.count() - 1)

        # Credential status
        self._update_cred_status_display()

        # Hardware
        self.serial_input.setText(self.device.serial_number or "")
        self.asset_tag_input.setText(self.device.asset_tag or "")
        self.netbox_id_input.setText(str(self.device.netbox_id) if self.device.netbox_id else "")

        # Notes
        self.description_input.setPlainText(self.device.description or "")
        self.comments_input.setPlainText(self.device.comments or "")

    def _update_cred_status_display(self):
        """Update credential status labels."""
        if not self.device:
            self.cred_status_label.setText("—")
            self.cred_tested_label.setText("—")
            return

        # Test result
        test_result = self.device.credential_test_result or "untested"
        if test_result == 'success':
            self.cred_status_label.setText("✓ Success")
            self.cred_status_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        elif test_result == 'failed':
            self.cred_status_label.setText("✗ Failed")
            self.cred_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        else:
            self.cred_status_label.setText("? Untested")
            self.cred_status_label.setStyleSheet("color: #95a5a6;")

        # Last tested
        if self.device.credential_tested_at:
            try:
                dt = datetime.fromisoformat(self.device.credential_tested_at)
                self.cred_tested_label.setText(dt.strftime("%Y-%m-%d %H:%M"))
            except (ValueError, AttributeError):
                self.cred_tested_label.setText(self.device.credential_tested_at)
        else:
            self.cred_tested_label.setText("Never")

    def _on_test_credential(self):
        """Test the selected credential against this device."""
        credential_id = self.credential_combo.currentData()

        if not credential_id:
            QMessageBox.warning(
                self, "No Credential",
                "Please select a credential to test."
            )
            return

        # Need IP address for testing
        ip = self.ip4_input.text().strip()
        if not ip:
            QMessageBox.warning(
                self, "No IP Address",
                "Please enter a Primary IPv4 address to test connectivity."
            )
            return

        # Build temporary device object for testing
        test_device = Device(
            id=self.device.id if self.device else 0,
            name=self.name_input.text() or "test",
            primary_ip4=ip.split('/')[0],  # Strip CIDR if present
            ssh_port=self.ssh_port_input.value(),
        )

        # Get vault password
        from PyQt6.QtWidgets import QInputDialog, QLineEdit
        password, ok = QInputDialog.getText(
            self, "Vault Password",
            "Enter vault password to test credential:",
            QLineEdit.EchoMode.Password
        )

        if not ok or not password:
            return

        # Show progress
        progress = QProgressDialog("Testing credential...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        # Run test
        self._test_thread = CredentialTestThread(test_device, credential_id, password)
        self._test_thread.finished_test.connect(
            lambda success, msg: self._on_test_finished(success, msg, progress)
        )
        self._test_thread.start()

    def _on_test_finished(self, success: bool, message: str, progress: QProgressDialog):
        """Handle credential test completion."""
        progress.close()

        if success:
            QMessageBox.information(self, "Test Successful", message)
            # Update status display
            self.cred_status_label.setText("✓ Success")
            self.cred_status_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
            self.cred_tested_label.setText("Just now")
        else:
            QMessageBox.warning(self, "Test Failed", f"Credential test failed:\n\n{message}")
            self.cred_status_label.setText("✗ Failed")
            self.cred_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.cred_tested_label.setText("Just now")

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
            'credential_id': self.credential_combo.currentData(),
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
        detail = DeviceDetailDialog(device, repo)
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