"""
VelocityCollector Jobs View

Job management view with live data from VelocityCollector collector database.
Provides search, filtering, and job management operations.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QAbstractItemView,
    QMenu, QMessageBox, QDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QShortcut, QKeySequence

from vcollector.dcim.jobs_repo import JobsRepository, Job
from vcollector.ui.widgets.stat_cards import StatCard
from vcollector.ui.widgets.job_dialogs import JobDetailDialog, JobEditDialog

from typing import Optional, List
from datetime import datetime


class JobsView(QWidget):
    """Job management view - reads from VelocityCollector collector database."""

    # Signals for external integration
    job_selected = pyqtSignal(int)  # Emits job_id
    job_double_clicked = pyqtSignal(int)  # Emits job_id for action
    run_job_requested = pyqtSignal(int)  # Request to run a job

    def __init__(self, repo: Optional[JobsRepository] = None, parent=None):
        super().__init__(parent)
        self.repo = repo or JobsRepository()
        self._jobs: List[Job] = []
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
        title = QLabel("Job Management")
        title.setProperty("heading", True)
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Add Job button
        add_btn = QPushButton("+ Add Job")
        add_btn.clicked.connect(self._on_add_job)
        header_layout.addWidget(add_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh_data)
        header_layout.addWidget(refresh_btn)

        layout.addLayout(header_layout)

        # Stats row
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        self.total_jobs = StatCard("Total Jobs", "0")
        stats_layout.addWidget(self.total_jobs)

        self.enabled_jobs = StatCard("Enabled", "0")
        stats_layout.addWidget(self.enabled_jobs)

        self.total_runs = StatCard("Total Runs", "0")
        stats_layout.addWidget(self.total_runs)

        self.successful_runs = StatCard("Successful", "0")
        stats_layout.addWidget(self.successful_runs)

        stats_layout.addStretch()
        layout.addLayout(stats_layout)

        # Search and filter bar
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search jobs by name, description...")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._do_search)
        filter_layout.addWidget(self.search_input, stretch=2)

        self.capture_type_filter = QComboBox()
        self.capture_type_filter.setMinimumWidth(120)
        self.capture_type_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.capture_type_filter)

        self.vendor_filter = QComboBox()
        self.vendor_filter.setMinimumWidth(120)
        self.vendor_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.vendor_filter)

        self.enabled_filter = QComboBox()
        self.enabled_filter.addItems(["All Jobs", "Enabled", "Disabled"])
        self.enabled_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.enabled_filter)

        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("secondary", True)
        clear_btn.clicked.connect(self._clear_filters)
        filter_layout.addWidget(clear_btn)

        layout.addLayout(filter_layout)

        # Jobs table
        self.jobs_table = QTableWidget()
        self.jobs_table.setColumnCount(8)
        self.jobs_table.setHorizontalHeaderLabels([
            "Name", "Type", "Vendor", "Command", "Workers",
            "Runs", "Last Run", "Status"
        ])
        self.jobs_table.setAlternatingRowColors(True)
        self.jobs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.jobs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.jobs_table.horizontalHeader().setStretchLastSection(True)
        self.jobs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.jobs_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.jobs_table.verticalHeader().setVisible(False)
        self.jobs_table.setSortingEnabled(True)

        # Context menu
        self.jobs_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.jobs_table.customContextMenuRequested.connect(self._show_context_menu)

        # Double-click handler
        self.jobs_table.doubleClicked.connect(self._on_double_click)

        # Selection changed
        self.jobs_table.itemSelectionChanged.connect(self._on_selection_changed)

        layout.addWidget(self.jobs_table)

        # Status bar
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

    def init_shortcuts(self):
        """Set up keyboard shortcuts."""
        QShortcut(QKeySequence("Ctrl+N"), self, self._on_add_job)
        QShortcut(QKeySequence("Return"), self, self._edit_selected)
        QShortcut(QKeySequence("Delete"), self, self._delete_selected)
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.search_input.setFocus())
        QShortcut(QKeySequence("F5"), self, self.refresh_data)
        QShortcut(QKeySequence("Ctrl+R"), self, self._run_selected)  # Run job

    def load_filters(self):
        """Load filter dropdown options from database."""
        # Capture types
        self.capture_type_filter.clear()
        self.capture_type_filter.addItem("All Types", None)
        capture_types = self.repo.get_capture_types()
        for ct in capture_types:
            self.capture_type_filter.addItem(ct.upper(), ct)

        # Vendors
        self.vendor_filter.clear()
        self.vendor_filter.addItem("All Vendors", None)
        vendors = self.repo.get_vendors()
        for vendor in vendors:
            self.vendor_filter.addItem(vendor.title(), vendor)

    def refresh_data(self):
        """Refresh stats, filters, and job list."""
        self.load_filters()
        self._update_stats()
        self._load_jobs()

    def _update_stats(self):
        """Update stat cards from database."""
        try:
            stats = self.repo.get_stats()
            self.total_jobs.set_value(str(stats.get('total_jobs', 0)))
            self.enabled_jobs.set_value(str(stats.get('enabled_jobs', 0)))
            self.total_runs.set_value(str(stats.get('total_runs', 0)))
            self.successful_runs.set_value(str(stats.get('successful_runs', 0)))
        except Exception as e:
            self.status_label.setText(f"Error loading stats: {e}")

    def _load_jobs(self):
        """Load jobs with current filters applied."""
        try:
            kwargs = {}

            # Capture type filter
            capture_type = self.capture_type_filter.currentData()
            if capture_type:
                kwargs['capture_type'] = capture_type

            # Vendor filter
            vendor = self.vendor_filter.currentData()
            if vendor:
                kwargs['vendor'] = vendor

            # Enabled filter
            enabled_idx = self.enabled_filter.currentIndex()
            if enabled_idx == 1:
                kwargs['is_enabled'] = True
            elif enabled_idx == 2:
                kwargs['is_enabled'] = False

            # Search
            search_text = self.search_input.text().strip()
            if search_text:
                kwargs['search'] = search_text

            self._jobs = self.repo.get_jobs(**kwargs)
            self._populate_table()
            self.status_label.setText(f"Showing {len(self._jobs)} jobs")

        except Exception as e:
            self.status_label.setText(f"Error loading jobs: {e}")
            self._jobs = []
            self._populate_table()

    def _populate_table(self):
        """Populate table with current job list."""
        self.jobs_table.setSortingEnabled(False)
        self.jobs_table.setRowCount(len(self._jobs))

        for row, job in enumerate(self._jobs):
            # Store job ID in first column
            name_item = QTableWidgetItem(job.name or "")
            name_item.setData(Qt.ItemDataRole.UserRole, job.id)
            # Gray out disabled jobs
            if not job.is_enabled:
                name_item.setForeground(QColor('#7f8c8d'))
            self.jobs_table.setItem(row, 0, name_item)

            # Capture type
            type_item = QTableWidgetItem((job.capture_type or "").upper())
            if not job.is_enabled:
                type_item.setForeground(QColor('#7f8c8d'))
            self.jobs_table.setItem(row, 1, type_item)

            # Vendor
            vendor_item = QTableWidgetItem((job.vendor or "—").title())
            if not job.is_enabled:
                vendor_item.setForeground(QColor('#7f8c8d'))
            self.jobs_table.setItem(row, 2, vendor_item)

            # Command (truncated)
            cmd = job.command or ""
            cmd_display = cmd[:40] + "..." if len(cmd) > 40 else cmd
            cmd_item = QTableWidgetItem(cmd_display)
            cmd_item.setToolTip(cmd)  # Full command on hover
            if not job.is_enabled:
                cmd_item.setForeground(QColor('#7f8c8d'))
            self.jobs_table.setItem(row, 3, cmd_item)

            # Workers
            workers_item = QTableWidgetItem(str(job.max_workers or 10))
            if not job.is_enabled:
                workers_item.setForeground(QColor('#7f8c8d'))
            self.jobs_table.setItem(row, 4, workers_item)

            # Run count
            runs_item = QTableWidgetItem(str(job.run_count or 0))
            if not job.is_enabled:
                runs_item.setForeground(QColor('#7f8c8d'))
            self.jobs_table.setItem(row, 5, runs_item)

            # Last run
            last_run = self._format_relative_time(job.last_run_at)
            last_run_item = QTableWidgetItem(last_run)
            if not job.is_enabled:
                last_run_item.setForeground(QColor('#7f8c8d'))
            self.jobs_table.setItem(row, 6, last_run_item)

            # Status
            status = job.last_run_status or ("enabled" if job.is_enabled else "disabled")
            status_item = QTableWidgetItem(status)
            status_color = self._get_status_color(status, job.is_enabled)
            if status_color:
                status_item.setForeground(status_color)
            self.jobs_table.setItem(row, 7, status_item)

        self.jobs_table.setSortingEnabled(True)

    def _get_status_color(self, status: Optional[str], is_enabled: bool) -> Optional[QColor]:
        """Get color for job status."""
        if not is_enabled:
            return QColor('#7f8c8d')  # Gray for disabled

        colors = {
            'success': QColor('#2ecc71'),  # Green
            'enabled': QColor('#2ecc71'),  # Green
            'partial': QColor('#f39c12'),  # Orange
            'running': QColor('#3498db'),  # Blue
            'failed': QColor('#e74c3c'),  # Red
            'disabled': QColor('#7f8c8d'),  # Gray
        }
        return colors.get(status)

    def _format_relative_time(self, timestamp: Optional[str]) -> str:
        """Format timestamp as relative time."""
        if not timestamp:
            return "Never"

        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            now = datetime.now()

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
        self._search_timer.start(300)

    def _do_search(self):
        """Execute search after debounce."""
        self._load_jobs()

    def _on_filter_changed(self, index: int):
        """Handle filter dropdown changes."""
        self._load_jobs()

    def _clear_filters(self):
        """Clear all filters and search."""
        self.search_input.clear()
        self.capture_type_filter.setCurrentIndex(0)
        self.vendor_filter.setCurrentIndex(0)
        self.enabled_filter.setCurrentIndex(0)
        self._load_jobs()

    def _on_selection_changed(self):
        """Handle table selection change."""
        selected = self.jobs_table.selectedItems()
        if selected:
            job_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if job_id:
                self.job_selected.emit(job_id)

    def _on_double_click(self, index):
        """Handle double-click on job row."""
        row = index.row()
        if row >= 0:
            job_id = self.jobs_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if job_id:
                self.job_double_clicked.emit(job_id)
                self._show_job_detail(job_id)

    def _show_context_menu(self, pos):
        """Show context menu for job actions."""
        item = self.jobs_table.itemAt(pos)
        if not item:
            return

        row = item.row()
        job_id = self.jobs_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not job_id:
            return

        job = self._get_job_by_id(job_id)
        if not job:
            return

        menu = QMenu(self)

        # Run job
        run_action = QAction("▶ Run Job", self)
        run_action.triggered.connect(lambda: self._run_job(job_id))
        if not job.is_enabled:
            run_action.setEnabled(False)
        menu.addAction(run_action)

        menu.addSeparator()

        # View details
        view_action = QAction("View Details", self)
        view_action.triggered.connect(lambda: self._show_job_detail(job_id))
        menu.addAction(view_action)

        # Edit
        edit_action = QAction("Edit Job", self)
        edit_action.triggered.connect(lambda: self._edit_job(job_id))
        menu.addAction(edit_action)

        # Duplicate
        duplicate_action = QAction("Duplicate Job", self)
        duplicate_action.triggered.connect(lambda: self._duplicate_job(job_id))
        menu.addAction(duplicate_action)

        menu.addSeparator()

        # Enable/Disable toggle
        if job.is_enabled:
            toggle_action = QAction("Disable Job", self)
            toggle_action.triggered.connect(lambda: self._toggle_job_enabled(job_id, False))
        else:
            toggle_action = QAction("Enable Job", self)
            toggle_action.triggered.connect(lambda: self._toggle_job_enabled(job_id, True))
        menu.addAction(toggle_action)

        menu.addSeparator()

        # Copy actions
        copy_menu = menu.addMenu("Copy")

        copy_name = QAction("Copy Name", self)
        copy_name.triggered.connect(lambda: self._copy_to_clipboard(job.name))
        copy_menu.addAction(copy_name)

        copy_cmd = QAction("Copy Command", self)
        copy_cmd.triggered.connect(lambda: self._copy_to_clipboard(job.command))
        copy_menu.addAction(copy_cmd)

        menu.addSeparator()

        # View history
        history_action = QAction("View Run History", self)
        history_action.triggered.connect(lambda: self._view_job_history(job_id))
        menu.addAction(history_action)

        menu.addSeparator()

        # Delete
        delete_action = QAction("Delete Job", self)
        delete_action.triggered.connect(lambda: self._delete_job(job_id, job.name))
        menu.addAction(delete_action)

        menu.exec(self.jobs_table.mapToGlobal(pos))

    def _get_job_by_id(self, job_id: int) -> Optional[Job]:
        """Get job from current list by ID."""
        for job in self._jobs:
            if job.id == job_id:
                return job
        return None

    def _on_add_job(self):
        """Open dialog to create a new job."""
        dialog = JobEditDialog(self.repo, job=None, parent=self)
        dialog.job_saved.connect(self._on_job_saved)
        dialog.exec()

    def _show_job_detail(self, job_id: int):
        """Show job detail dialog."""
        job = self.repo.get_job(job_id=job_id)
        if job:
            dialog = JobDetailDialog(job, self)
            dialog.edit_requested.connect(self._edit_job)
            dialog.run_requested.connect(self._run_job)
            dialog.exec()

    def _edit_job(self, job_id: int):
        """Open edit dialog for job."""
        job = self.repo.get_job(job_id=job_id)
        if job:
            dialog = JobEditDialog(self.repo, job=job, parent=self)
            dialog.job_saved.connect(self._on_job_saved)
            dialog.exec()

    def _edit_selected(self):
        """Edit the currently selected job."""
        job_id = self.get_selected_job_id()
        if job_id:
            self._edit_job(job_id)

    def _delete_selected(self):
        """Delete the currently selected job."""
        job = self.get_selected_job()
        if job:
            self._delete_job(job.id, job.name)

    def _run_selected(self):
        """Run the currently selected job."""
        job_id = self.get_selected_job_id()
        if job_id:
            self._run_job(job_id)

    def _run_job(self, job_id: int):
        """Request to run a job."""
        job = self.repo.get_job(job_id=job_id)
        if job and job.is_enabled:
            self.run_job_requested.emit(job_id)
            self.status_label.setText(f"Run requested: {job.name}")
        elif job and not job.is_enabled:
            QMessageBox.warning(self, "Job Disabled",
                                f"Cannot run disabled job '{job.name}'.\n\nEnable the job first.")

    def _duplicate_job(self, job_id: int):
        """Create a copy of a job."""
        job = self.repo.get_job(job_id=job_id)
        if not job:
            return

        # Generate new name/slug
        new_name = f"{job.name} (Copy)"
        new_slug = f"{job.slug}-copy"

        # Check for existing copies and increment
        existing = self.repo.get_job(slug=new_slug)
        counter = 1
        while existing:
            counter += 1
            new_slug = f"{job.slug}-copy-{counter}"
            new_name = f"{job.name} (Copy {counter})"
            existing = self.repo.get_job(slug=new_slug)

        try:
            new_id = self.repo.duplicate_job(job_id, new_name, new_slug)
            if new_id:
                self.refresh_data()
                self.status_label.setText(f"Duplicated job: {new_name}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to duplicate job: {e}")

    def _toggle_job_enabled(self, job_id: int, enabled: bool):
        """Enable or disable a job."""
        try:
            self.repo.set_job_enabled(job_id, enabled)
            self.refresh_data()
            status = "enabled" if enabled else "disabled"
            self.status_label.setText(f"Job {status}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to update job: {e}")

    def _view_job_history(self, job_id: int):
        """View run history for a job."""
        # This would open a history dialog or switch to history view
        # For now, just emit a message
        job = self.repo.get_job(job_id=job_id)
        if job:
            self.status_label.setText(f"History for: {job.name} ({job.run_count} runs)")
            # TODO: Open history dialog or emit signal

    def _delete_job(self, job_id: int, job_name: str):
        """Confirm and delete a job."""
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete job '{job_name}'?\n\n"
            "This will not delete job history or captured data.\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.repo.delete_job(job_id)
                if success:
                    self.status_label.setText(f"Deleted job: {job_name}")
                    self.refresh_data()
                else:
                    QMessageBox.warning(self, "Error", "Failed to delete job")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Error deleting job: {e}")

    def _on_job_saved(self, job_id: int):
        """Handle job saved signal from edit dialog."""
        self.refresh_data()
        self.status_label.setText(f"Job saved (ID: {job_id})")

    def _copy_to_clipboard(self, text: str):
        """Copy text to clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.status_label.setText(f"Copied: {text[:50]}..." if len(text) > 50 else f"Copied: {text}")

    def get_selected_job(self) -> Optional[Job]:
        """Get currently selected job."""
        selected = self.jobs_table.selectedItems()
        if selected:
            job_id = selected[0].data(Qt.ItemDataRole.UserRole)
            return self._get_job_by_id(job_id)
        return None

    def get_selected_job_id(self) -> Optional[int]:
        """Get ID of currently selected job."""
        selected = self.jobs_table.selectedItems()
        if selected:
            return selected[0].data(Qt.ItemDataRole.UserRole)
        return None


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow

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

    window = QMainWindow()
    window.setWindowTitle("Job Management - VelocityCollector")
    window.resize(1200, 700)

    view = JobsView()
    window.setCentralWidget(view)

    view.job_selected.connect(lambda id: print(f"Selected job ID: {id}"))
    view.job_double_clicked.connect(lambda id: print(f"Double-clicked job ID: {id}"))
    view.run_job_requested.connect(lambda id: print(f"Run requested for job ID: {id}"))

    window.show()
    sys.exit(app.exec())