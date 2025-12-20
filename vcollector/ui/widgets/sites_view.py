"""
VelocityCollector Sites View

Site inventory view with live data from VelocityCollector DCIM database.
Provides search, filtering, and site management operations.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QAbstractItemView,
    QMenu, QMessageBox, QDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QShortcut, QKeySequence

from vcollector.dcim.dcim_repo import DCIMRepository, Site
from vcollector.ui.widgets.stat_cards import StatCard
from vcollector.ui.widgets.site_dialogs import SiteDetailDialog, SiteEditDialog

from typing import Optional, List
from datetime import datetime


class SitesView(QWidget):
    """Site inventory view - reads from VelocityCollector DCIM database."""

    # Signals for external integration
    site_selected = pyqtSignal(int)  # Emits site_id
    site_double_clicked = pyqtSignal(int)  # Emits site_id for action

    def __init__(self, repo: Optional[DCIMRepository] = None, parent=None):
        super().__init__(parent)
        self.repo = repo or DCIMRepository()
        self._sites: List[Site] = []
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)

        self.init_ui()
        self.init_shortcuts()
        self.refresh_data()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Site Management")
        title.setProperty("heading", True)
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Add Site button
        add_btn = QPushButton("+ Add Site")
        add_btn.clicked.connect(self._on_add_site)
        header_layout.addWidget(add_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh_data)
        header_layout.addWidget(refresh_btn)

        layout.addLayout(header_layout)

        # Stats row
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        self.total_sites = StatCard("Total Sites", "0")
        stats_layout.addWidget(self.total_sites)

        self.active_sites = StatCard("Active", "0")
        stats_layout.addWidget(self.active_sites)

        self.total_devices = StatCard("Total Devices", "0")
        stats_layout.addWidget(self.total_devices)

        stats_layout.addStretch()
        layout.addLayout(stats_layout)

        # Search and filter bar
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search sites by name, slug, description...")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._do_search)
        filter_layout.addWidget(self.search_input, stretch=2)

        self.status_filter = QComboBox()
        self.status_filter.addItems([
            "All Status", "active", "planned", "staging",
            "decommissioning", "retired"
        ])
        self.status_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.status_filter)

        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("secondary", True)
        clear_btn.clicked.connect(self._clear_filters)
        filter_layout.addWidget(clear_btn)

        layout.addLayout(filter_layout)

        # Site table
        self.site_table = QTableWidget()
        self.site_table.setColumnCount(6)
        self.site_table.setHorizontalHeaderLabels([
            "Name", "Slug", "Status", "Facility", "Time Zone", "Device Count"
        ])
        self.site_table.setAlternatingRowColors(True)
        self.site_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.site_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.site_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.site_table.horizontalHeader().setStretchLastSection(True)
        self.site_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.site_table.verticalHeader().setVisible(False)
        self.site_table.setSortingEnabled(True)

        # Context menu
        self.site_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.site_table.customContextMenuRequested.connect(self._show_context_menu)

        # Double-click handler
        self.site_table.doubleClicked.connect(self._on_double_click)

        # Selection changed
        self.site_table.itemSelectionChanged.connect(self._on_selection_changed)

        layout.addWidget(self.site_table)

        # Status bar
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

    def init_shortcuts(self):
        """Set up keyboard shortcuts."""
        # Ctrl+N - New site
        QShortcut(QKeySequence("Ctrl+N"), self, self._on_add_site)

        # Enter - Edit selected
        QShortcut(QKeySequence("Return"), self, self._edit_selected)

        # Delete - Delete selected
        QShortcut(QKeySequence("Delete"), self, self._delete_selected)

        # Ctrl+F - Focus search
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.search_input.setFocus())

        # F5 - Refresh
        QShortcut(QKeySequence("F5"), self, self.refresh_data)

    def refresh_data(self):
        """Refresh stats and site list."""
        self._update_stats()
        self._load_sites()

    def _update_stats(self):
        """Update stat cards from database."""
        try:
            stats = self.repo.get_stats()
            self.total_sites.set_value(str(stats.get('total_sites', 0)))
            self.active_sites.set_value(str(stats.get('active_sites', 0)))
            self.total_devices.set_value(str(stats.get('total_devices', 0)))
        except Exception as e:
            self.status_label.setText(f"Error loading stats: {e}")

    def _load_sites(self):
        """Load sites with current filters applied."""
        try:
            # Get all sites first
            self._sites = self.repo.get_sites()

            # Apply status filter
            status_idx = self.status_filter.currentIndex()
            if status_idx > 0:
                status = self.status_filter.currentText()
                self._sites = [s for s in self._sites if s.status == status]

            # Apply search filter
            search_text = self.search_input.text().strip().lower()
            if search_text:
                self._sites = [
                    s for s in self._sites
                    if search_text in (s.name or "").lower()
                    or search_text in (s.slug or "").lower()
                    or search_text in (s.description or "").lower()
                    or search_text in (s.facility or "").lower()
                ]

            self._populate_table()
            self.status_label.setText(f"Showing {len(self._sites)} sites")

        except Exception as e:
            self.status_label.setText(f"Error loading sites: {e}")
            self._sites = []
            self._populate_table()

    def _populate_table(self):
        """Populate table with current site list."""
        self.site_table.setSortingEnabled(False)
        self.site_table.setRowCount(len(self._sites))

        for row, site in enumerate(self._sites):
            # Store site ID in first column for retrieval
            name_item = QTableWidgetItem(site.name or "")
            name_item.setData(Qt.ItemDataRole.UserRole, site.id)
            self.site_table.setItem(row, 0, name_item)

            self.site_table.setItem(row, 1, QTableWidgetItem(site.slug or "—"))

            # Status with color coding
            status_item = QTableWidgetItem(site.status or "—")
            status_color = self._get_status_color(site.status)
            if status_color:
                status_item.setForeground(status_color)
            self.site_table.setItem(row, 2, status_item)

            self.site_table.setItem(row, 3, QTableWidgetItem(site.facility or "—"))
            self.site_table.setItem(row, 4, QTableWidgetItem(site.time_zone or "—"))

            # Device count
            device_count = getattr(site, 'device_count', 0) or 0
            self.site_table.setItem(row, 5, QTableWidgetItem(str(device_count)))

        self.site_table.setSortingEnabled(True)

    def _get_status_color(self, status: Optional[str]) -> Optional[QColor]:
        """Get color for site status."""
        colors = {
            'active': QColor('#2ecc71'),      # Green
            'planned': QColor('#3498db'),     # Blue
            'staging': QColor('#f39c12'),     # Orange
            'decommissioning': QColor('#9b59b6'),  # Purple
            'retired': QColor('#7f8c8d'),     # Dark Gray
        }
        return colors.get(status)

    def _on_search_changed(self, text: str):
        """Handle search text changes with debounce."""
        self._search_timer.stop()
        self._search_timer.start(300)  # 300ms debounce

    def _do_search(self):
        """Execute search after debounce."""
        self._load_sites()

    def _on_filter_changed(self, index: int):
        """Handle filter dropdown changes."""
        self._load_sites()

    def _clear_filters(self):
        """Clear all filters and search."""
        self.search_input.clear()
        self.status_filter.setCurrentIndex(0)
        self._load_sites()

    def _on_selection_changed(self):
        """Handle table selection change."""
        selected = self.site_table.selectedItems()
        if selected:
            site_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if site_id:
                self.site_selected.emit(site_id)

    def _on_double_click(self, index):
        """Handle double-click on site row."""
        row = index.row()
        if row >= 0:
            site_id = self.site_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if site_id:
                self.site_double_clicked.emit(site_id)
                self._show_site_detail(site_id)

    def _show_context_menu(self, pos):
        """Show context menu for site actions."""
        item = self.site_table.itemAt(pos)
        if not item:
            return

        row = item.row()
        site_id = self.site_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not site_id:
            return

        site = self._get_site_by_id(site_id)
        if not site:
            return

        menu = QMenu(self)

        # View details
        view_action = QAction("View Details", self)
        view_action.triggered.connect(lambda: self._show_site_detail(site_id))
        menu.addAction(view_action)

        # Edit
        edit_action = QAction("Edit Site", self)
        edit_action.triggered.connect(lambda: self._edit_site(site_id))
        menu.addAction(edit_action)

        menu.addSeparator()

        # Copy actions
        copy_menu = menu.addMenu("Copy")

        copy_name = QAction("Copy Name", self)
        copy_name.triggered.connect(lambda: self._copy_to_clipboard(site.name))
        copy_menu.addAction(copy_name)

        copy_slug = QAction("Copy Slug", self)
        copy_slug.triggered.connect(lambda: self._copy_to_clipboard(site.slug))
        copy_menu.addAction(copy_slug)

        menu.addSeparator()

        # Status change submenu
        status_menu = menu.addMenu("Set Status")
        for status in ['active', 'planned', 'staging', 'decommissioning', 'retired']:
            if status != site.status:
                action = QAction(status.title(), self)
                action.triggered.connect(
                    lambda checked, s=status, sid=site_id: self._set_site_status(sid, s)
                )
                status_menu.addAction(action)

        menu.addSeparator()

        # Delete
        delete_action = QAction("Delete Site", self)
        delete_action.triggered.connect(lambda: self._delete_site(site_id, site.name))
        menu.addAction(delete_action)

        menu.exec(self.site_table.mapToGlobal(pos))

    def _get_site_by_id(self, site_id: int) -> Optional[Site]:
        """Get site from current list by ID."""
        for site in self._sites:
            if site.id == site_id:
                return site
        return None

    def _on_add_site(self):
        """Open dialog to create a new site."""
        dialog = SiteEditDialog(self.repo, site=None, parent=self)
        dialog.site_saved.connect(self._on_site_saved)
        dialog.exec()

    def _show_site_detail(self, site_id: int):
        """Show site detail dialog."""
        site = self.repo.get_site(site_id=site_id)
        if site:
            dialog = SiteDetailDialog(site, self)
            dialog.edit_requested.connect(self._edit_site)
            dialog.exec()

    def _edit_site(self, site_id: int):
        """Open edit dialog for site."""
        site = self.repo.get_site(site_id=site_id)
        if site:
            dialog = SiteEditDialog(self.repo, site=site, parent=self)
            dialog.site_saved.connect(self._on_site_saved)
            dialog.exec()

    def _edit_selected(self):
        """Edit the currently selected site."""
        site_id = self.get_selected_site_id()
        if site_id:
            self._edit_site(site_id)

    def _delete_selected(self):
        """Delete the currently selected site."""
        site = self.get_selected_site()
        if site:
            self._delete_site(site.id, site.name)

    def _delete_site(self, site_id: int, site_name: str):
        """Confirm and delete a site."""
        # Check if site has devices
        site = self.repo.get_site(site_id=site_id)
        device_count = getattr(site, 'device_count', 0) or 0

        warning_msg = f"Are you sure you want to delete site '{site_name}'?"
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
                success = self.repo.delete_site(site_id)
                if success:
                    self.status_label.setText(f"Deleted site: {site_name}")
                    self.refresh_data()
                else:
                    QMessageBox.warning(self, "Error", "Failed to delete site")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Error deleting site: {e}")

    def _on_site_saved(self, site_id: int):
        """Handle site saved signal from edit dialog."""
        self.refresh_data()
        self.status_label.setText(f"Site saved (ID: {site_id})")

    def _copy_to_clipboard(self, text: str):
        """Copy text to clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.status_label.setText(f"Copied: {text}")

    def _set_site_status(self, site_id: int, status: str):
        """Update site status."""
        try:
            self.repo.update_site(site_id, status=status)
            self.refresh_data()
            self.status_label.setText(f"Status updated to {status}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to update status: {e}")

    def get_selected_site(self) -> Optional[Site]:
        """Get currently selected site."""
        selected = self.site_table.selectedItems()
        if selected:
            site_id = selected[0].data(Qt.ItemDataRole.UserRole)
            return self._get_site_by_id(site_id)
        return None

    def get_selected_site_id(self) -> Optional[int]:
        """Get ID of currently selected site."""
        selected = self.site_table.selectedItems()
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

    # Create main window with sites view
    window = QMainWindow()
    window.setWindowTitle("Site Management - VelocityCollector")
    window.resize(1000, 600)

    view = SitesView()
    window.setCentralWidget(view)

    # Connect signals for demo
    view.site_selected.connect(lambda id: print(f"Selected site ID: {id}"))
    view.site_double_clicked.connect(lambda id: print(f"Double-clicked site ID: {id}"))

    window.show()
    sys.exit(app.exec())