"""
VelocityCollector Run View

Job execution view for running collection jobs from the GUI.
Provides job selection, execution control, and real-time progress display.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTextEdit, QProgressBar, QGroupBox, QFormLayout, QSpinBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QSplitter, QFrame, QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QTextCursor

from vcollector.dcim.jobs_repo import JobsRepository, Job
from vcollector.vault.resolver import CredentialResolver

from typing import Optional, List
from datetime import datetime
from dataclasses import dataclass
import traceback


@dataclass
class DeviceProgress:
    """Progress update for a single device."""
    device_name: str
    host: str
    success: bool
    duration_ms: float
    error: Optional[str] = None


class JobExecutionThread(QThread):
    """Background thread for job execution."""

    # Signals
    progress = pyqtSignal(int, int, object)  # completed, total, DeviceProgress
    log_message = pyqtSignal(str)  # Log message
    finished_job = pyqtSignal(object)  # JobResult
    error = pyqtSignal(str)  # Error message

    def __init__(self, job_id: int, vault_password: str, options: dict):
        super().__init__()
        self.job_id = job_id
        self.vault_password = vault_password
        self.options = options
        self._cancelled = False

    def run(self):
        try:
            from vcollector.jobs.runner import JobRunner
            from vcollector.vault.resolver import CredentialResolver

            # Unlock vault
            self.log_message.emit("Unlocking vault...")
            resolver = CredentialResolver()

            if not resolver.is_initialized():
                self.error.emit("Vault not initialized. Run 'vcollector vault init' first.")
                return

            if not resolver.unlock_vault(self.vault_password):
                self.error.emit("Invalid vault password")
                return

            try:
                # Get credentials
                creds = resolver.get_ssh_credentials()
                if not creds:
                    self.error.emit("No credentials in vault")
                    return

                self.log_message.emit(f"Using credentials: {creds.username}")

                # Create runner
                runner = JobRunner(
                    credentials=creds,
                    validate=self.options.get('validate', True),
                    debug=self.options.get('debug', False),
                    no_save=self.options.get('no_save', False),
                    force_save=self.options.get('force_save', False),
                    limit=self.options.get('limit'),
                    quiet=False,
                    record_history=True,
                )

                # Progress callback
                def on_progress(completed, total, result):
                    if self._cancelled:
                        return
                    progress = DeviceProgress(
                        device_name=result.device_name if hasattr(result, 'device_name') else str(result),
                        host=result.host if hasattr(result, 'host') else '',
                        success=result.success if hasattr(result, 'success') else True,
                        duration_ms=result.duration_ms if hasattr(result, 'duration_ms') else 0,
                        error=result.error if hasattr(result, 'error') else None,
                    )
                    self.progress.emit(completed, total, progress)

                # Run job
                self.log_message.emit(f"Starting job execution...")
                result = runner.run_job(job_id=self.job_id, progress_callback=on_progress)

                if not self._cancelled:
                    self.finished_job.emit(result)

            finally:
                resolver.lock_vault()

        except Exception as e:
            self.error.emit(f"Execution error: {str(e)}\n{traceback.format_exc()}")

    def cancel(self):
        """Request cancellation."""
        self._cancelled = True


class RunView(QWidget):
    """Job execution view with scrollable content."""

    def __init__(self, repo: Optional[JobsRepository] = None, parent=None):
        super().__init__(parent)
        self.repo = repo or JobsRepository()
        self._jobs: List[Job] = []
        self._execution_thread: Optional[JobExecutionThread] = None
        self._start_time: Optional[datetime] = None

        self.init_ui()
        self.refresh_jobs()

    def init_ui(self):
        # Main layout - holds the scroll area
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        # Create content widget that will be scrollable
        content_widget = QWidget()
        content_widget.setObjectName("scrollContent")
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Run Collection Job")
        title.setProperty("heading", True)
        header_layout.addWidget(title)
        header_layout.addStretch()

        refresh_btn = QPushButton("Refresh Jobs")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh_jobs)
        header_layout.addWidget(refresh_btn)

        layout.addLayout(header_layout)

        # === Top: Job Selection and Options ===
        top_layout = QHBoxLayout()
        top_layout.setSpacing(16)

        # Job selection group
        job_group = QGroupBox("Job Selection")
        job_layout = QFormLayout(job_group)

        self.job_combo = QComboBox()
        self.job_combo.setMinimumWidth(300)
        self.job_combo.currentIndexChanged.connect(self._on_job_selected)
        job_layout.addRow("Job:", self.job_combo)

        self.job_info_label = QLabel("Select a job to see details")
        self.job_info_label.setWordWrap(True)
        self.job_info_label.setStyleSheet("color: #888;")
        job_layout.addRow("", self.job_info_label)

        top_layout.addWidget(job_group)

        # Options group
        options_group = QGroupBox("Execution Options")
        options_layout = QFormLayout(options_group)

        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 1000)
        self.limit_spin.setValue(0)
        self.limit_spin.setSpecialValueText("No limit")
        options_layout.addRow("Device Limit:", self.limit_spin)

        self.validate_check = QCheckBox("Validate with TextFSM")
        self.validate_check.setChecked(True)
        options_layout.addRow("", self.validate_check)

        self.force_save_check = QCheckBox("Save even if validation fails")
        self.force_save_check.setChecked(False)
        options_layout.addRow("", self.force_save_check)

        self.debug_check = QCheckBox("Debug output")
        self.debug_check.setChecked(False)
        options_layout.addRow("", self.debug_check)

        top_layout.addWidget(options_group)

        # Credentials group
        creds_group = QGroupBox("Credentials")
        creds_layout = QVBoxLayout(creds_group)

        self.creds_status = QLabel("Not unlocked")
        self.creds_status.setStyleSheet("color: #e74c3c;")
        creds_layout.addWidget(self.creds_status)

        self.vault_status_label = QLabel("")
        creds_layout.addWidget(self.vault_status_label)

        top_layout.addWidget(creds_group)

        layout.addLayout(top_layout)

        # === Middle: Progress ===
        progress_group = QGroupBox("Execution Progress")
        progress_layout = QVBoxLayout(progress_group)

        # Progress bar
        progress_header = QHBoxLayout()
        self.progress_label = QLabel("Ready")
        progress_header.addWidget(self.progress_label)
        progress_header.addStretch()
        self.elapsed_label = QLabel("")
        progress_header.addWidget(self.elapsed_label)
        progress_layout.addLayout(progress_header)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        # Results table
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(4)
        self.results_table.setHorizontalHeaderLabels(["Device", "Host", "Status", "Duration"])
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setMinimumHeight(200)
        self.results_table.setMaximumHeight(300)
        progress_layout.addWidget(self.results_table)

        layout.addWidget(progress_group)

        # === Bottom: Log Output ===
        log_group = QGroupBox("Execution Log")
        log_layout = QVBoxLayout(log_group)

        log_header = QHBoxLayout()
        log_header.addStretch()
        clear_log_btn = QPushButton("Clear Log")
        clear_log_btn.setProperty("secondary", True)
        clear_log_btn.clicked.connect(self._clear_log)
        log_header.addWidget(clear_log_btn)
        log_layout.addLayout(log_header)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(120)
        self.log_output.setMaximumHeight(180)
        self.log_output.setStyleSheet("font-family: monospace;")
        log_layout.addWidget(self.log_output)

        layout.addWidget(log_group)

        # === Control Buttons ===
        button_frame = QFrame()
        button_frame.setProperty("card", True)
        button_layout = QHBoxLayout(button_frame)

        self.run_btn = QPushButton("▶ Run Job")
        self.run_btn.setMinimumWidth(120)
        self.run_btn.setMinimumHeight(40)
        self.run_btn.clicked.connect(self._on_run_clicked)
        button_layout.addWidget(self.run_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setProperty("secondary", True)
        self.cancel_btn.setMinimumHeight(40)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        button_layout.addWidget(self.cancel_btn)

        button_layout.addStretch()

        # Summary labels
        self.summary_label = QLabel("")
        self.summary_label.setProperty("subheading", True)
        button_layout.addWidget(self.summary_label)

        layout.addWidget(button_frame)

        # Add stretch at bottom
        layout.addStretch()

        # Set content widget to scroll area
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # Timer for elapsed time
        self._elapsed_timer = QTimer()
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        # Check vault status
        self._check_vault_status()

    def refresh_jobs(self):
        """Refresh the job list."""
        self.job_combo.clear()
        self._jobs = self.repo.get_jobs(is_enabled=True)

        if not self._jobs:
            self.job_combo.addItem("No enabled jobs found", None)
            return

        for job in self._jobs:
            label = f"{job.name} ({job.capture_type}/{job.vendor or 'any'})"
            self.job_combo.addItem(label, job.id)

        self._on_job_selected(0)

    def _check_vault_status(self):
        """Check if vault is initialized."""
        try:
            resolver = CredentialResolver()
            if resolver.is_initialized():
                creds = resolver.list_credentials()
                if creds:
                    default = next((c for c in creds if c.is_default), creds[0] if creds else None)
                    if default:
                        self.creds_status.setText(f"Available: {default.name}")
                        self.creds_status.setStyleSheet("color: #2ecc71;")
                    else:
                        self.creds_status.setText(f"{len(creds)} credential(s)")
                        self.creds_status.setStyleSheet("color: #f39c12;")
                else:
                    self.creds_status.setText("No credentials")
                    self.creds_status.setStyleSheet("color: #e74c3c;")
            else:
                self.creds_status.setText("Vault not initialized")
                self.creds_status.setStyleSheet("color: #e74c3c;")
        except Exception as e:
            self.creds_status.setText(f"Error: {e}")
            self.creds_status.setStyleSheet("color: #e74c3c;")

    def _on_job_selected(self, index: int):
        """Handle job selection change."""
        job_id = self.job_combo.currentData()
        if not job_id:
            self.job_info_label.setText("Select a job to see details")
            return

        job = self.repo.get_job(job_id=job_id)
        if job:
            info = f"Command: {job.command[:60]}..." if len(job.command) > 60 else f"Command: {job.command}"
            info += f"\nWorkers: {job.max_workers}, Timeout: {job.timeout_seconds}s"
            if job.use_textfsm:
                info += f"\nTextFSM: {job.textfsm_template or 'auto'}"
            self.job_info_label.setText(info)

    def _on_run_clicked(self):
        """Start job execution."""
        job_id = self.job_combo.currentData()
        if not job_id:
            QMessageBox.warning(self, "No Job Selected", "Please select a job to run.")
            return

        # Get vault password
        from PyQt6.QtWidgets import QInputDialog, QLineEdit
        password, ok = QInputDialog.getText(
            self, "Vault Password", "Enter vault password:",
            QLineEdit.EchoMode.Password
        )

        if not ok or not password:
            return

        # Prepare options
        options = {
            'validate': self.validate_check.isChecked(),
            'force_save': self.force_save_check.isChecked(),
            'debug': self.debug_check.isChecked(),
            'limit': self.limit_spin.value() if self.limit_spin.value() > 0 else None,
        }

        # Clear previous results
        self.results_table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting...")
        self.summary_label.setText("")

        # Start execution thread
        self._execution_thread = JobExecutionThread(job_id, password, options)
        self._execution_thread.progress.connect(self._on_progress)
        self._execution_thread.log_message.connect(self._append_log)
        self._execution_thread.finished_job.connect(self._on_job_finished)
        self._execution_thread.error.connect(self._on_error)
        self._execution_thread.finished.connect(self._on_thread_finished)

        # Update UI state
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.job_combo.setEnabled(False)

        # Start timer
        self._start_time = datetime.now()
        self._elapsed_timer.start(1000)

        self._append_log(f"Starting job: {self.job_combo.currentText()}")
        self._execution_thread.start()

    def _on_cancel_clicked(self):
        """Cancel execution."""
        if self._execution_thread:
            self._append_log("Cancellation requested...")
            self._execution_thread.cancel()

    def _on_progress(self, completed: int, total: int, device: DeviceProgress):
        """Handle progress update."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(completed)
        self.progress_label.setText(f"Progress: {completed}/{total} devices")

        # Add row to results table
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)

        self.results_table.setItem(row, 0, QTableWidgetItem(device.device_name))
        self.results_table.setItem(row, 1, QTableWidgetItem(device.host))

        status = "✓" if device.success else "✗"
        status_item = QTableWidgetItem(status)
        status_item.setForeground(QColor('#2ecc71' if device.success else '#e74c3c'))
        self.results_table.setItem(row, 2, status_item)

        duration = f"{device.duration_ms:.0f}ms" if device.duration_ms else "—"
        self.results_table.setItem(row, 3, QTableWidgetItem(duration))

        # Scroll to bottom
        self.results_table.scrollToBottom()

    def _on_job_finished(self, result):
        """Handle job completion."""
        self._elapsed_timer.stop()

        status = "✓ Complete" if result.success else "✗ Failed"
        self.progress_label.setText(status)

        summary = (f"Success: {result.success_count}, "
                  f"Failed: {result.failed_count}, "
                  f"Skipped: {result.skipped_count}")
        self.summary_label.setText(summary)

        self._append_log(f"\n{'='*50}")
        self._append_log(f"Job completed: {status}")
        self._append_log(f"Total devices: {result.total_devices}")
        self._append_log(f"Success: {result.success_count}")
        self._append_log(f"Failed: {result.failed_count}")
        self._append_log(f"Skipped (validation): {result.skipped_count}")
        self._append_log(f"Duration: {result.duration_ms:.0f}ms")

        if result.error:
            self._append_log(f"Error: {result.error}")

        if result.saved_files:
            self._append_log(f"\nSaved {len(result.saved_files)} files")

    def _on_error(self, message: str):
        """Handle execution error."""
        self._elapsed_timer.stop()
        self.progress_label.setText("✗ Error")
        self._append_log(f"\nERROR: {message}")
        QMessageBox.critical(self, "Execution Error", message)

    def _on_thread_finished(self):
        """Handle thread completion."""
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.job_combo.setEnabled(True)
        self._elapsed_timer.stop()
        self._execution_thread = None

    def _update_elapsed(self):
        """Update elapsed time display."""
        if self._start_time:
            elapsed = datetime.now() - self._start_time
            self.elapsed_label.setText(f"Elapsed: {elapsed.seconds}s")

    def _append_log(self, message: str):
        """Append message to log output."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{timestamp}] {message}")
        # Scroll to bottom
        cursor = self.log_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_output.setTextCursor(cursor)

    def _clear_log(self):
        """Clear log output."""
        self.log_output.clear()

    def run_job_by_id(self, job_id: int):
        """
        External entry point to run a specific job.

        Called when "Run" is clicked from Jobs view.
        """
        # Find job in combo
        for i in range(self.job_combo.count()):
            if self.job_combo.itemData(i) == job_id:
                self.job_combo.setCurrentIndex(i)
                break

        # Could auto-trigger run, but let user confirm
        self._append_log(f"Job {job_id} selected. Click 'Run Job' to start.")


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)

    window = QMainWindow()
    window.setWindowTitle("Run Job - VelocityCollector")
    window.resize(900, 700)

    view = RunView()
    window.setCentralWidget(view)

    window.show()
    sys.exit(app.exec())