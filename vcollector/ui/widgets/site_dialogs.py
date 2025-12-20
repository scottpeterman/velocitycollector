"""
VelocityCollector Site Dialogs

Dialog windows for viewing and editing site records.
- SiteDetailDialog: Read-only view of site details
- SiteEditDialog: Create/Edit form with validation
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QTextEdit, QPushButton, QMessageBox, QFrame, QWidget, QTabWidget
)
from PyQt6.QtCore import Qt, pyqtSignal

from vcollector.dcim.dcim_repo import DCIMRepository, Site

from typing import Optional, List
from datetime import datetime
import re


# Site status options (matches dcim_site table)
SITE_STATUSES = ['active', 'planned', 'staging', 'decommissioning', 'retired']

# Common time zones for network infrastructure
COMMON_TIMEZONES = [
    "",  # None/default
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
    "Pacific/Honolulu",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Asia/Singapore",
    "Asia/Hong_Kong",
    "Australia/Sydney",
]


class SiteDetailDialog(QDialog):
    """
    Read-only dialog showing full site details.

    Used for quick inspection without risk of accidental edits.
    """

    # Signal to request opening edit dialog
    edit_requested = pyqtSignal(int)  # site_id

    def __init__(self, site: Site, parent=None):
        super().__init__(parent)
        self.site = site
        self.setWindowTitle(f"Site: {site.name}")
        self.setMinimumWidth(450)
        self.setMinimumHeight(350)
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
        general_layout.addRow("Name:", self._label(self.site.name))
        general_layout.addRow("Slug:", self._label(self.site.slug))
        general_layout.addRow("Status:", self._status_label(self.site.status))

        # Add separator
        general_layout.addRow(self._separator())

        # Location/Facility
        general_layout.addRow("Facility:", self._label(self.site.facility))
        general_layout.addRow("Time Zone:", self._label(self.site.time_zone))
        general_layout.addRow("Address:", self._label(self.site.physical_address))

        tabs.addTab(general_tab, "General")

        # === Metadata Tab ===
        meta_tab = QWidget()
        meta_layout = QVBoxLayout(meta_tab)

        # Timestamps
        time_form = QFormLayout()
        time_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        time_form.addRow("Created:", self._label(self._format_timestamp(self.site.created_at)))
        time_form.addRow("Updated:", self._label(self._format_timestamp(self.site.updated_at)))
        time_form.addRow("NetBox ID:", self._label(str(self.site.netbox_id) if self.site.netbox_id else None))

        # Device count if available
        device_count = getattr(self.site, 'device_count', None)
        if device_count is not None:
            time_form.addRow("Device Count:", self._label(str(device_count)))

        meta_layout.addLayout(time_form)

        # Description
        if self.site.description:
            meta_layout.addWidget(QLabel("Description:"))
            desc = QTextEdit()
            desc.setPlainText(self.site.description)
            desc.setReadOnly(True)
            desc.setMaximumHeight(100)
            meta_layout.addWidget(desc)

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
            'staging': '#f39c12',
            'decommissioning': '#9b59b6',
            'retired': '#7f8c8d',
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
        self.edit_requested.emit(self.site.id)
        self.accept()


class SiteEditDialog(QDialog):
    """
    Create/Edit dialog for site records.

    Usage:
        # Edit existing site
        dialog = SiteEditDialog(repo, site=existing_site)

        # Create new site
        dialog = SiteEditDialog(repo, site=None)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Site was saved
            site_id = dialog.saved_site_id
    """

    # Signal emitted when site is saved (new or updated)
    site_saved = pyqtSignal(int)  # site_id

    def __init__(self, repo: DCIMRepository, site: Optional[Site] = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.site = site
        self.is_edit_mode = site is not None
        self.saved_site_id: Optional[int] = None

        self.setWindowTitle("Edit Site" if self.is_edit_mode else "New Site")
        self.setMinimumWidth(450)
        self.setMinimumHeight(400)

        self.init_ui()

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
        self.name_input.setPlaceholderText("Site name (required)")
        self.name_input.setMaxLength(100)
        self.name_input.textChanged.connect(self._auto_generate_slug)
        identity_layout.addRow("Name: *", self.name_input)

        # Slug (required, auto-generated)
        self.slug_input = QLineEdit()
        self.slug_input.setPlaceholderText("URL-friendly identifier (auto-generated)")
        self.slug_input.setMaxLength(100)
        identity_layout.addRow("Slug: *", self.slug_input)

        # Status
        self.status_combo = QComboBox()
        self.status_combo.addItems(SITE_STATUSES)
        identity_layout.addRow("Status:", self.status_combo)

        tabs.addTab(identity_tab, "Identity")

        # === Location Tab ===
        location_tab = QWidget()
        location_layout = QFormLayout(location_tab)
        location_layout.setSpacing(10)
        location_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Facility
        self.facility_input = QLineEdit()
        self.facility_input.setPlaceholderText("Data center or facility code")
        location_layout.addRow("Facility:", self.facility_input)

        # Time Zone
        self.timezone_combo = QComboBox()
        self.timezone_combo.setEditable(True)  # Allow custom entry
        self.timezone_combo.addItems(COMMON_TIMEZONES)
        self.timezone_combo.setCurrentIndex(0)
        location_layout.addRow("Time Zone:", self.timezone_combo)

        # Physical Address
        self.address_input = QTextEdit()
        self.address_input.setPlaceholderText("Physical address")
        self.address_input.setMaximumHeight(80)
        location_layout.addRow("Address:", self.address_input)

        tabs.addTab(location_tab, "Location")

        # === Notes Tab ===
        notes_tab = QWidget()
        notes_layout = QVBoxLayout(notes_tab)

        notes_layout.addWidget(QLabel("Description:"))
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("Brief description of the site")
        notes_layout.addWidget(self.description_input)

        # NetBox ID (read-only in edit, hidden in create)
        if self.is_edit_mode:
            netbox_layout = QFormLayout()
            self.netbox_id_label = QLabel()
            netbox_layout.addRow("NetBox ID:", self.netbox_id_label)
            notes_layout.addLayout(netbox_layout)

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

    def _auto_generate_slug(self, name: str):
        """Auto-generate slug from name (only if slug is empty or matches previous auto-slug)."""
        if not self.is_edit_mode or not self.slug_input.text():
            slug = self._slugify(name)
            self.slug_input.setText(slug)

    def _slugify(self, text: str) -> str:
        """Convert text to URL-friendly slug."""
        # Lowercase
        slug = text.lower().strip()
        # Replace spaces and special chars with hyphens
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        # Remove leading/trailing hyphens
        slug = slug.strip('-')
        # Collapse multiple hyphens
        slug = re.sub(r'-+', '-', slug)
        return slug

    def populate_form(self):
        """Populate form fields from existing site."""
        if not self.site:
            return

        # Identity
        self.name_input.setText(self.site.name or "")
        self.slug_input.setText(self.site.slug or "")

        # Status
        status_idx = self.status_combo.findText(self.site.status or "active")
        if status_idx >= 0:
            self.status_combo.setCurrentIndex(status_idx)

        # Location
        self.facility_input.setText(self.site.facility or "")

        # Time zone - try to find in list, otherwise set as text
        tz = self.site.time_zone or ""
        tz_idx = self.timezone_combo.findText(tz)
        if tz_idx >= 0:
            self.timezone_combo.setCurrentIndex(tz_idx)
        else:
            self.timezone_combo.setCurrentText(tz)

        self.address_input.setPlainText(self.site.physical_address or "")

        # Notes
        self.description_input.setPlainText(self.site.description or "")

        # NetBox ID
        if self.is_edit_mode:
            self.netbox_id_label.setText(str(self.site.netbox_id) if self.site.netbox_id else "—")

    def validate(self) -> bool:
        """Validate form inputs. Returns True if valid."""
        errors = []

        # Name required
        name = self.name_input.text().strip()
        if not name:
            errors.append("Site name is required")
        elif len(name) > 100:
            errors.append("Site name must be 100 characters or less")

        # Slug required
        slug = self.slug_input.text().strip()
        if not slug:
            errors.append("Slug is required")
        elif len(slug) > 100:
            errors.append("Slug must be 100 characters or less")
        elif not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$', slug):
            errors.append("Slug must contain only lowercase letters, numbers, and hyphens")

        if errors:
            self.validation_label.setText("• " + "\n• ".join(errors))
            return False

        self.validation_label.setText("")
        return True

    def _collect_form_data(self) -> dict:
        """Collect form data into a dict for save."""
        data = {
            'name': self.name_input.text().strip(),
            'slug': self.slug_input.text().strip(),
            'status': self.status_combo.currentText(),
            'facility': self.facility_input.text().strip() or None,
            'time_zone': self.timezone_combo.currentText().strip() or None,
            'physical_address': self.address_input.toPlainText().strip() or None,
            'description': self.description_input.toPlainText().strip() or None,
        }
        return data

    def _on_save(self):
        """Handle save button click."""
        if not self.validate():
            return

        data = self._collect_form_data()

        try:
            if self.is_edit_mode:
                # Update existing site
                success = self.repo.update_site(self.site.id, **data)
                if success:
                    self.saved_site_id = self.site.id
                    self.site_saved.emit(self.site.id)
                    self.accept()
                else:
                    self.validation_label.setText("Failed to update site")
            else:
                # Create new site
                name = data.pop('name')
                slug = data.pop('slug')
                site_id = self.repo.create_site(name, slug, **data)
                self.saved_site_id = site_id
                self.site_saved.emit(site_id)
                self.accept()

        except Exception as e:
            error_msg = str(e)
            if "UNIQUE constraint failed" in error_msg:
                if "slug" in error_msg:
                    self.validation_label.setText("A site with this slug already exists")
                elif "name" in error_msg:
                    self.validation_label.setText("A site with this name already exists")
                else:
                    self.validation_label.setText("Duplicate value detected")
            else:
                self.validation_label.setText(f"Error saving site: {error_msg}")

    def _on_delete(self):
        """Handle delete button click."""
        if not self.site:
            return

        # Check device count
        device_count = getattr(self.site, 'device_count', 0) or 0

        warning_msg = f"Are you sure you want to delete site '{self.site.name}'?"
        if device_count > 0:
            warning_msg += f"\n\nWARNING: This site has {device_count} device(s) that will also be deleted!"
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
                success = self.repo.delete_site(self.site.id)
                if success:
                    self.saved_site_id = None  # Indicates deletion
                    self.accept()
                else:
                    self.validation_label.setText("Failed to delete site")
            except Exception as e:
                self.validation_label.setText(f"Error deleting site: {e}")


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    # Test with repository
    repo = DCIMRepository()

    # Test edit dialog with first site
    sites = repo.get_sites()
    if sites:
        site = repo.get_site(site_id=sites[0].id)

        # Test detail dialog
        detail = SiteDetailDialog(site)
        detail.edit_requested.connect(lambda id: print(f"Edit requested for site {id}"))

        if detail.exec() == QDialog.DialogCode.Accepted:
            print("Detail dialog closed")

        # Test edit dialog
        edit = SiteEditDialog(repo, site=site)
        edit.site_saved.connect(lambda id: print(f"Site saved: {id}"))

        if edit.exec() == QDialog.DialogCode.Accepted:
            print(f"Edit dialog saved site: {edit.saved_site_id}")
    else:
        # Test create dialog
        create = SiteEditDialog(repo, site=None)
        create.site_saved.connect(lambda id: print(f"New site created: {id}"))

        if create.exec() == QDialog.DialogCode.Accepted:
            print(f"Create dialog saved site: {create.saved_site_id}")

    repo.close()