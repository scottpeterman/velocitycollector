"""
VelocityCollector Devices View

Device inventory view with live data from VelocityCollector DCIM database.
Provides search, filtering, credential status display, and device management operations.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QAbstractItemView,
    QMenu, QMessageBox, QDialog, QProgressDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QAction, QColor, QShortcut, QKeySequence

from vcollector.dcim.dcim_repo import DCIMRepository, Device
from vcollector.ui.widgets.stat_cards import StatCard
from vcollector.ui.widgets.device_dialogs import DeviceDetailDialog, DeviceEditDialog

from typing import Optional, List
from datetime import datetime


class CredentialDiscoveryThread(QThread):
    """Background thread for credential discovery."""

    progress = pyqtSignal(int, int, object)  # completed, total, result
    finished_discovery = pyqtSignal(object)  # DiscoveryResult
    error = pyqtSignal(str)

    def __init__(self, devices: List[Device], vault_password: str, options: dict):
        super().__init__()
        self.devices = devices
        self.vault_password = vault_password
        self.options = options
        self._cancelled = False

    def run(self):
        try:
            from vcollector.core.cred_discovery import CredentialDiscovery
            from vcollector.vault.resolver import CredentialResolver
            from vcollector.dcim.dcim_repo import DCIMRepository

            # Unlock vault
            resolver = CredentialResolver()
            if not resolver.is_initialized():
                self.error.emit("Vault not initialized")
                return

            if not resolver.unlock_vault(self.vault_password):
                self.error.emit("Invalid vault password")
                return

            try:
                dcim_repo = DCIMRepository()
                discovery = CredentialDiscovery(
                    resolver=resolver,
                    dcim_repo=dcim_repo,
                    timeout=self.options.get('timeout', 15),
                    max_workers=self.options.get('max_workers', 8),
                )

                def on_progress(completed, total, result):
                    if not self._cancelled:
                        self.progress.emit(completed, total, result)

                result = discovery.discover(
                    devices=self.devices,
                    credential_names=self.options.get('credential_names'),
                    skip_configured=self.options.get('skip_configured', False),
                    skip_recently_tested=self.options.get('skip_recently_tested', True),
                    update_devices=True,
                    progress_callback=on_progress,
                )

                if not self._cancelled:
                    self.finished_discovery.emit(result)

            finally:
                resolver.lock_vault()

        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")

    def cancel(self):
        self._cancelled = True


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
        self._discovery_thread: Optional[CredentialDiscoveryThread] = None

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

        # Discover Credentials button
        discover_btn = QPushButton("🔑 Discover Creds")
        discover_btn.setToolTip("Test credentials against selected or all devices")
        discover_btn.clicked.connect(self._on_discover_credentials)
        header_layout.addWidget(discover_btn)

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

        self.cred_coverage = StatCard("Cred Coverage", "0%")
        stats_layout.addWidget(self.cred_coverage)

        self.sites_count = StatCard("Sites", "0")
        stats_layout.addWidget(self.sites_count)

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

        # Credential status filter
        self.cred_filter = QComboBox()
        self.cred_filter.addItems([
            "All Creds", "✓ Success", "✗ Failed", "? Untested", "No Cred"
        ])
        self.cred_filter.setToolTip("Filter by credential test status")
        self.cred_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.cred_filter)

        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("secondary", True)
        clear_btn.clicked.connect(self._clear_filters)
        filter_layout.addWidget(clear_btn)

        layout.addLayout(filter_layout)

        # Device table - now with 9 columns including Cred Status
        self.device_table = QTableWidget()
        self.device_table.setColumnCount(9)
        self.device_table.setHorizontalHeaderLabels([
            "Name", "IP Address", "Site", "Manufacturer", "Platform",
            "Role", "Status", "Cred Status", "Last Collected"
        ])
        self.device_table.setAlternatingRowColors(True)
        self.device_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.device_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # Multi-select
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

        # Ctrl+D - Discover credentials for selected
        QShortcut(QKeySequence("Ctrl+D"), self, self._on_discover_credentials)

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
            self.platforms_count.set_value(str(stats.get('total_platforms', 0)))

            # Credential coverage stats
            cred_stats = self.repo.get_credential_coverage_stats()
            total = cred_stats.get('total_active', 0)
            success = cred_stats.get('test_success', 0)
            if total > 0:
                coverage = (success / total) * 100
                self.cred_coverage.set_value(f"{coverage:.0f}%")
                # Color code based on coverage
                if coverage >= 90:
                    self.cred_coverage.setStyleSheet("QLabel { color: #2ecc71; }")
                elif coverage >= 50:
                    self.cred_coverage.setStyleSheet("QLabel { color: #f39c12; }")
                else:
                    self.cred_coverage.setStyleSheet("QLabel { color: #e74c3c; }")
            else:
                self.cred_coverage.set_value("—")

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

            # Apply credential filter in memory
            cred_filter_idx = self.cred_filter.currentIndex()
            if cred_filter_idx > 0:
                if cred_filter_idx == 1:  # Success
                    self._devices = [d for d in self._devices if d.credential_test_result == 'success']
                elif cred_filter_idx == 2:  # Failed
                    self._devices = [d for d in self._devices if d.credential_test_result == 'failed']
                elif cred_filter_idx == 3:  # Untested
                    self._devices = [d for d in self._devices
                                    if d.credential_test_result in (None, 'untested', '')]
                elif cred_filter_idx == 4:  # No Cred
                    self._devices = [d for d in self._devices if d.credential_id is None]

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

            # Credential status with color coding
            cred_status = self._format_cred_status(device)
            cred_item = QTableWidgetItem(cred_status)
            cred_color = self._get_cred_status_color(device.credential_test_result)
            if cred_color:
                cred_item.setForeground(cred_color)
            # Store credential info for tooltip
            if device.credential_tested_at:
                cred_item.setToolTip(f"Last tested: {device.credential_tested_at}")
            self.device_table.setItem(row, 7, cred_item)

            # Last collected - friendly format
            last_collected = self._format_relative_time(device.last_collected_at)
            self.device_table.setItem(row, 8, QTableWidgetItem(last_collected))

        self.device_table.setSortingEnabled(True)

    def _format_cred_status(self, device: Device) -> str:
        """Format credential status for display."""
        if device.credential_test_result == 'success':
            return "✓ OK"
        elif device.credential_test_result == 'failed':
            return "✗ Failed"
        elif device.credential_id:
            return "? Assigned"
        else:
            return "—"

    def _get_cred_status_color(self, test_result: Optional[str]) -> Optional[QColor]:
        """Get color for credential test status."""
        colors = {
            'success': QColor('#2ecc71'),   # Green
            'failed': QColor('#e74c3c'),    # Red
        }
        return colors.get(test_result)

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
        self.cred_filter.setCurrentIndex(0)
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

        # Credential actions
        cred_menu = menu.addMenu("Credentials")

        test_cred_action = QAction("Test Credential", self)
        test_cred_action.triggered.connect(lambda: self._test_device_credential(device))
        cred_menu.addAction(test_cred_action)

        discover_cred_action = QAction("Discover Credential", self)
        discover_cred_action.triggered.connect(lambda: self._discover_single_device_credential(device))
        cred_menu.addAction(discover_cred_action)

        # Selected devices discovery
        selected_rows = set(item.row() for item in self.device_table.selectedItems())
        if len(selected_rows) > 1:
            discover_selected_action = QAction(f"Discover Selected ({len(selected_rows)})", self)
            discover_selected_action.triggered.connect(self._on_discover_credentials)
            cred_menu.addAction(discover_selected_action)

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

    def _get_selected_devices(self) -> List[Device]:
        """Get all selected devices."""
        selected_rows = set(item.row() for item in self.device_table.selectedItems())
        devices = []
        for row in selected_rows:
            device_id = self.device_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            device = self._get_device_by_id(device_id)
            if device:
                devices.append(device)
        return devices

    def _on_add_device(self):
        """Open dialog to create a new device."""
        dialog = DeviceEditDialog(self.repo, device=None, parent=self)
        dialog.device_saved.connect(self._on_device_saved)
        dialog.exec()

    def _show_device_detail(self, device_id: int):
        """Show device detail dialog."""
        device = self.repo.get_device(device_id=device_id)
        if device:
            dialog = DeviceDetailDialog(device, self.repo, self)
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

    def _test_device_credential(self, device: Device):
        """Test the assigned credential for a single device."""
        if not device.credential_id:
            QMessageBox.information(
                self, "No Credential",
                f"Device '{device.name}' has no assigned credential.\n"
                "Use 'Discover Credential' to find a working credential."
            )
            return

        # Run discovery for just this device (will test assigned credential first)
        self._discover_single_device_credential(device)

    def _discover_single_device_credential(self, device: Device):
        """Discover working credential for a single device."""
        self._run_credential_discovery([device])

    def _on_discover_credentials(self):
        """Discover credentials for selected devices (or all if none selected)."""
        selected_devices = self._get_selected_devices()

        if not selected_devices:
            # Prompt to discover all
            reply = QMessageBox.question(
                self,
                "Discover Credentials",
                "No devices selected. Discover credentials for all active devices?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            # Get all active devices
            selected_devices = self.repo.get_devices(status='active')

        self._run_credential_discovery(selected_devices)

    def _run_credential_discovery(self, devices: List[Device]):
        """Run credential discovery in background thread."""
        if self._discovery_thread and self._discovery_thread.isRunning():
            QMessageBox.warning(
                self, "Discovery Running",
                "A credential discovery is already in progress."
            )
            return

        # Get vault password
        from PyQt6.QtWidgets import QInputDialog, QLineEdit
        password, ok = QInputDialog.getText(
            self, "Vault Password",
            f"Enter vault password to test {len(devices)} device(s):",
            QLineEdit.EchoMode.Password
        )

        if not ok or not password:
            return

        # Create progress dialog
        self._progress = QProgressDialog(
            f"Testing credentials on {len(devices)} devices...",
            "Cancel", 0, len(devices), self
        )
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setAutoClose(True)
        self._progress.setAutoReset(False)

        # Start discovery thread
        self._discovery_thread = CredentialDiscoveryThread(
            devices=devices,
            vault_password=password,
            options={
                'skip_configured': False,
                'skip_recently_tested': False,
                'timeout': 15,
                'max_workers': 8,
            }
        )

        self._discovery_thread.progress.connect(self._on_discovery_progress)
        self._discovery_thread.finished_discovery.connect(self._on_discovery_finished)
        self._discovery_thread.error.connect(self._on_discovery_error)
        self._progress.canceled.connect(self._on_discovery_cancel)

        self._discovery_thread.start()

    def _on_discovery_progress(self, completed: int, total: int, result):
        """Handle discovery progress update."""
        self._progress.setValue(completed)
        status = "✓" if result.success else "✗"
        self._progress.setLabelText(
            f"[{completed}/{total}] {result.device_name}: {status}"
        )

    def _on_discovery_finished(self, result):
        """Handle discovery completion."""
        self._progress.close()
        self.refresh_data()

        QMessageBox.information(
            self,
            "Discovery Complete",
            f"Credential Discovery Results:\n\n"
            f"  Devices tested: {result.total_devices}\n"
            f"  Matched: {result.matched_count}\n"
            f"  No match: {result.no_match_count}\n"
            f"  Skipped: {result.skipped_count}\n"
            f"  Duration: {result.duration_seconds:.1f}s"
        )

    def _on_discovery_error(self, message: str):
        """Handle discovery error."""
        self._progress.close()
        QMessageBox.critical(self, "Discovery Error", message)

    def _on_discovery_cancel(self):
        """Handle discovery cancellation."""
        if self._discovery_thread:
            self._discovery_thread.cancel()

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