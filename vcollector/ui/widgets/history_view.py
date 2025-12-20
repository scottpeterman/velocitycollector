"""
VelocityCollector History View

Job execution history browser showing past collection runs.
Displays success/failure stats, duration, device counts.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QAbstractItemView,
    QMenu, QMessageBox, QDialog, QFormLayout, QTextEdit, QFrame, QScrollArea
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QShortcut, QKeySequence

from vcollector.dcim.jobs_repo import JobsRepository, JobHistory

from typing import Optional, List
from datetime import datetime, timedelta


class HistoryDetailDialog(QDialog):
    """Dialog showing full details of a job history record."""

    rerun_requested = pyqtSignal(str)  # job_id/slug

    def __init__(self, history: JobHistory, parent=None):
        super().__init__(parent)
        self.history = history
        self.setWindowTitle(f"Run Details - {history.job_name or history.job_id}")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Header
        header = QLabel(self.history.job_name or self.history.job_id)
        header.setProperty("heading", True)
        layout.addWidget(header)

        # Status banner
        status_frame = QFrame()
        status_frame.setProperty("card", True)
        status_layout = QHBoxLayout(status_frame)

        status_label = QLabel(f"Status: {self.history.status or 'unknown'}")
        status_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {self._status_color()};")
        status_layout.addWidget(status_label)

        status_layout.addStretch()

        if self.history.total_devices:
            devices_label = QLabel(f"{self.history.success_count or 0}/{self.history.total_devices} devices")
            devices_label.setStyleSheet("font-size: 14px;")
            status_layout.addWidget(devices_label)

        layout.addWidget(status_frame)

        # Details form
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        form.addRow("Job ID:", self._label(self.history.job_id))
        form.addRow("Job Name:", self._label(self.history.job_name))

        if self.history.capture_type:
            form.addRow("Capture Type:", self._label(self.history.capture_type.upper()))

        if self.history.vendor:
            form.addRow("Vendor:", self._label(self.history.vendor.title()))

        form.addRow(self._separator())

        form.addRow("Started:", self._label(self._format_timestamp(self.history.started_at)))
        form.addRow("Completed:", self._label(self._format_timestamp(self.history.completed_at)))
        form.addRow("Duration:", self._label(self._calc_duration()))

        form.addRow(self._separator())

        form.addRow("Total Devices:", self._label(str(self.history.total_devices or 0)))
        form.addRow("Successful:", self._label(str(self.history.success_count or 0), color='#2ecc71'))
        form.addRow("Failed:", self._label(str(self.history.failed_count or 0),
                                           color='#e74c3c' if self.history.failed_count else None))

        if self.history.job_file:
            form.addRow(self._separator())
            form.addRow("Source:", self._label(self.history.job_file))

        layout.addLayout(form)

        # Error message (if any)
        if self.history.error_message:
            layout.addWidget(QLabel("Error Message:"))
            error_text = QTextEdit()
            error_text.setPlainText(self.history.error_message)
            error_text.setReadOnly(True)
            error_text.setMaximumHeight(100)
            error_text.setStyleSheet("color: #e74c3c;")
            layout.addWidget(error_text)

        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()

        if self.history.job_id:
            rerun_btn = QPushButton("▶ Re-run Job")
            rerun_btn.clicked.connect(self._on_rerun)
            button_layout.addWidget(rerun_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _label(self, text: Optional[str], color: Optional[str] = None) -> QLabel:
        label = QLabel(text or "—")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if color:
            label.setStyleSheet(f"color: {color}; font-weight: bold;")
        return label

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _status_color(self) -> str:
        colors = {
            'success': '#2ecc71',
            'partial': '#f39c12',
            'failed': '#e74c3c',
            'running': '#3498db',
        }
        return colors.get(self.history.status, '#888')

    def _format_timestamp(self, ts: Optional[str]) -> str:
        if not ts:
            return "—"
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            return ts

    def _calc_duration(self) -> str:
        if not self.history.started_at or not self.history.completed_at:
            return "—"
        try:
            start = datetime.fromisoformat(self.history.started_at.replace('Z', '+00:00'))
            end = datetime.fromisoformat(self.history.completed_at.replace('Z', '+00:00'))
            delta = end - start

            if delta.total_seconds() < 60:
                return f"{delta.total_seconds():.1f}s"
            elif delta.total_seconds() < 3600:
                minutes = int(delta.total_seconds() // 60)
                seconds = int(delta.total_seconds() % 60)
                return f"{minutes}m {seconds}s"
            else:
                hours = int(delta.total_seconds() // 3600)
                minutes = int((delta.total_seconds() % 3600) // 60)
                return f"{hours}h {minutes}m"
        except (ValueError, AttributeError):
            return "—"

    def _on_rerun(self):
        self.rerun_requested.emit(self.history.job_id)
        self.accept()


class HistoryView(QWidget):
    """Job execution history browser."""

    # Signals
    rerun_job_requested = pyqtSignal(str)  # job_id/slug

    def __init__(self, repo: Optional[JobsRepository] = None, parent=None):
        super().__init__(parent)
        self.repo = repo or JobsRepository()
        self._history: List[JobHistory] = []

        self.init_ui()
        self.load_filters()
        self.refresh_data()

    def init_ui(self):
        # Main layout with scroll area
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Job History")
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

        self.total_runs_card = self._create_stat_card("Total Runs", "0")
        stats_layout.addWidget(self.total_runs_card)

        self.success_card = self._create_stat_card("Successful", "0", "#2ecc71")
        stats_layout.addWidget(self.success_card)

        self.partial_card = self._create_stat_card("Partial", "0", "#f39c12")
        stats_layout.addWidget(self.partial_card)

        self.failed_card = self._create_stat_card("Failed", "0", "#e74c3c")
        stats_layout.addWidget(self.failed_card)

        stats_layout.addStretch()
        layout.addLayout(stats_layout)

        # Filter bar
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(12)

        self.job_filter = QComboBox()
        self.job_filter.setMinimumWidth(200)
        self.job_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(QLabel("Job:"))
        filter_layout.addWidget(self.job_filter)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["All Status", "Success", "Partial", "Failed", "Running"])
        self.status_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(QLabel("Status:"))
        filter_layout.addWidget(self.status_filter)

        self.time_filter = QComboBox()
        self.time_filter.addItems(["All Time", "Last 24 Hours", "Last 7 Days", "Last 30 Days"])
        self.time_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(QLabel("Time:"))
        filter_layout.addWidget(self.time_filter)

        filter_layout.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("secondary", True)
        clear_btn.clicked.connect(self._clear_filters)
        filter_layout.addWidget(clear_btn)

        layout.addLayout(filter_layout)

        # History table
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(8)
        self.history_table.setHorizontalHeaderLabels([
            "Job", "Type", "Started", "Duration", "Devices", "Success", "Failed", "Status"
        ])
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSortingEnabled(True)
        self.history_table.setMinimumHeight(400)

        # Context menu
        self.history_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self._show_context_menu)

        # Double-click
        self.history_table.doubleClicked.connect(self._on_double_click)

        layout.addWidget(self.history_table)

        # Status bar
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # Shortcuts
        QShortcut(QKeySequence("F5"), self, self.refresh_data)
        QShortcut(QKeySequence("Return"), self, self._view_selected)
        QShortcut(QKeySequence("Delete"), self, self._delete_selected)

    def _create_stat_card(self, label: str, value: str, color: Optional[str] = None) -> QFrame:
        """Create a stat card widget."""
        card = QFrame()
        card.setProperty("card", True)
        card.setMinimumWidth(120)
        card.setMaximumWidth(160)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        value_label = QLabel(value)
        value_label.setProperty("stat", True)
        if color:
            value_label.setStyleSheet(f"color: {color};")
        value_label.setObjectName(f"stat_value_{label.lower().replace(' ', '_')}")
        layout.addWidget(value_label)

        text_label = QLabel(label.upper())
        text_label.setProperty("stat_label", True)
        layout.addWidget(text_label)

        return card

    def _update_stat_card(self, card: QFrame, value: str):
        """Update a stat card's value."""
        for child in card.findChildren(QLabel):
            if child.property("stat"):
                child.setText(value)
                break

    def load_filters(self):
        """Load job filter dropdown."""
        self.job_filter.clear()
        self.job_filter.addItem("All Jobs", None)

        jobs = self.repo.get_jobs()
        for job in jobs:
            self.job_filter.addItem(job.name, job.slug)

    def refresh_data(self):
        """Refresh stats and history list."""
        self._update_stats()
        self._load_history()

    def _update_stats(self):
        """Update stat cards."""
        try:
            stats = self.repo.get_stats()
            self._update_stat_card(self.total_runs_card, str(stats.get('total_runs', 0)))
            self._update_stat_card(self.success_card, str(stats.get('successful_runs', 0)))
            self._update_stat_card(self.failed_card, str(stats.get('failed_runs', 0)))

            # Calculate partial (total - success - failed)
            total = stats.get('total_runs', 0)
            success = stats.get('successful_runs', 0)
            failed = stats.get('failed_runs', 0)
            partial = total - success - failed
            self._update_stat_card(self.partial_card, str(max(0, partial)))

        except Exception as e:
            self.status_label.setText(f"Error loading stats: {e}")

    def _load_history(self):
        """Load history with current filters."""
        try:
            kwargs = {'limit': 100}

            # Job filter
            job_slug = self.job_filter.currentData()
            if job_slug:
                kwargs['job_slug'] = job_slug

            # Status filter
            status_idx = self.status_filter.currentIndex()
            status_map = {1: 'success', 2: 'partial', 3: 'failed', 4: 'running'}
            if status_idx in status_map:
                kwargs['status'] = status_map[status_idx]

            self._history = self.repo.get_job_history_list(**kwargs)

            # Time filter (client-side for simplicity)
            time_idx = self.time_filter.currentIndex()
            if time_idx > 0:
                now = datetime.now()
                cutoffs = {
                    1: now - timedelta(hours=24),
                    2: now - timedelta(days=7),
                    3: now - timedelta(days=30),
                }
                cutoff = cutoffs.get(time_idx)
                if cutoff:
                    filtered = []
                    for h in self._history:
                        try:
                            started = datetime.fromisoformat(h.started_at.replace('Z', '+00:00'))
                            if started.replace(tzinfo=None) >= cutoff:
                                filtered.append(h)
                        except (ValueError, AttributeError):
                            pass
                    self._history = filtered

            self._populate_table()
            self.status_label.setText(f"Showing {len(self._history)} records")

        except Exception as e:
            self.status_label.setText(f"Error loading history: {e}")
            self._history = []
            self._populate_table()

    def _populate_table(self):
        """Populate table with history records."""
        self.history_table.setSortingEnabled(False)
        self.history_table.setRowCount(len(self._history))

        for row, h in enumerate(self._history):
            # Job name (store ID in UserRole)
            job_item = QTableWidgetItem(h.job_name or h.job_id or "—")
            job_item.setData(Qt.ItemDataRole.UserRole, h.id)
            self.history_table.setItem(row, 0, job_item)

            # Capture type
            type_item = QTableWidgetItem((h.capture_type or "—").upper())
            self.history_table.setItem(row, 1, type_item)

            # Started
            started = self._format_timestamp(h.started_at)
            self.history_table.setItem(row, 2, QTableWidgetItem(started))

            # Duration
            duration = self._calc_duration(h.started_at, h.completed_at)
            self.history_table.setItem(row, 3, QTableWidgetItem(duration))

            # Total devices
            self.history_table.setItem(row, 4, QTableWidgetItem(str(h.total_devices or 0)))

            # Success count
            success_item = QTableWidgetItem(str(h.success_count or 0))
            if h.success_count and h.success_count > 0:
                success_item.setForeground(QColor('#2ecc71'))
            self.history_table.setItem(row, 5, success_item)

            # Failed count
            failed_item = QTableWidgetItem(str(h.failed_count or 0))
            if h.failed_count and h.failed_count > 0:
                failed_item.setForeground(QColor('#e74c3c'))
            self.history_table.setItem(row, 6, failed_item)

            # Status
            status = h.status or "unknown"
            status_item = QTableWidgetItem(status)
            status_color = self._get_status_color(status)
            if status_color:
                status_item.setForeground(status_color)
            self.history_table.setItem(row, 7, status_item)

        self.history_table.setSortingEnabled(True)

    def _format_timestamp(self, ts: Optional[str]) -> str:
        if not ts:
            return "—"
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            now = datetime.now()
            if dt.tzinfo:
                dt = dt.replace(tzinfo=None)

            # If today, show time only
            if dt.date() == now.date():
                return dt.strftime("%H:%M:%S")
            # If this year, show month/day
            elif dt.year == now.year:
                return dt.strftime("%m/%d %H:%M")
            else:
                return dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            return ts or "—"

    def _calc_duration(self, start: Optional[str], end: Optional[str]) -> str:
        if not start or not end:
            return "—"
        try:
            s = datetime.fromisoformat(start.replace('Z', '+00:00'))
            e = datetime.fromisoformat(end.replace('Z', '+00:00'))
            delta = e - s

            if delta.total_seconds() < 60:
                return f"{delta.total_seconds():.1f}s"
            elif delta.total_seconds() < 3600:
                return f"{int(delta.total_seconds() // 60)}m {int(delta.total_seconds() % 60)}s"
            else:
                return f"{int(delta.total_seconds() // 3600)}h {int((delta.total_seconds() % 3600) // 60)}m"
        except (ValueError, AttributeError):
            return "—"

    def _get_status_color(self, status: str) -> Optional[QColor]:
        colors = {
            'success': QColor('#2ecc71'),
            'partial': QColor('#f39c12'),
            'failed': QColor('#e74c3c'),
            'running': QColor('#3498db'),
        }
        return colors.get(status)

    def _on_filter_changed(self, index: int):
        """Handle filter change."""
        self._load_history()

    def _clear_filters(self):
        """Clear all filters."""
        self.job_filter.setCurrentIndex(0)
        self.status_filter.setCurrentIndex(0)
        self.time_filter.setCurrentIndex(0)
        self._load_history()

    def _on_double_click(self, index):
        """Handle double-click on row."""
        row = index.row()
        if row >= 0:
            history_id = self.history_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if history_id:
                self._show_detail(history_id)

    def _show_context_menu(self, pos):
        """Show context menu."""
        item = self.history_table.itemAt(pos)
        if not item:
            return

        row = item.row()
        history_id = self.history_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not history_id:
            return

        history = self._get_history_by_id(history_id)
        if not history:
            return

        menu = QMenu(self)

        # View details
        view_action = QAction("View Details", self)
        view_action.triggered.connect(lambda: self._show_detail(history_id))
        menu.addAction(view_action)

        menu.addSeparator()

        # Re-run job
        if history.job_id:
            rerun_action = QAction("▶ Re-run Job", self)
            rerun_action.triggered.connect(lambda: self._rerun_job(history.job_id))
            menu.addAction(rerun_action)

        menu.addSeparator()

        # Delete record
        delete_action = QAction("Delete Record", self)
        delete_action.triggered.connect(lambda: self._delete_history(history_id))
        menu.addAction(delete_action)

        menu.exec(self.history_table.mapToGlobal(pos))

    def _get_history_by_id(self, history_id: int) -> Optional[JobHistory]:
        """Get history record from current list."""
        for h in self._history:
            if h.id == history_id:
                return h
        return None

    def _show_detail(self, history_id: int):
        """Show detail dialog."""
        history = self.repo.get_job_history(history_id)
        if history:
            dialog = HistoryDetailDialog(history, self)
            dialog.rerun_requested.connect(self._rerun_job)
            dialog.exec()

    def _view_selected(self):
        """View selected history record."""
        selected = self.history_table.selectedItems()
        if selected:
            history_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if history_id:
                self._show_detail(history_id)

    def _delete_selected(self):
        """Delete selected history record."""
        selected = self.history_table.selectedItems()
        if selected:
            history_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if history_id:
                self._delete_history(history_id)

    def _rerun_job(self, job_id: str):
        """Request to re-run a job."""
        self.rerun_job_requested.emit(job_id)
        self.status_label.setText(f"Re-run requested: {job_id}")

    def _delete_history(self, history_id: int):
        """Delete a history record."""
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            "Delete this history record?\n\nThis will not affect captured output files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Need to add delete method to repo
                self.repo.conn.execute("DELETE FROM job_history WHERE id = ?", (history_id,))
                self.repo.conn.commit()
                self.refresh_data()
                self.status_label.setText("Record deleted")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to delete: {e}")


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)

    window = QMainWindow()
    window.setWindowTitle("Job History - VelocityCollector")
    window.resize(1000, 600)

    view = HistoryView()
    window.setCentralWidget(view)

    view.rerun_job_requested.connect(lambda job_id: print(f"Re-run requested: {job_id}"))

    window.show()
    sys.exit(app.exec())