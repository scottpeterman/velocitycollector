"""
VelocityCollector Platform and Role Dialogs

Dialog windows for viewing and editing platform and device role records.
- PlatformDetailDialog: Read-only view of platform details
- PlatformEditDialog: Create/Edit form for platforms
- RoleDetailDialog: Read-only view of role details
- RoleEditDialog: Create/Edit form for roles
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QTextEdit, QPushButton, QMessageBox, QFrame, QWidget,
    QColorDialog, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

from vcollector.dcim.dcim_repo import DCIMRepository, Platform, DeviceRole, Manufacturer

from typing import Optional, List
from datetime import datetime
import re


# Common Netmiko device types for dropdown
NETMIKO_DEVICE_TYPES = [
    "",
    "cisco_ios",
    "cisco_xe",
    "cisco_xr",
    "cisco_nxos",
    "cisco_asa",
    "arista_eos",
    "juniper_junos",
    "hp_procurve",
    "hp_comware",
    "dell_force10",
    "dell_os10",
    "brocade_fastiron",
    "brocade_netiron",
    "extreme_exos",
    "fortinet",
    "paloalto_panos",
    "checkpoint_gaia",
    "linux",
    "generic_termserver",
]

# Common paging disable commands
PAGING_COMMANDS = [
    "",
    "terminal length 0",
    "terminal pager 0",
    "set cli screen-length 0",
    "screen-length 0 temporary",
    "no page",
]

# Preset colors for roles (NetBox-style)
ROLE_COLORS = [
    ("Gray", "9e9e9e"),
    ("Red", "e74c3c"),
    ("Orange", "f39c12"),
    ("Yellow", "f1c40f"),
    ("Green", "2ecc71"),
    ("Teal", "1abc9c"),
    ("Cyan", "00bcd4"),
    ("Blue", "3498db"),
    ("Indigo", "3f51b5"),
    ("Purple", "9b59b6"),
    ("Pink", "e91e63"),
    ("Brown", "795548"),
]


class PlatformDetailDialog(QDialog):
    """
    Read-only dialog showing full platform details.
    """

    edit_requested = pyqtSignal(int)  # platform_id

    def __init__(self, platform: Platform, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.setWindowTitle(f"Platform: {platform.name}")
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Form layout for details
        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Identity
        form.addRow("Name:", self._label(self.platform.name))
        form.addRow("Slug:", self._label(self.platform.slug))
        form.addRow("Manufacturer:", self._label(self.platform.manufacturer_name))

        form.addRow(self._separator())

        # Collection settings
        form.addRow("Netmiko Type:", self._label(self.platform.netmiko_device_type))
        form.addRow("Paging Command:", self._label(self.platform.paging_disable_command))

        form.addRow(self._separator())

        # Metadata
        form.addRow("NetBox ID:", self._label(str(self.platform.netbox_id) if self.platform.netbox_id else None))
        form.addRow("Created:", self._label(self._format_timestamp(self.platform.created_at)))
        form.addRow("Updated:", self._label(self._format_timestamp(self.platform.updated_at)))

        # Device count if available
        device_count = getattr(self.platform, 'device_count', None)
        if device_count is not None:
            form.addRow("Device Count:", self._label(str(device_count)))

        layout.addLayout(form)

        # Description
        if self.platform.description:
            layout.addWidget(QLabel("Description:"))
            desc = QTextEdit()
            desc.setPlainText(self.platform.description)
            desc.setReadOnly(True)
            desc.setMaximumHeight(80)
            layout.addWidget(desc)

        layout.addStretch()

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
        label = QLabel(text or "—")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _format_timestamp(self, ts: Optional[str]) -> str:
        if not ts:
            return "—"
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            return ts

    def _on_edit(self):
        self.edit_requested.emit(self.platform.id)
        self.accept()


class PlatformEditDialog(QDialog):
    """
    Create/Edit dialog for platform records.
    """

    platform_saved = pyqtSignal(int)  # platform_id

    def __init__(self, repo: DCIMRepository, platform: Optional[Platform] = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.platform = platform
        self.is_edit_mode = platform is not None
        self.saved_platform_id: Optional[int] = None

        self._manufacturers: List[Manufacturer] = []

        self.setWindowTitle("Edit Platform" if self.is_edit_mode else "New Platform")
        self.setMinimumWidth(450)
        self.setMinimumHeight(400)

        self.init_ui()
        self.load_lookups()

        if self.is_edit_mode:
            self.populate_form()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Form layout
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Name (required)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Platform name (required)")
        self.name_input.setMaxLength(100)
        self.name_input.textChanged.connect(self._auto_generate_slug)
        form.addRow("Name: *", self.name_input)

        # Slug (required, auto-generated)
        self.slug_input = QLineEdit()
        self.slug_input.setPlaceholderText("URL-friendly identifier (auto-generated)")
        self.slug_input.setMaxLength(100)
        form.addRow("Slug: *", self.slug_input)

        # Manufacturer
        self.manufacturer_combo = QComboBox()
        form.addRow("Manufacturer:", self.manufacturer_combo)

        form.addRow(self._separator())

        # Netmiko device type
        self.netmiko_combo = QComboBox()
        self.netmiko_combo.setEditable(True)
        self.netmiko_combo.addItems(NETMIKO_DEVICE_TYPES)
        form.addRow("Netmiko Type:", self.netmiko_combo)

        # Paging disable command
        self.paging_combo = QComboBox()
        self.paging_combo.setEditable(True)
        self.paging_combo.addItems(PAGING_COMMANDS)
        form.addRow("Paging Command:", self.paging_combo)

        layout.addLayout(form)

        # Description
        layout.addWidget(QLabel("Description:"))
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("Brief description of the platform")
        self.description_input.setMaximumHeight(80)
        layout.addWidget(self.description_input)

        layout.addStretch()

        # Validation message
        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #e74c3c;")
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)

        # Button row
        button_layout = QHBoxLayout()

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

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _auto_generate_slug(self, name: str):
        if not self.is_edit_mode or not self.slug_input.text():
            slug = self._slugify(name)
            self.slug_input.setText(slug)

    def _slugify(self, text: str) -> str:
        slug = text.lower().strip()
        # Use underscore for platform slugs (common convention)
        slug = re.sub(r'[^a-z0-9]+', '_', slug)
        slug = slug.strip('_')
        slug = re.sub(r'_+', '_', slug)
        return slug

    def load_lookups(self):
        """Load manufacturer dropdown."""
        self._manufacturers = self.repo.get_manufacturers()
        self.manufacturer_combo.clear()
        self.manufacturer_combo.addItem("— None —", None)
        for mfg in self._manufacturers:
            self.manufacturer_combo.addItem(mfg.name, mfg.id)

    def populate_form(self):
        """Populate form from existing platform."""
        if not self.platform:
            return

        self.name_input.setText(self.platform.name or "")
        self.slug_input.setText(self.platform.slug or "")

        # Manufacturer
        mfg_idx = self.manufacturer_combo.findData(self.platform.manufacturer_id)
        if mfg_idx >= 0:
            self.manufacturer_combo.setCurrentIndex(mfg_idx)

        # Netmiko type
        netmiko = self.platform.netmiko_device_type or ""
        idx = self.netmiko_combo.findText(netmiko)
        if idx >= 0:
            self.netmiko_combo.setCurrentIndex(idx)
        else:
            self.netmiko_combo.setCurrentText(netmiko)

        # Paging command
        paging = self.platform.paging_disable_command or ""
        idx = self.paging_combo.findText(paging)
        if idx >= 0:
            self.paging_combo.setCurrentIndex(idx)
        else:
            self.paging_combo.setCurrentText(paging)

        self.description_input.setPlainText(self.platform.description or "")

    def validate(self) -> bool:
        errors = []

        name = self.name_input.text().strip()
        if not name:
            errors.append("Platform name is required")

        slug = self.slug_input.text().strip()
        if not slug:
            errors.append("Slug is required")
        elif not re.match(r'^[a-z0-9][a-z0-9_]*[a-z0-9]$|^[a-z0-9]$', slug):
            errors.append("Slug must contain only lowercase letters, numbers, and underscores")

        if errors:
            self.validation_label.setText("• " + "\n• ".join(errors))
            return False

        self.validation_label.setText("")
        return True

    def _collect_form_data(self) -> dict:
        return {
            'name': self.name_input.text().strip(),
            'slug': self.slug_input.text().strip(),
            'manufacturer_id': self.manufacturer_combo.currentData(),
            'netmiko_device_type': self.netmiko_combo.currentText().strip() or None,
            'paging_disable_command': self.paging_combo.currentText().strip() or None,
            'description': self.description_input.toPlainText().strip() or None,
        }

    def _on_save(self):
        if not self.validate():
            return

        data = self._collect_form_data()

        try:
            if self.is_edit_mode:
                success = self.repo.update_platform(self.platform.id, **data)
                if success:
                    self.saved_platform_id = self.platform.id
                    self.platform_saved.emit(self.platform.id)
                    self.accept()
                else:
                    self.validation_label.setText("Failed to update platform")
            else:
                name = data.pop('name')
                slug = data.pop('slug')
                platform_id = self.repo.create_platform(name, slug, **data)
                self.saved_platform_id = platform_id
                self.platform_saved.emit(platform_id)
                self.accept()

        except Exception as e:
            error_msg = str(e)
            if "UNIQUE constraint failed" in error_msg:
                self.validation_label.setText("A platform with this name or slug already exists")
            else:
                self.validation_label.setText(f"Error saving platform: {error_msg}")

    def _on_delete(self):
        if not self.platform:
            return

        device_count = getattr(self.platform, 'device_count', 0) or 0

        warning_msg = f"Are you sure you want to delete platform '{self.platform.name}'?"
        if device_count > 0:
            warning_msg += f"\n\nWARNING: {device_count} device(s) use this platform."
        warning_msg += "\n\nThis action cannot be undone."

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            warning_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.repo.delete_platform(self.platform.id)
                if success:
                    self.saved_platform_id = None
                    self.accept()
                else:
                    self.validation_label.setText("Failed to delete platform")
            except Exception as e:
                self.validation_label.setText(f"Error deleting platform: {e}")


class RoleDetailDialog(QDialog):
    """
    Read-only dialog showing full role details.
    """

    edit_requested = pyqtSignal(int)  # role_id

    def __init__(self, role: DeviceRole, parent=None):
        super().__init__(parent)
        self.role = role
        self.setWindowTitle(f"Role: {role.name}")
        self.setMinimumWidth(350)
        self.setMinimumHeight(250)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        form.addRow("Name:", self._label(self.role.name))
        form.addRow("Slug:", self._label(self.role.slug))

        # Color with visual indicator
        color = self.role.color or "9e9e9e"
        color_label = QLabel(f"#{color}")
        color_label.setStyleSheet(f"color: #{color}; font-weight: bold;")
        form.addRow("Color:", color_label)

        form.addRow(self._separator())

        form.addRow("NetBox ID:", self._label(str(self.role.netbox_id) if self.role.netbox_id else None))
        form.addRow("Created:", self._label(self._format_timestamp(self.role.created_at)))
        form.addRow("Updated:", self._label(self._format_timestamp(self.role.updated_at)))

        device_count = getattr(self.role, 'device_count', None)
        if device_count is not None:
            form.addRow("Device Count:", self._label(str(device_count)))

        layout.addLayout(form)

        if self.role.description:
            layout.addWidget(QLabel("Description:"))
            desc = QTextEdit()
            desc.setPlainText(self.role.description)
            desc.setReadOnly(True)
            desc.setMaximumHeight(80)
            layout.addWidget(desc)

        layout.addStretch()

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
        label = QLabel(text or "—")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _format_timestamp(self, ts: Optional[str]) -> str:
        if not ts:
            return "—"
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            return ts

    def _on_edit(self):
        self.edit_requested.emit(self.role.id)
        self.accept()


class RoleEditDialog(QDialog):
    """
    Create/Edit dialog for device role records.
    """

    role_saved = pyqtSignal(int)  # role_id

    def __init__(self, repo: DCIMRepository, role: Optional[DeviceRole] = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.role = role
        self.is_edit_mode = role is not None
        self.saved_role_id: Optional[int] = None

        self.setWindowTitle("Edit Role" if self.is_edit_mode else "New Role")
        self.setMinimumWidth(400)
        self.setMinimumHeight(350)

        self.init_ui()

        if self.is_edit_mode:
            self.populate_form()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Name (required)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Role name (required)")
        self.name_input.setMaxLength(100)
        self.name_input.textChanged.connect(self._auto_generate_slug)
        form.addRow("Name: *", self.name_input)

        # Slug (required)
        self.slug_input = QLineEdit()
        self.slug_input.setPlaceholderText("URL-friendly identifier (auto-generated)")
        self.slug_input.setMaxLength(100)
        form.addRow("Slug: *", self.slug_input)

        layout.addLayout(form)

        # Color picker section
        color_group = QWidget()
        color_layout = QVBoxLayout(color_group)
        color_layout.setContentsMargins(0, 0, 0, 0)

        color_layout.addWidget(QLabel("Color:"))

        # Color preview and custom picker
        color_row = QHBoxLayout()

        self.color_preview = QLabel("  ")
        self.color_preview.setFixedSize(30, 30)
        self.color_preview.setStyleSheet("background-color: #9e9e9e; border: 1px solid #666;")
        color_row.addWidget(self.color_preview)

        self.color_input = QLineEdit()
        self.color_input.setPlaceholderText("9e9e9e")
        self.color_input.setMaxLength(6)
        self.color_input.textChanged.connect(self._update_color_preview)
        color_row.addWidget(self.color_input)

        custom_color_btn = QPushButton("Pick Color")
        custom_color_btn.setProperty("secondary", True)
        custom_color_btn.clicked.connect(self._pick_custom_color)
        color_row.addWidget(custom_color_btn)

        color_row.addStretch()
        color_layout.addLayout(color_row)

        # Preset colors grid
        preset_grid = QGridLayout()
        preset_grid.setSpacing(4)
        for i, (name, hex_color) in enumerate(ROLE_COLORS):
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(f"background-color: #{hex_color}; border: 1px solid #666;")
            btn.setToolTip(name)
            btn.clicked.connect(lambda checked, c=hex_color: self._set_color(c))
            preset_grid.addWidget(btn, i // 6, i % 6)
        color_layout.addLayout(preset_grid)

        layout.addWidget(color_group)

        # Description
        layout.addWidget(QLabel("Description:"))
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("Brief description of the role")
        self.description_input.setMaximumHeight(80)
        layout.addWidget(self.description_input)

        layout.addStretch()

        # Validation message
        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #e74c3c;")
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)

        # Button row
        button_layout = QHBoxLayout()

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

    def _auto_generate_slug(self, name: str):
        if not self.is_edit_mode or not self.slug_input.text():
            slug = self._slugify(name)
            self.slug_input.setText(slug)

    def _slugify(self, text: str) -> str:
        slug = text.lower().strip()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        slug = re.sub(r'-+', '-', slug)
        return slug

    def _set_color(self, hex_color: str):
        """Set the color from preset or picker."""
        self.color_input.setText(hex_color)
        self._update_color_preview(hex_color)

    def _update_color_preview(self, text: str):
        """Update the color preview square."""
        color = text.strip()
        if len(color) == 6 and all(c in '0123456789abcdefABCDEF' for c in color):
            self.color_preview.setStyleSheet(f"background-color: #{color}; border: 1px solid #666;")
        else:
            self.color_preview.setStyleSheet("background-color: #9e9e9e; border: 1px solid #666;")

    def _pick_custom_color(self):
        """Open color picker dialog."""
        current = self.color_input.text().strip()
        initial = QColor(f"#{current}") if current else QColor("#9e9e9e")

        color = QColorDialog.getColor(initial, self, "Select Role Color")
        if color.isValid():
            hex_color = color.name()[1:]  # Remove # prefix
            self._set_color(hex_color)

    def populate_form(self):
        """Populate form from existing role."""
        if not self.role:
            return

        self.name_input.setText(self.role.name or "")
        self.slug_input.setText(self.role.slug or "")

        color = self.role.color or "9e9e9e"
        self.color_input.setText(color)
        self._update_color_preview(color)

        self.description_input.setPlainText(self.role.description or "")

    def validate(self) -> bool:
        errors = []

        name = self.name_input.text().strip()
        if not name:
            errors.append("Role name is required")

        slug = self.slug_input.text().strip()
        if not slug:
            errors.append("Slug is required")
        elif not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$', slug):
            errors.append("Slug must contain only lowercase letters, numbers, and hyphens")

        color = self.color_input.text().strip()
        if color and (len(color) != 6 or not all(c in '0123456789abcdefABCDEF' for c in color)):
            errors.append("Color must be a 6-character hex code (e.g., 2ecc71)")

        if errors:
            self.validation_label.setText("• " + "\n• ".join(errors))
            return False

        self.validation_label.setText("")
        return True

    def _collect_form_data(self) -> dict:
        return {
            'name': self.name_input.text().strip(),
            'slug': self.slug_input.text().strip(),
            'color': self.color_input.text().strip() or "9e9e9e",
            'description': self.description_input.toPlainText().strip() or None,
        }

    def _on_save(self):
        if not self.validate():
            return

        data = self._collect_form_data()

        try:
            if self.is_edit_mode:
                success = self.repo.update_device_role(self.role.id, **data)
                if success:
                    self.saved_role_id = self.role.id
                    self.role_saved.emit(self.role.id)
                    self.accept()
                else:
                    self.validation_label.setText("Failed to update role")
            else:
                name = data.pop('name')
                slug = data.pop('slug')
                role_id = self.repo.create_device_role(name, slug, **data)
                self.saved_role_id = role_id
                self.role_saved.emit(role_id)
                self.accept()

        except Exception as e:
            error_msg = str(e)
            if "UNIQUE constraint failed" in error_msg:
                self.validation_label.setText("A role with this name or slug already exists")
            else:
                self.validation_label.setText(f"Error saving role: {error_msg}")

    def _on_delete(self):
        if not self.role:
            return

        device_count = getattr(self.role, 'device_count', 0) or 0

        warning_msg = f"Are you sure you want to delete role '{self.role.name}'?"
        if device_count > 0:
            warning_msg += f"\n\nWARNING: {device_count} device(s) use this role."
        warning_msg += "\n\nThis action cannot be undone."

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            warning_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.repo.delete_device_role(self.role.id)
                if success:
                    self.saved_role_id = None
                    self.accept()
                else:
                    self.validation_label.setText("Failed to delete role")
            except Exception as e:
                self.validation_label.setText(f"Error deleting role: {e}")


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    repo = DCIMRepository()

    # Test role edit dialog
    roles = repo.get_device_roles()
    if roles:
        role = repo.get_device_role(role_id=roles[0].id)
        dialog = RoleEditDialog(repo, role=role)
        dialog.role_saved.connect(lambda id: print(f"Role saved: {id}"))
        dialog.exec()
    else:
        # Test create
        dialog = RoleEditDialog(repo, role=None)
        dialog.role_saved.connect(lambda id: print(f"Role created: {id}"))
        dialog.exec()

    repo.close()