"""
VelocityCollector Devices View

Device inventory view with live data from VelocityCollector DCIM database.
Provides search, filtering, and device management operations.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QAbstractItemView,
    QMenu, QMessageBox, QDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QShortcut, QKeySequence

from vcollector.dcim.dcim_repo import DCIMRepository,Device
from vcollector.ui.widgets.stat_cards import StatCard
from vcollector.ui.widgets.device_dialogs import DeviceDetailDialog, DeviceEditDialog

from typing import Optional, List
from datetime import datetime


class DevicesView(QWidget):
    """Device inventory view - reads from VelocityCollector DCIM database."""

    # Signals for external integration
    device_selected = pyqtSignal(int)  # Emits device_id
    device_double_clicked = pyqtSignal(int)  # Emits device_id for action (SSH, etc.)

    def __init__(self, repo: Optional[DCIMRepository] = None, parent=None):
        super().__init__(parent)
        self.repo = repo or DCIMRepository()
        self._devices: List[Device] = []
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)

        self.init_ui()
        self.init_shortcuts()
        self.load_filters()
        self.refresh_data()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Device Inventory")
        title.setProperty("heading", True)
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Add Device button
        add_btn = QPushButton("+ Add Device")
        add_btn.clicked.connect(self._on_add_device)
        header_layout.addWidget(add_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh_data)
        header_layout.addWidget(refresh_btn)

        layout.addLayout(header_layout)

        # Stats row
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        self.total_devices = StatCard("Total Devices", "0")
        stats_layout.addWidget(self.total_devices)

        self.active_devices = StatCard("Active", "0")
        stats_layout.addWidget(self.active_devices)

        self.sites_count = StatCard("Sites", "0")
        stats_layout.addWidget(self.sites_count)

        self.manufacturers_count = StatCard("Manufacturers", "0")
        stats_layout.addWidget(self.manufacturers_count)

        self.platforms_count = StatCard("Platforms", "0")
        stats_layout.addWidget(self.platforms_count)

        stats_layout.addStretch()
        layout.addLayout(stats_layout)

        # Search and filter bar
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search devices by name, IP, description...")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._do_search)
        filter_layout.addWidget(self.search_input, stretch=2)

        self.site_filter = QComboBox()
        self.site_filter.setMinimumWidth(150)
        self.site_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.site_filter)

        self.manufacturer_filter = QComboBox()
        self.manufacturer_filter.setMinimumWidth(150)
        self.manufacturer_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.manufacturer_filter)

        self.status_filter = QComboBox()
        self.status_filter.addItems([
            "All Status", "active", "planned", "staged",
            "failed", "offline", "decommissioning", "inventory"
        ])
        self.status_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.status_filter)

        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("secondary", True)
        clear_btn.clicked.connect(self._clear_filters)
        filter_layout.addWidget(clear_btn)

        layout.addLayout(filter_layout)

        # Device table
        self.device_table = QTableWidget()
        self.device_table.setColumnCount(8)
        self.device_table.setHorizontalHeaderLabels([
            "Name", "IP Address", "Site", "Manufacturer", "Platform",
            "Role", "Status", "Last Collected"
        ])
        self.device_table.setAlternatingRowColors(True)
        self.device_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.device_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.device_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.device_table.horizontalHeader().setStretchLastSection(True)
        self.device_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.device_table.verticalHeader().setVisible(False)
        self.device_table.setSortingEnabled(True)

        # Context menu
        self.device_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.device_table.customContextMenuRequested.connect(self._show_context_menu)

        # Double-click handler
        self.device_table.doubleClicked.connect(self._on_double_click)

        # Selection changed
        self.device_table.itemSelectionChanged.connect(self._on_selection_changed)

        layout.addWidget(self.device_table)

        # Status bar
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

    def init_shortcuts(self):
        """Set up keyboard shortcuts."""
        # Ctrl+N - New device
        QShortcut(QKeySequence("Ctrl+N"), self, self._on_add_device)

        # Enter - Edit selected
        QShortcut(QKeySequence("Return"), self, self._edit_selected)

        # Delete - Delete selected
        QShortcut(QKeySequence("Delete"), self, self._delete_selected)

        # Ctrl+F - Focus search
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.search_input.setFocus())

        # F5 - Refresh
        QShortcut(QKeySequence("F5"), self, self.refresh_data)

    def load_filters(self):
        """Load filter dropdown options from database."""
        # Sites
        self.site_filter.clear()
        self.site_filter.addItem("All Sites", None)
        sites = self.repo.get_sites()
        for site in sites:
            self.site_filter.addItem(site.name, site.slug)

        # Manufacturers
        self.manufacturer_filter.clear()
        self.manufacturer_filter.addItem("All Manufacturers", None)
        manufacturers = self.repo.get_manufacturers()
        for mfg in manufacturers:
            self.manufacturer_filter.addItem(mfg.name, mfg.slug)

    def refresh_data(self):
        """Refresh stats, filters, and device list."""
        self.load_filters()
        self._update_stats()
        self._load_devices()

    def _update_stats(self):
        """Update stat cards from database."""
        try:
            stats = self.repo.get_stats()
            self.total_devices.set_value(str(stats.get('total_devices', 0)))
            self.active_devices.set_value(str(stats.get('active_devices', 0)))
            self.sites_count.set_value(str(stats.get('total_sites', 0)))
            self.manufacturers_count.set_value(str(stats.get('total_manufacturers', 0)))
            self.platforms_count.set_value(str(stats.get('total_platforms', 0)))
        except Exception as e:
            self.status_label.setText(f"Error loading stats: {e}")

    def _load_devices(self):
        """Load devices with current filters applied."""
        try:
            # Build filter kwargs
            kwargs = {}

            # Site filter
            site_slug = self.site_filter.currentData()
            if site_slug:
                kwargs['site_slug'] = site_slug

            # Manufacturer filter - need to get platforms for this manufacturer
            mfg_slug = self.manufacturer_filter.currentData()

            # Status filter
            status_idx = self.status_filter.currentIndex()
            if status_idx > 0:
                kwargs['status'] = self.status_filter.currentText()

            # Search
            search_text = self.search_input.text().strip()
            if search_text:
                kwargs['search'] = search_text

            self._devices = self.repo.get_devices(**kwargs)

            # Apply manufacturer filter in memory if set
            if mfg_slug:
                self._devices = [
                    d for d in self._devices
                    if d.manufacturer_slug == mfg_slug
                ]

            self._populate_table()
            self.status_label.setText(f"Showing {len(self._devices)} devices")

        except Exception as e:
            self.status_label.setText(f"Error loading devices: {e}")
            self._devices = []
            self._populate_table()

    def _populate_table(self):
        """Populate table with current device list."""
        self.device_table.setSortingEnabled(False)
        self.device_table.setRowCount(len(self._devices))

        for row, device in enumerate(self._devices):
            # Store device ID in first column for retrieval
            name_item = QTableWidgetItem(device.name or "")
            name_item.setData(Qt.ItemDataRole.UserRole, device.id)
            self.device_table.setItem(row, 0, name_item)

            self.device_table.setItem(row, 1, QTableWidgetItem(device.primary_ip4 or "—"))
            self.device_table.setItem(row, 2, QTableWidgetItem(device.site_name or "—"))
            self.device_table.setItem(row, 3, QTableWidgetItem(device.manufacturer_name or "—"))
            self.device_table.setItem(row, 4, QTableWidgetItem(device.platform_name or "—"))
            self.device_table.setItem(row, 5, QTableWidgetItem(device.role_name or "—"))

            # Status with color coding
            status_item = QTableWidgetItem(device.status or "—")
            status_color = self._get_status_color(device.status)
            if status_color:
                status_item.setForeground(status_color)
            self.device_table.setItem(row, 6, status_item)

            # Last collected - friendly format
            last_collected = self._format_relative_time(device.last_collected_at)
            self.device_table.setItem(row, 7, QTableWidgetItem(last_collected))

        self.device_table.setSortingEnabled(True)

    def _get_status_color(self, status: Optional[str]) -> Optional[QColor]:
        """Get color for device status."""
        colors = {
            'active': QColor('#2ecc71'),      # Green
            'planned': QColor('#3498db'),     # Blue
            'staged': QColor('#f39c12'),      # Orange
            'failed': QColor('#e74c3c'),      # Red
            'offline': QColor('#95a5a6'),     # Gray
            'decommissioning': QColor('#9b59b6'),  # Purple
            'inventory': QColor('#1abc9c'),   # Teal
        }
        return colors.get(status)

    def _format_relative_time(self, timestamp: Optional[str]) -> str:
        """Format timestamp as relative time (e.g., '5 min ago')."""
        if not timestamp:
            return "Never"

        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            now = datetime.now()

            # Handle timezone-naive comparison
            if dt.tzinfo:
                dt = dt.replace(tzinfo=None)

            delta = now - dt

            if delta.days > 30:
                return dt.strftime("%Y-%m-%d")
            elif delta.days > 0:
                return f"{delta.days}d ago"
            elif delta.seconds >= 3600:
                hours = delta.seconds // 3600
                return f"{hours}h ago"
            elif delta.seconds >= 60:
                minutes = delta.seconds // 60
                return f"{minutes}m ago"
            else:
                return "Just now"
        except (ValueError, AttributeError):
            return timestamp or "—"

    def _on_search_changed(self, text: str):
        """Handle search text changes with debounce."""
        self._search_timer.stop()
        self._search_timer.start(300)  # 300ms debounce

    def _do_search(self):
        """Execute search after debounce."""
        self._load_devices()

    def _on_filter_changed(self, index: int):
        """Handle filter dropdown changes."""
        self._load_devices()

    def _clear_filters(self):
        """Clear all filters and search."""
        self.search_input.clear()
        self.site_filter.setCurrentIndex(0)
        self.manufacturer_filter.setCurrentIndex(0)
        self.status_filter.setCurrentIndex(0)
        self._load_devices()

    def _on_selection_changed(self):
        """Handle table selection change."""
        selected = self.device_table.selectedItems()
        if selected:
            device_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if device_id:
                self.device_selected.emit(device_id)

    def _on_double_click(self, index):
        """Handle double-click on device row."""
        row = index.row()
        if row >= 0:
            device_id = self.device_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if device_id:
                self.device_double_clicked.emit(device_id)
                self._show_device_detail(device_id)

    def _show_context_menu(self, pos):
        """Show context menu for device actions."""
        item = self.device_table.itemAt(pos)
        if not item:
            return

        row = item.row()
        device_id = self.device_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not device_id:
            return

        device = self._get_device_by_id(device_id)
        if not device:
            return

        menu = QMenu(self)

        # View details
        view_action = QAction("View Details", self)
        view_action.triggered.connect(lambda: self._show_device_detail(device_id))
        menu.addAction(view_action)

        # Edit
        edit_action = QAction("Edit Device", self)
        edit_action.triggered.connect(lambda: self._edit_device(device_id))
        menu.addAction(edit_action)

        menu.addSeparator()

        # SSH action (if IP available)
        if device.primary_ip4:
            ssh_action = QAction(f"SSH to {device.primary_ip4}", self)
            ssh_action.triggered.connect(lambda: self._ssh_to_device(device))
            menu.addAction(ssh_action)

        # Copy actions
        copy_menu = menu.addMenu("Copy")

        copy_name = QAction("Copy Name", self)
        copy_name.triggered.connect(lambda: self._copy_to_clipboard(device.name))
        copy_menu.addAction(copy_name)

        if device.primary_ip4:
            copy_ip = QAction("Copy IP", self)
            copy_ip.triggered.connect(lambda: self._copy_to_clipboard(device.primary_ip4))
            copy_menu.addAction(copy_ip)

        menu.addSeparator()

        # Status change submenu
        status_menu = menu.addMenu("Set Status")
        for status in ['active', 'offline', 'failed', 'decommissioning']:
            if status != device.status:
                action = QAction(status.title(), self)
                action.triggered.connect(
                    lambda checked, s=status, d=device_id: self._set_device_status(d, s)
                )
                status_menu.addAction(action)

        menu.addSeparator()

        # Delete
        delete_action = QAction("Delete Device", self)
        delete_action.triggered.connect(lambda: self._delete_device(device_id, device.name))
        menu.addAction(delete_action)

        menu.exec(self.device_table.mapToGlobal(pos))

    def _get_device_by_id(self, device_id: int) -> Optional[Device]:
        """Get device from current list by ID."""
        for device in self._devices:
            if device.id == device_id:
                return device
        return None

    def _on_add_device(self):
        """Open dialog to create a new device."""
        dialog = DeviceEditDialog(self.repo, device=None, parent=self)
        dialog.device_saved.connect(self._on_device_saved)
        dialog.exec()

    def _show_device_detail(self, device_id: int):
        """Show device detail dialog."""
        device = self.repo.get_device(device_id=device_id)
        if device:
            dialog = DeviceDetailDialog(device, self)
            dialog.edit_requested.connect(self._edit_device)
            dialog.exec()

    def _edit_device(self, device_id: int):
        """Open edit dialog for device."""
        device = self.repo.get_device(device_id=device_id)
        if device:
            dialog = DeviceEditDialog(self.repo, device=device, parent=self)
            dialog.device_saved.connect(self._on_device_saved)
            dialog.exec()

    def _edit_selected(self):
        """Edit the currently selected device."""
        device_id = self.get_selected_device_id()
        if device_id:
            self._edit_device(device_id)

    def _delete_selected(self):
        """Delete the currently selected device."""
        device = self.get_selected_device()
        if device:
            self._delete_device(device.id, device.name)

    def _delete_device(self, device_id: int, device_name: str):
        """Confirm and delete a device."""
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete device '{device_name}'?\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.repo.delete_device(device_id)
                if success:
                    self.status_label.setText(f"Deleted device: {device_name}")
                    self.refresh_data()
                else:
                    QMessageBox.warning(self, "Error", "Failed to delete device")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Error deleting device: {e}")

    def _on_device_saved(self, device_id: int):
        """Handle device saved signal from edit dialog."""
        self.refresh_data()
        self.status_label.setText(f"Device saved (ID: {device_id})")

    def _ssh_to_device(self, device: Device):
        """Emit signal or launch SSH to device."""
        # This would integrate with your SSH/terminal component
        self.device_double_clicked.emit(device.id)

    def _copy_to_clipboard(self, text: str):
        """Copy text to clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.status_label.setText(f"Copied: {text}")

    def _set_device_status(self, device_id: int, status: str):
        """Update device status."""
        try:
            self.repo.update_device(device_id, status=status)
            self.refresh_data()
            self.status_label.setText(f"Status updated to {status}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to update status: {e}")

    def get_selected_device(self) -> Optional[Device]:
        """Get currently selected device."""
        selected = self.device_table.selectedItems()
        if selected:
            device_id = selected[0].data(Qt.ItemDataRole.UserRole)
            return self._get_device_by_id(device_id)
        return None

    def get_selected_device_id(self) -> Optional[int]:
        """Get ID of currently selected device."""
        selected = self.device_table.selectedItems()
        if selected:
            return selected[0].data(Qt.ItemDataRole.UserRole)
        return None


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow

    # Minimal StatCard stub if not available
    try:
        from vcollector.ui.widgets.stat_cards import StatCard
    except ImportError:
        class StatCard(QWidget):
            def __init__(self, label, value, parent=None):
                super().__init__(parent)
                layout = QVBoxLayout(self)
                self.label = QLabel(label)
                self.value_label = QLabel(value)
                self.value_label.setStyleSheet("font-size: 24px; font-weight: bold;")
                layout.addWidget(self.value_label)
                layout.addWidget(self.label)

            def set_value(self, value):
                self.value_label.setText(value)

    app = QApplication(sys.argv)

    # Create main window with devices view
    window = QMainWindow()
    window.setWindowTitle("Device Inventory - VelocityCollector")
    window.resize(1200, 700)

    view = DevicesView()
    window.setCentralWidget(view)

    # Connect signals for demo
    view.device_selected.connect(lambda id: print(f"Selected device ID: {id}"))
    view.device_double_clicked.connect(lambda id: print(f"Double-clicked device ID: {id}"))

    window.show()
    sys.exit(app.exec())