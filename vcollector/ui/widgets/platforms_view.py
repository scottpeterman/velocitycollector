"""
VelocityCollector Platforms View

Platform and Device Role management view with live data from VelocityCollector DCIM database.
Combines Platforms and Roles in a tabbed interface since they're closely related lookup tables.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QAbstractItemView,
    QMenu, QMessageBox, QDialog, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QShortcut, QKeySequence

from vcollector.dcim.dcim_repo import DCIMRepository, Platform, DeviceRole, Manufacturer
from vcollector.ui.widgets.stat_cards import StatCard
from vcollector.ui.widgets.platform_dialogs import (
    PlatformDetailDialog, PlatformEditDialog,
    RoleDetailDialog, RoleEditDialog
)

from typing import Optional, List


class PlatformsView(QWidget):
    """
    Combined Platform and Device Role management view.

    Uses tabs to organize the two related entity types.
    """

    # Signals for external integration
    platform_selected = pyqtSignal(int)
    role_selected = pyqtSignal(int)

    def __init__(self, repo: Optional[DCIMRepository] = None, parent=None):
        super().__init__(parent)
        self.repo = repo or DCIMRepository()

        self._platforms: List[Platform] = []
        self._roles: List[DeviceRole] = []
        self._manufacturers: List[Manufacturer] = []

        self._platform_search_timer = QTimer()
        self._platform_search_timer.setSingleShot(True)
        self._platform_search_timer.timeout.connect(self._do_platform_search)

        self._role_search_timer = QTimer()
        self._role_search_timer.setSingleShot(True)
        self._role_search_timer.timeout.connect(self._do_role_search)

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
        title = QLabel("Platforms & Roles")
        title.setProperty("heading", True)
        header_layout.addWidget(title)
        header_layout.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh_data)
        header_layout.addWidget(refresh_btn)

        layout.addLayout(header_layout)

        # Stats row
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        self.total_platforms = StatCard("Platforms", "0")
        stats_layout.addWidget(self.total_platforms)

        self.total_manufacturers = StatCard("Manufacturers", "0")
        stats_layout.addWidget(self.total_manufacturers)

        self.total_roles = StatCard("Roles", "0")
        stats_layout.addWidget(self.total_roles)

        self.total_devices = StatCard("Total Devices", "0")
        stats_layout.addWidget(self.total_devices)

        stats_layout.addStretch()
        layout.addLayout(stats_layout)

        # Tabbed interface for Platforms and Roles
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_platforms_tab(), "Platforms")
        self.tabs.addTab(self._create_roles_tab(), "Device Roles")
        layout.addWidget(self.tabs)

        # Status bar
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

    def _create_platforms_tab(self) -> QWidget:
        """Create the Platforms tab content."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # Filter bar
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(12)

        self.platform_search = QLineEdit()
        self.platform_search.setPlaceholderText("Search platforms by name, slug...")
        self.platform_search.textChanged.connect(self._on_platform_search_changed)
        self.platform_search.returnPressed.connect(self._do_platform_search)
        filter_layout.addWidget(self.platform_search, stretch=2)

        self.manufacturer_filter = QComboBox()
        self.manufacturer_filter.setMinimumWidth(150)
        self.manufacturer_filter.currentIndexChanged.connect(self._on_platform_filter_changed)
        filter_layout.addWidget(self.manufacturer_filter)

        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("secondary", True)
        clear_btn.clicked.connect(self._clear_platform_filters)
        filter_layout.addWidget(clear_btn)

        add_btn = QPushButton("+ Add Platform")
        add_btn.clicked.connect(self._on_add_platform)
        filter_layout.addWidget(add_btn)

        layout.addLayout(filter_layout)

        # Platform table
        self.platform_table = QTableWidget()
        self.platform_table.setColumnCount(5)
        self.platform_table.setHorizontalHeaderLabels([
            "Name", "Slug", "Manufacturer", "Netmiko Type", "Device Count"
        ])
        self.platform_table.setAlternatingRowColors(True)
        self.platform_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.platform_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.platform_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.platform_table.horizontalHeader().setStretchLastSection(True)
        self.platform_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.platform_table.verticalHeader().setVisible(False)
        self.platform_table.setSortingEnabled(True)

        # Context menu
        self.platform_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.platform_table.customContextMenuRequested.connect(self._show_platform_context_menu)

        # Double-click handler
        self.platform_table.doubleClicked.connect(self._on_platform_double_click)

        # Selection changed
        self.platform_table.itemSelectionChanged.connect(self._on_platform_selection_changed)

        layout.addWidget(self.platform_table)

        return tab

    def _create_roles_tab(self) -> QWidget:
        """Create the Device Roles tab content."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # Filter bar
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(12)

        self.role_search = QLineEdit()
        self.role_search.setPlaceholderText("Search roles by name, slug...")
        self.role_search.textChanged.connect(self._on_role_search_changed)
        self.role_search.returnPressed.connect(self._do_role_search)
        filter_layout.addWidget(self.role_search, stretch=2)

        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("secondary", True)
        clear_btn.clicked.connect(self._clear_role_filters)
        filter_layout.addWidget(clear_btn)

        add_btn = QPushButton("+ Add Role")
        add_btn.clicked.connect(self._on_add_role)
        filter_layout.addWidget(add_btn)

        layout.addLayout(filter_layout)

        # Role table
        self.role_table = QTableWidget()
        self.role_table.setColumnCount(4)
        self.role_table.setHorizontalHeaderLabels([
            "Name", "Slug", "Color", "Device Count"
        ])
        self.role_table.setAlternatingRowColors(True)
        self.role_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.role_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.role_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.role_table.horizontalHeader().setStretchLastSection(True)
        self.role_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.role_table.verticalHeader().setVisible(False)
        self.role_table.setSortingEnabled(True)

        # Context menu
        self.role_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.role_table.customContextMenuRequested.connect(self._show_role_context_menu)

        # Double-click handler
        self.role_table.doubleClicked.connect(self._on_role_double_click)

        # Selection changed
        self.role_table.itemSelectionChanged.connect(self._on_role_selection_changed)

        layout.addWidget(self.role_table)

        return tab

    def init_shortcuts(self):
        """Set up keyboard shortcuts."""
        # Ctrl+N - New (context-aware based on active tab)
        QShortcut(QKeySequence("Ctrl+N"), self, self._on_add_current)

        # Enter - Edit selected
        QShortcut(QKeySequence("Return"), self, self._edit_selected)

        # Delete - Delete selected
        QShortcut(QKeySequence("Delete"), self, self._delete_selected)

        # Ctrl+F - Focus search
        QShortcut(QKeySequence("Ctrl+F"), self, self._focus_search)

        # F5 - Refresh
        QShortcut(QKeySequence("F5"), self, self.refresh_data)

    def _on_add_current(self):
        """Add new item based on current tab."""
        if self.tabs.currentIndex() == 0:
            self._on_add_platform()
        else:
            self._on_add_role()

    def _edit_selected(self):
        """Edit selected item based on current tab."""
        if self.tabs.currentIndex() == 0:
            self._edit_selected_platform()
        else:
            self._edit_selected_role()

    def _delete_selected(self):
        """Delete selected item based on current tab."""
        if self.tabs.currentIndex() == 0:
            self._delete_selected_platform()
        else:
            self._delete_selected_role()

    def _focus_search(self):
        """Focus the appropriate search field."""
        if self.tabs.currentIndex() == 0:
            self.platform_search.setFocus()
        else:
            self.role_search.setFocus()

    def load_filters(self):
        """Load filter dropdown options from database."""
        # Manufacturers for platform filter
        self._manufacturers = self.repo.get_manufacturers()
        self.manufacturer_filter.clear()
        self.manufacturer_filter.addItem("All Manufacturers", None)
        for mfg in self._manufacturers:
            self.manufacturer_filter.addItem(mfg.name, mfg.id)

    def refresh_data(self):
        """Refresh stats and both tables."""
        self.load_filters()
        self._update_stats()
        self._load_platforms()
        self._load_roles()

    def _update_stats(self):
        """Update stat cards from database."""
        try:
            stats = self.repo.get_stats()
            self.total_platforms.set_value(str(stats.get('total_platforms', 0)))
            self.total_manufacturers.set_value(str(stats.get('total_manufacturers', 0)))
            self.total_roles.set_value(str(stats.get('total_roles', 0)))
            self.total_devices.set_value(str(stats.get('total_devices', 0)))
        except Exception as e:
            self.status_label.setText(f"Error loading stats: {e}")

    # ========== PLATFORMS ==========

    def _load_platforms(self):
        """Load platforms with current filters applied."""
        try:
            self._platforms = self.repo.get_platforms()

            # Apply manufacturer filter
            mfg_id = self.manufacturer_filter.currentData()
            if mfg_id:
                self._platforms = [p for p in self._platforms if p.manufacturer_id == mfg_id]

            # Apply search filter
            search_text = self.platform_search.text().strip().lower()
            if search_text:
                self._platforms = [
                    p for p in self._platforms
                    if search_text in (p.name or "").lower()
                    or search_text in (p.slug or "").lower()
                    or search_text in (p.netmiko_device_type or "").lower()
                ]

            self._populate_platform_table()
            self.status_label.setText(f"Showing {len(self._platforms)} platforms")

        except Exception as e:
            self.status_label.setText(f"Error loading platforms: {e}")
            self._platforms = []
            self._populate_platform_table()

    def _populate_platform_table(self):
        """Populate platform table with current list."""
        self.platform_table.setSortingEnabled(False)
        self.platform_table.setRowCount(len(self._platforms))

        for row, platform in enumerate(self._platforms):
            # Store ID in first column
            name_item = QTableWidgetItem(platform.name or "")
            name_item.setData(Qt.ItemDataRole.UserRole, platform.id)
            self.platform_table.setItem(row, 0, name_item)

            self.platform_table.setItem(row, 1, QTableWidgetItem(platform.slug or "—"))
            self.platform_table.setItem(row, 2, QTableWidgetItem(platform.manufacturer_name or "—"))
            self.platform_table.setItem(row, 3, QTableWidgetItem(platform.netmiko_device_type or "—"))

            # Device count
            device_count = getattr(platform, 'device_count', 0) or 0
            self.platform_table.setItem(row, 4, QTableWidgetItem(str(device_count)))

        self.platform_table.setSortingEnabled(True)

    def _on_platform_search_changed(self, text: str):
        """Handle platform search text changes with debounce."""
        self._platform_search_timer.stop()
        self._platform_search_timer.start(300)

    def _do_platform_search(self):
        """Execute platform search after debounce."""
        self._load_platforms()

    def _on_platform_filter_changed(self, index: int):
        """Handle manufacturer filter changes."""
        self._load_platforms()

    def _clear_platform_filters(self):
        """Clear platform filters and search."""
        self.platform_search.clear()
        self.manufacturer_filter.setCurrentIndex(0)
        self._load_platforms()

    def _on_platform_selection_changed(self):
        """Handle platform table selection change."""
        selected = self.platform_table.selectedItems()
        if selected:
            platform_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if platform_id:
                self.platform_selected.emit(platform_id)

    def _on_platform_double_click(self, index):
        """Handle double-click on platform row."""
        row = index.row()
        if row >= 0:
            platform_id = self.platform_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if platform_id:
                self._show_platform_detail(platform_id)

    def _show_platform_context_menu(self, pos):
        """Show context menu for platform actions."""
        item = self.platform_table.itemAt(pos)
        if not item:
            return

        row = item.row()
        platform_id = self.platform_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not platform_id:
            return

        platform = self._get_platform_by_id(platform_id)
        if not platform:
            return

        menu = QMenu(self)

        # View details
        view_action = QAction("View Details", self)
        view_action.triggered.connect(lambda: self._show_platform_detail(platform_id))
        menu.addAction(view_action)

        # Edit
        edit_action = QAction("Edit Platform", self)
        edit_action.triggered.connect(lambda: self._edit_platform(platform_id))
        menu.addAction(edit_action)

        menu.addSeparator()

        # Copy actions
        copy_menu = menu.addMenu("Copy")

        copy_name = QAction("Copy Name", self)
        copy_name.triggered.connect(lambda: self._copy_to_clipboard(platform.name))
        copy_menu.addAction(copy_name)

        copy_slug = QAction("Copy Slug", self)
        copy_slug.triggered.connect(lambda: self._copy_to_clipboard(platform.slug))
        copy_menu.addAction(copy_slug)

        if platform.netmiko_device_type:
            copy_netmiko = QAction("Copy Netmiko Type", self)
            copy_netmiko.triggered.connect(lambda: self._copy_to_clipboard(platform.netmiko_device_type))
            copy_menu.addAction(copy_netmiko)

        menu.addSeparator()

        # Delete
        delete_action = QAction("Delete Platform", self)
        delete_action.triggered.connect(lambda: self._delete_platform(platform_id, platform.name))
        menu.addAction(delete_action)

        menu.exec(self.platform_table.mapToGlobal(pos))

    def _get_platform_by_id(self, platform_id: int) -> Optional[Platform]:
        """Get platform from current list by ID."""
        for platform in self._platforms:
            if platform.id == platform_id:
                return platform
        return None

    def _on_add_platform(self):
        """Open dialog to create a new platform."""
        dialog = PlatformEditDialog(self.repo, platform=None, parent=self)
        dialog.platform_saved.connect(self._on_platform_saved)
        dialog.exec()

    def _show_platform_detail(self, platform_id: int):
        """Show platform detail dialog."""
        platform = self.repo.get_platform(platform_id=platform_id)
        if platform:
            dialog = PlatformDetailDialog(platform, self)
            dialog.edit_requested.connect(self._edit_platform)
            dialog.exec()

    def _edit_platform(self, platform_id: int):
        """Open edit dialog for platform."""
        platform = self.repo.get_platform(platform_id=platform_id)
        if platform:
            dialog = PlatformEditDialog(self.repo, platform=platform, parent=self)
            dialog.platform_saved.connect(self._on_platform_saved)
            dialog.exec()

    def _edit_selected_platform(self):
        """Edit the currently selected platform."""
        selected = self.platform_table.selectedItems()
        if selected:
            platform_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if platform_id:
                self._edit_platform(platform_id)

    def _delete_selected_platform(self):
        """Delete the currently selected platform."""
        selected = self.platform_table.selectedItems()
        if selected:
            platform_id = selected[0].data(Qt.ItemDataRole.UserRole)
            platform = self._get_platform_by_id(platform_id)
            if platform:
                self._delete_platform(platform_id, platform.name)

    def _delete_platform(self, platform_id: int, platform_name: str):
        """Confirm and delete a platform."""
        platform = self.repo.get_platform(platform_id=platform_id)
        device_count = getattr(platform, 'device_count', 0) or 0

        warning_msg = f"Are you sure you want to delete platform '{platform_name}'?"
        if device_count > 0:
            warning_msg += f"\n\nWARNING: {device_count} device(s) use this platform. They will be set to no platform."
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
                success = self.repo.delete_platform(platform_id)
                if success:
                    self.status_label.setText(f"Deleted platform: {platform_name}")
                    self.refresh_data()
                else:
                    QMessageBox.warning(self, "Error", "Failed to delete platform")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Error deleting platform: {e}")

    def _on_platform_saved(self, platform_id: int):
        """Handle platform saved signal from edit dialog."""
        self.refresh_data()
        self.status_label.setText(f"Platform saved (ID: {platform_id})")

    # ========== ROLES ==========

    def _load_roles(self):
        """Load roles with current filters applied."""
        try:
            self._roles = self.repo.get_device_roles()

            # Apply search filter
            search_text = self.role_search.text().strip().lower()
            if search_text:
                self._roles = [
                    r for r in self._roles
                    if search_text in (r.name or "").lower()
                    or search_text in (r.slug or "").lower()
                ]

            self._populate_role_table()

        except Exception as e:
            self.status_label.setText(f"Error loading roles: {e}")
            self._roles = []
            self._populate_role_table()

    def _populate_role_table(self):
        """Populate role table with current list."""
        self.role_table.setSortingEnabled(False)
        self.role_table.setRowCount(len(self._roles))

        for row, role in enumerate(self._roles):
            # Store ID in first column
            name_item = QTableWidgetItem(role.name or "")
            name_item.setData(Qt.ItemDataRole.UserRole, role.id)
            self.role_table.setItem(row, 0, name_item)

            self.role_table.setItem(row, 1, QTableWidgetItem(role.slug or "—"))

            # Color with visual indicator
            color = role.color or "9e9e9e"
            color_item = QTableWidgetItem(f"#{color}")
            color_item.setForeground(QColor(f"#{color}"))
            self.role_table.setItem(row, 2, color_item)

            # Device count
            device_count = getattr(role, 'device_count', 0) or 0
            self.role_table.setItem(row, 3, QTableWidgetItem(str(device_count)))

        self.role_table.setSortingEnabled(True)

    def _on_role_search_changed(self, text: str):
        """Handle role search text changes with debounce."""
        self._role_search_timer.stop()
        self._role_search_timer.start(300)

    def _do_role_search(self):
        """Execute role search after debounce."""
        self._load_roles()

    def _clear_role_filters(self):
        """Clear role filters and search."""
        self.role_search.clear()
        self._load_roles()

    def _on_role_selection_changed(self):
        """Handle role table selection change."""
        selected = self.role_table.selectedItems()
        if selected:
            role_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if role_id:
                self.role_selected.emit(role_id)

    def _on_role_double_click(self, index):
        """Handle double-click on role row."""
        row = index.row()
        if row >= 0:
            role_id = self.role_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if role_id:
                self._show_role_detail(role_id)

    def _show_role_context_menu(self, pos):
        """Show context menu for role actions."""
        item = self.role_table.itemAt(pos)
        if not item:
            return

        row = item.row()
        role_id = self.role_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not role_id:
            return

        role = self._get_role_by_id(role_id)
        if not role:
            return

        menu = QMenu(self)

        # View details
        view_action = QAction("View Details", self)
        view_action.triggered.connect(lambda: self._show_role_detail(role_id))
        menu.addAction(view_action)

        # Edit
        edit_action = QAction("Edit Role", self)
        edit_action.triggered.connect(lambda: self._edit_role(role_id))
        menu.addAction(edit_action)

        menu.addSeparator()

        # Copy actions
        copy_menu = menu.addMenu("Copy")

        copy_name = QAction("Copy Name", self)
        copy_name.triggered.connect(lambda: self._copy_to_clipboard(role.name))
        copy_menu.addAction(copy_name)

        copy_slug = QAction("Copy Slug", self)
        copy_slug.triggered.connect(lambda: self._copy_to_clipboard(role.slug))
        copy_menu.addAction(copy_slug)

        menu.addSeparator()

        # Delete
        delete_action = QAction("Delete Role", self)
        delete_action.triggered.connect(lambda: self._delete_role(role_id, role.name))
        menu.addAction(delete_action)

        menu.exec(self.role_table.mapToGlobal(pos))

    def _get_role_by_id(self, role_id: int) -> Optional[DeviceRole]:
        """Get role from current list by ID."""
        for role in self._roles:
            if role.id == role_id:
                return role
        return None

    def _on_add_role(self):
        """Open dialog to create a new role."""
        dialog = RoleEditDialog(self.repo, role=None, parent=self)
        dialog.role_saved.connect(self._on_role_saved)
        dialog.exec()

    def _show_role_detail(self, role_id: int):
        """Show role detail dialog."""
        role = self.repo.get_device_role(role_id=role_id)
        if role:
            dialog = RoleDetailDialog(role, self)
            dialog.edit_requested.connect(self._edit_role)
            dialog.exec()

    def _edit_role(self, role_id: int):
        """Open edit dialog for role."""
        role = self.repo.get_device_role(role_id=role_id)
        if role:
            dialog = RoleEditDialog(self.repo, role=role, parent=self)
            dialog.role_saved.connect(self._on_role_saved)
            dialog.exec()

    def _edit_selected_role(self):
        """Edit the currently selected role."""
        selected = self.role_table.selectedItems()
        if selected:
            role_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if role_id:
                self._edit_role(role_id)

    def _delete_selected_role(self):
        """Delete the currently selected role."""
        selected = self.role_table.selectedItems()
        if selected:
            role_id = selected[0].data(Qt.ItemDataRole.UserRole)
            role = self._get_role_by_id(role_id)
            if role:
                self._delete_role(role_id, role.name)

    def _delete_role(self, role_id: int, role_name: str):
        """Confirm and delete a role."""
        role = self.repo.get_device_role(role_id=role_id)
        device_count = getattr(role, 'device_count', 0) or 0

        warning_msg = f"Are you sure you want to delete role '{role_name}'?"
        if device_count > 0:
            warning_msg += f"\n\nWARNING: {device_count} device(s) use this role. They will be set to no role."
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
                success = self.repo.delete_device_role(role_id)
                if success:
                    self.status_label.setText(f"Deleted role: {role_name}")
                    self.refresh_data()
                else:
                    QMessageBox.warning(self, "Error", "Failed to delete role")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Error deleting role: {e}")

    def _on_role_saved(self, role_id: int):
        """Handle role saved signal from edit dialog."""
        self.refresh_data()
        self.status_label.setText(f"Role saved (ID: {role_id})")

    # ========== COMMON ==========

    def _copy_to_clipboard(self, text: str):
        """Copy text to clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.status_label.setText(f"Copied: {text}")


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

    # Create main window with platforms view
    window = QMainWindow()
    window.setWindowTitle("Platforms & Roles - VelocityCollector")
    window.resize(1000, 600)

    view = PlatformsView()
    window.setCentralWidget(view)

    # Connect signals for demo
    view.platform_selected.connect(lambda id: print(f"Selected platform ID: {id}"))
    view.role_selected.connect(lambda id: print(f"Selected role ID: {id}"))

    window.show()
    sys.exit(app.exec())