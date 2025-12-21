"""
VelocityCollector Run View

Job execution view for running collection jobs from the GUI.
Provides job selection, batch execution, real-time progress display,
and per-device credential support.

Supports two modes:
- Single Job: Run one job at a time (existing behavior)
- Batch: Run multiple jobs sequentially from a batch definition file
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTextEdit, QProgressBar, QGroupBox, QFormLayout, QSpinBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QSplitter, QFrame, QScrollArea, QRadioButton, QButtonGroup,
    QListWidget, QListWidgetItem, QInputDialog, QLineEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QTextCursor

from vcollector.dcim.jobs_repo import JobsRepository, Job
from vcollector.vault.resolver import CredentialResolver

from typing import Optional, List
from datetime import datetime
from dataclasses import dataclass
import traceback
import time


@dataclass
class DeviceProgress:
    """Progress update for a single device."""
    device_name: str
    host: str
    success: bool
    duration_ms: float
    error: Optional[str] = None
    credential_name: Optional[str] = None


class JobExecutionThread(QThread):
    """Background thread for single job execution with per-device credential support."""

    # Signals
    progress = pyqtSignal(int, int, object)  # completed, total, DeviceProgress
    log_message = pyqtSignal(str)
    finished_job = pyqtSignal(object)  # JobResult
    error = pyqtSignal(str)

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

            self.log_message.emit("Unlocking vault...")
            resolver = CredentialResolver()

            if not resolver.is_initialized():
                self.error.emit("Vault not initialized. Run 'vcollector vault init' first.")
                return

            if not resolver.unlock_vault(self.vault_password):
                self.error.emit("Invalid vault password")
                return

            try:
                creds = resolver.get_ssh_credentials()
                if not creds:
                    self.error.emit("No credentials in vault")
                    return

                self.log_message.emit(f"Default credential: {creds.username}")

                credential_resolver = None
                use_per_device_creds = self.options.get('use_per_device_creds', True)

                if use_per_device_creds:
                    self.log_message.emit("Per-device credentials: ENABLED")
                    credential_resolver = resolver
                else:
                    self.log_message.emit("Per-device credentials: DISABLED (using default)")

                runner = JobRunner(
                    credentials=creds,
                    credential_resolver=credential_resolver,
                    validate=self.options.get('validate', True),
                    debug=self.options.get('debug', False),
                    no_save=self.options.get('no_save', False),
                    force_save=self.options.get('force_save', False),
                    limit=self.options.get('limit'),
                    quiet=False,
                    record_history=True,
                )

                def on_progress(completed, total, result):
                    if self._cancelled:
                        return
                    progress = DeviceProgress(
                        device_name=result.device_name if hasattr(result, 'device_name') else str(result),
                        host=result.host if hasattr(result, 'host') else '',
                        success=result.success if hasattr(result, 'success') else True,
                        duration_ms=result.duration_ms if hasattr(result, 'duration_ms') else 0,
                        error=result.error if hasattr(result, 'error') else None,
                        credential_name=result.credential_name if hasattr(result, 'credential_name') else None,
                    )
                    self.progress.emit(completed, total, progress)

                self.log_message.emit(f"Starting job execution...")
                result = runner.run_job(job_id=self.job_id, progress_callback=on_progress)

                if not self._cancelled:
                    self.finished_job.emit(result)

            finally:
                resolver.lock_vault()

        except Exception as e:
            self.error.emit(f"Execution error: {str(e)}\n{traceback.format_exc()}")

    def cancel(self):
        self._cancelled = True


class BatchExecutionThread(QThread):
    """Background thread for batch job execution - runs jobs sequentially."""

    # Signals
    job_starting = pyqtSignal(int, int, str)  # job_index, total_jobs, job_slug
    job_progress = pyqtSignal(int, int, object)  # completed, total, DeviceProgress
    job_finished = pyqtSignal(int, str, object)  # job_index, job_slug, JobResult
    log_message = pyqtSignal(str)
    batch_finished = pyqtSignal(list)  # List of (job_slug, JobResult)
    error = pyqtSignal(str)

    def __init__(self, job_slugs: List[str], vault_password: str, options: dict):
        super().__init__()
        self.job_slugs = job_slugs
        self.vault_password = vault_password
        self.options = options
        self._cancelled = False

    def run(self):
        results = []

        try:
            from vcollector.jobs.runner import JobRunner
            from vcollector.vault.resolver import CredentialResolver
            from vcollector.dcim.jobs_repo import JobsRepository

            self.log_message.emit("Unlocking vault...")
            resolver = CredentialResolver()

            if not resolver.is_initialized():
                self.error.emit("Vault not initialized. Run 'vcollector vault init' first.")
                return

            if not resolver.unlock_vault(self.vault_password):
                self.error.emit("Invalid vault password")
                return

            try:
                creds = resolver.get_ssh_credentials()
                if not creds:
                    self.error.emit("No credentials in vault")
                    return

                self.log_message.emit(f"Default credential: {creds.username}")

                credential_resolver = None
                use_per_device_creds = self.options.get('use_per_device_creds', True)

                if use_per_device_creds:
                    self.log_message.emit("Per-device credentials: ENABLED")
                    credential_resolver = resolver

                # Get job IDs from slugs
                jobs_repo = JobsRepository()
                try:
                    slug_to_id = {j.slug: j.id for j in jobs_repo.get_jobs()}
                finally:
                    jobs_repo.close()

                stop_on_failure = self.options.get('stop_on_failure', False)
                delay_between = self.options.get('delay_between_jobs', 5)
                total_jobs = len(self.job_slugs)

                for i, slug in enumerate(self.job_slugs):
                    if self._cancelled:
                        self.log_message.emit("Batch cancelled by user")
                        break

                    job_id = slug_to_id.get(slug)
                    if not job_id:
                        self.log_message.emit(f"⚠ Job not found: {slug}, skipping")
                        results.append((slug, None))
                        continue

                    self.job_starting.emit(i + 1, total_jobs, slug)
                    self.log_message.emit(f"\n{'='*50}")
                    self.log_message.emit(f"Starting job {i+1}/{total_jobs}: {slug}")

                    runner = JobRunner(
                        credentials=creds,
                        credential_resolver=credential_resolver,
                        validate=self.options.get('validate', True),
                        debug=self.options.get('debug', False),
                        no_save=self.options.get('no_save', False),
                        force_save=self.options.get('force_save', False),
                        limit=self.options.get('limit'),
                        quiet=False,
                        record_history=True,
                    )

                    def on_progress(completed, total, result):
                        if self._cancelled:
                            return
                        progress = DeviceProgress(
                            device_name=result.device_name if hasattr(result, 'device_name') else str(result),
                            host=result.host if hasattr(result, 'host') else '',
                            success=result.success if hasattr(result, 'success') else True,
                            duration_ms=result.duration_ms if hasattr(result, 'duration_ms') else 0,
                            error=result.error if hasattr(result, 'error') else None,
                            credential_name=result.credential_name if hasattr(result, 'credential_name') else None,
                        )
                        self.job_progress.emit(completed, total, progress)

                    try:
                        result = runner.run_job(job_id=job_id, progress_callback=on_progress)
                        results.append((slug, result))
                        self.job_finished.emit(i + 1, slug, result)

                        if not result.success and stop_on_failure:
                            self.log_message.emit(f"Job {slug} failed, stopping batch (stop_on_failure=True)")
                            break

                    except Exception as e:
                        self.log_message.emit(f"Job {slug} error: {e}")
                        results.append((slug, None))
                        if stop_on_failure:
                            break

                    # Delay between jobs (except after last job)
                    if i < total_jobs - 1 and delay_between > 0 and not self._cancelled:
                        self.log_message.emit(f"Waiting {delay_between}s before next job...")
                        for _ in range(delay_between):
                            if self._cancelled:
                                break
                            time.sleep(1)

                if not self._cancelled:
                    self.batch_finished.emit(results)

            finally:
                resolver.lock_vault()

        except Exception as e:
            self.error.emit(f"Batch execution error: {str(e)}\n{traceback.format_exc()}")

    def cancel(self):
        self._cancelled = True


class RunView(QWidget):
    """Job execution view with single job and batch mode support."""

    def __init__(self, repo: Optional[JobsRepository] = None, parent=None):
        super().__init__(parent)
        self.repo = repo or JobsRepository()
        self._jobs: List[Job] = []
        self._execution_thread: Optional[JobExecutionThread] = None
        self._batch_thread: Optional[BatchExecutionThread] = None
        self._start_time: Optional[datetime] = None
        self._batch_loader = None

        self.init_ui()
        self.refresh_jobs()
        self.refresh_batches()

    @property
    def batch_loader(self):
        """Lazy-load batch loader."""
        if self._batch_loader is None:
            from vcollector.core.batch_loader import BatchLoader
            self._batch_loader = BatchLoader(jobs_repo=self.repo)
        return self._batch_loader

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

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

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self._on_refresh_all)
        header_layout.addWidget(refresh_btn)

        layout.addLayout(header_layout)

        # === Mode Selection ===
        mode_group = QGroupBox("Execution Mode")
        mode_layout = QHBoxLayout(mode_group)

        self.single_radio = QRadioButton("Single Job")
        self.batch_radio = QRadioButton("Batch")
        self.single_radio.setChecked(True)

        self.mode_group = QButtonGroup()
        self.mode_group.addButton(self.single_radio, 0)
        self.mode_group.addButton(self.batch_radio, 1)
        self.mode_group.idToggled.connect(self._on_mode_changed)

        mode_layout.addWidget(self.single_radio)
        mode_layout.addWidget(self.batch_radio)
        mode_layout.addStretch()

        layout.addWidget(mode_group)

        # === Top: Job/Batch Selection and Options ===
        top_layout = QHBoxLayout()
        top_layout.setSpacing(16)

        # Single Job selection group
        self.job_group = QGroupBox("Job Selection")
        job_layout = QFormLayout(self.job_group)

        self.job_combo = QComboBox()
        self.job_combo.setMinimumWidth(300)
        self.job_combo.currentIndexChanged.connect(self._on_job_selected)
        job_layout.addRow("Job:", self.job_combo)

        self.job_info_label = QLabel("Select a job to see details")
        self.job_info_label.setWordWrap(True)
        self.job_info_label.setStyleSheet("color: #888;")
        job_layout.addRow("", self.job_info_label)

        top_layout.addWidget(self.job_group)

        # Batch selection group (initially hidden)
        self.batch_group = QGroupBox("Batch Selection")
        batch_layout = QVBoxLayout(self.batch_group)

        batch_combo_layout = QHBoxLayout()
        self.batch_combo = QComboBox()
        self.batch_combo.setMinimumWidth(250)
        self.batch_combo.currentIndexChanged.connect(self._on_batch_selected)
        batch_combo_layout.addWidget(self.batch_combo)

        self.edit_batch_btn = QPushButton("Edit")
        self.edit_batch_btn.setFixedWidth(60)
        self.edit_batch_btn.clicked.connect(self._on_edit_batch)
        batch_combo_layout.addWidget(self.edit_batch_btn)

        self.new_batch_btn = QPushButton("New")
        self.new_batch_btn.setFixedWidth(60)
        self.new_batch_btn.clicked.connect(self._on_new_batch)
        batch_combo_layout.addWidget(self.new_batch_btn)

        batch_layout.addLayout(batch_combo_layout)

        batch_layout.addWidget(QLabel("Jobs in batch:"))
        self.batch_jobs_list = QListWidget()
        self.batch_jobs_list.setMaximumHeight(120)
        self.batch_jobs_list.setAlternatingRowColors(True)
        batch_layout.addWidget(self.batch_jobs_list)

        self.batch_group.setVisible(False)
        top_layout.addWidget(self.batch_group)

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

        self.force_save_check = QCheckBox("Force save (ignore validation)")
        options_layout.addRow("", self.force_save_check)

        self.debug_check = QCheckBox("Debug output")
        options_layout.addRow("", self.debug_check)

        self.per_device_creds_check = QCheckBox("Use per-device credentials")
        self.per_device_creds_check.setChecked(True)
        self.per_device_creds_check.setToolTip(
            "Use device-specific credentials discovered via credential discovery.\n"
            "Falls back to default credential if device has no assigned credential."
        )
        options_layout.addRow("", self.per_device_creds_check)

        # Batch-specific options
        self.batch_options_frame = QFrame()
        batch_opts_layout = QFormLayout(self.batch_options_frame)
        batch_opts_layout.setContentsMargins(0, 8, 0, 0)

        self.stop_on_failure_check = QCheckBox("Stop on failure")
        self.stop_on_failure_check.setToolTip("Stop batch execution if a job fails")
        batch_opts_layout.addRow("", self.stop_on_failure_check)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 60)
        self.delay_spin.setValue(5)
        self.delay_spin.setSuffix(" sec")
        batch_opts_layout.addRow("Delay between jobs:", self.delay_spin)

        self.batch_options_frame.setVisible(False)
        options_layout.addRow(self.batch_options_frame)

        # Credential coverage
        self.cred_coverage_label = QLabel("Coverage: checking...")
        self.cred_coverage_label.setStyleSheet("color: #888; font-size: 11px;")
        options_layout.addRow("Credentials:", self.cred_coverage_label)

        top_layout.addWidget(options_group)

        layout.addLayout(top_layout)

        # === Middle: Controls ===
        controls_layout = QHBoxLayout()

        self.run_btn = QPushButton("Run Job")
        self.run_btn.setProperty("primary", True)
        self.run_btn.setMinimumHeight(40)
        self.run_btn.clicked.connect(self._on_run_clicked)
        controls_layout.addWidget(self.run_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        controls_layout.addWidget(self.cancel_btn)

        controls_layout.addStretch()

        self.progress_label = QLabel("Ready")
        controls_layout.addWidget(self.progress_label)

        self.elapsed_label = QLabel("")
        self.elapsed_label.setStyleSheet("color: #888;")
        controls_layout.addWidget(self.elapsed_label)

        layout.addLayout(controls_layout)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Batch progress label (shows "Job 2/5: cisco-arp")
        self.batch_progress_label = QLabel("")
        self.batch_progress_label.setStyleSheet("color: #f39c12; font-weight: bold;")
        self.batch_progress_label.setVisible(False)
        layout.addWidget(self.batch_progress_label)

        # === Bottom: Results ===
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Results table
        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout(results_group)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels(["Device", "Host", "Credential", "Status", "Duration"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setAlternatingRowColors(True)
        results_layout.addWidget(self.results_table)

        self.summary_label = QLabel("")
        results_layout.addWidget(self.summary_label)

        splitter.addWidget(results_group)

        # Log output
        log_group = QGroupBox("Execution Log")
        log_layout = QVBoxLayout(log_group)

        log_controls = QHBoxLayout()
        clear_log_btn = QPushButton("Clear Log")
        clear_log_btn.clicked.connect(self._clear_log)
        log_controls.addWidget(clear_log_btn)
        log_controls.addStretch()
        log_layout.addLayout(log_controls)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(150)
        log_layout.addWidget(self.log_output)

        splitter.addWidget(log_group)

        layout.addWidget(splitter)

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # Timer for elapsed time
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        # Update credential coverage
        QTimer.singleShot(500, self._update_credential_coverage)

    def _on_mode_changed(self, button_id: int, checked: bool):
        """Handle mode radio button change."""
        if not checked:
            return

        is_batch = (button_id == 1)
        self.job_group.setVisible(not is_batch)
        self.batch_group.setVisible(is_batch)
        self.batch_options_frame.setVisible(is_batch)
        self.run_btn.setText("Run Batch" if is_batch else "Run Job")

    def _on_refresh_all(self):
        """Refresh both jobs and batches."""
        self.refresh_jobs()
        self.refresh_batches()

    def refresh_jobs(self):
        """Reload jobs from database."""
        self.job_combo.clear()
        self._jobs = [j for j in self.repo.get_jobs() if j.is_enabled]

        if not self._jobs:
            self.job_combo.addItem("No jobs available", None)
            return

        for job in self._jobs:
            display = f"{job.name} [{job.capture_type}]"
            if job.vendor:
                display += f" ({job.vendor})"
            self.job_combo.addItem(display, job.id)

    def refresh_batches(self):
        """Reload batch definitions from files."""
        self.batch_combo.clear()
        self.batch_jobs_list.clear()

        try:
            batches = self.batch_loader.list_batches()

            if not batches:
                self.batch_combo.addItem("No batches defined", None)
                return

            for batch in batches:
                display = f"{batch.name} ({batch.job_count} jobs)"
                self.batch_combo.addItem(display, batch.filename)

        except Exception as e:
            self.batch_combo.addItem(f"Error loading batches: {e}", None)

    def _update_credential_coverage(self):
        """Update credential coverage display."""
        try:
            from vcollector.dcim.dcim_repo import DCIMRepository
            dcim = DCIMRepository()
            try:
                devices = dcim.get_devices(status='active')
                total = len(devices)
                with_creds = sum(1 for d in devices if d.get('credential_id'))

                if total > 0:
                    pct = (with_creds / total) * 100
                    color = '#2ecc71' if pct >= 80 else '#f39c12' if pct >= 50 else '#e74c3c'
                    self.cred_coverage_label.setText(f"{with_creds}/{total} devices ({pct:.0f}%)")
                    self.cred_coverage_label.setStyleSheet(f"color: {color}; font-size: 11px;")
                else:
                    self.cred_coverage_label.setText("No active devices")
            finally:
                dcim.close()

        except Exception as e:
            self.cred_coverage_label.setText("Coverage: unknown")
            self.cred_coverage_label.setStyleSheet("color: #888;")

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

    def _on_batch_selected(self, index: int):
        """Handle batch selection change."""
        self.batch_jobs_list.clear()

        filename = self.batch_combo.currentData()
        if not filename:
            return

        try:
            batch = self.batch_loader.load_batch(filename)

            for slug in batch.jobs:
                item = QListWidgetItem(slug)
                if slug in batch.invalid_jobs:
                    item.setForeground(QColor('#e74c3c'))
                    item.setToolTip("Job not found in database")
                else:
                    item.setForeground(QColor('#2ecc71'))
                self.batch_jobs_list.addItem(item)

            if batch.invalid_jobs:
                self._append_log(f"⚠ Batch has invalid jobs: {batch.invalid_jobs}")

        except Exception as e:
            self._append_log(f"Error loading batch: {e}")

    def _on_edit_batch(self):
        """Open batch edit dialog."""
        filename = self.batch_combo.currentData()
        if not filename:
            return

        try:
            from vcollector.ui.widgets.batch_dialogs import BatchEditDialog

            batch = self.batch_loader.load_batch(filename)
            dialog = BatchEditDialog(
                batch_loader=self.batch_loader,
                jobs_repo=self.repo,
                batch=batch,
                parent=self
            )
            dialog.batch_saved.connect(self._on_batch_saved)
            dialog.batch_deleted.connect(self._on_batch_deleted)
            dialog.exec()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to edit batch: {e}")

    def _on_new_batch(self):
        """Create a new batch file via dialog."""
        try:
            from vcollector.ui.widgets.batch_dialogs import BatchEditDialog

            dialog = BatchEditDialog(
                batch_loader=self.batch_loader,
                jobs_repo=self.repo,
                batch=None,  # New batch
                parent=self
            )
            dialog.batch_saved.connect(self._on_batch_saved)
            dialog.exec()

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to create batch: {e}")

    def _on_batch_saved(self, filename: str):
        """Handle batch saved - refresh and select it."""
        self.refresh_batches()

        # Select the saved batch
        for i in range(self.batch_combo.count()):
            if self.batch_combo.itemData(i) == filename:
                self.batch_combo.setCurrentIndex(i)
                break

        self._append_log(f"Batch saved: {filename}")

    def _on_batch_deleted(self, filename: str):
        """Handle batch deleted - refresh list."""
        self.refresh_batches()
        self._append_log(f"Batch deleted: {filename}")

    def _on_run_clicked(self):
        """Start job or batch execution."""
        is_batch = self.batch_radio.isChecked()

        if is_batch:
            self._run_batch()
        else:
            self._run_single_job()

    def _run_single_job(self):
        """Run a single job."""
        job_id = self.job_combo.currentData()
        if not job_id:
            QMessageBox.warning(self, "No Job Selected", "Please select a job to run.")
            return

        password, ok = QInputDialog.getText(
            self, "Vault Password", "Enter vault password:",
            QLineEdit.EchoMode.Password
        )

        if not ok or not password:
            return

        options = {
            'validate': self.validate_check.isChecked(),
            'force_save': self.force_save_check.isChecked(),
            'debug': self.debug_check.isChecked(),
            'limit': self.limit_spin.value() if self.limit_spin.value() > 0 else None,
            'use_per_device_creds': self.per_device_creds_check.isChecked(),
        }

        self._prepare_execution()
        self._append_log(f"Starting job: {self.job_combo.currentText()}")

        self._execution_thread = JobExecutionThread(job_id, password, options)
        self._execution_thread.progress.connect(self._on_progress)
        self._execution_thread.log_message.connect(self._append_log)
        self._execution_thread.finished_job.connect(self._on_job_finished)
        self._execution_thread.error.connect(self._on_error)
        self._execution_thread.finished.connect(self._on_thread_finished)
        self._execution_thread.start()

    def _run_batch(self):
        """Run a batch of jobs."""
        filename = self.batch_combo.currentData()
        if not filename:
            QMessageBox.warning(self, "No Batch Selected", "Please select a batch to run.")
            return

        try:
            batch = self.batch_loader.load_batch(filename)
            if not batch.valid_jobs:
                QMessageBox.warning(self, "Empty Batch", "Batch has no valid jobs to run.")
                return

            if batch.invalid_jobs:
                reply = QMessageBox.question(
                    self,
                    "Invalid Jobs",
                    f"Batch contains jobs not found in database:\n{batch.invalid_jobs}\n\n"
                    f"Continue with {len(batch.valid_jobs)} valid jobs?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load batch: {e}")
            return

        password, ok = QInputDialog.getText(
            self, "Vault Password", "Enter vault password:",
            QLineEdit.EchoMode.Password
        )

        if not ok or not password:
            return

        options = {
            'validate': self.validate_check.isChecked(),
            'force_save': self.force_save_check.isChecked(),
            'debug': self.debug_check.isChecked(),
            'limit': self.limit_spin.value() if self.limit_spin.value() > 0 else None,
            'use_per_device_creds': self.per_device_creds_check.isChecked(),
            'stop_on_failure': self.stop_on_failure_check.isChecked(),
            'delay_between_jobs': self.delay_spin.value(),
        }

        self._prepare_execution()
        self.batch_progress_label.setVisible(True)
        self._append_log(f"Starting batch: {batch.name} ({len(batch.valid_jobs)} jobs)")

        self._batch_thread = BatchExecutionThread(batch.valid_jobs, password, options)
        self._batch_thread.job_starting.connect(self._on_batch_job_starting)
        self._batch_thread.job_progress.connect(self._on_progress)
        self._batch_thread.job_finished.connect(self._on_batch_job_finished)
        self._batch_thread.log_message.connect(self._append_log)
        self._batch_thread.batch_finished.connect(self._on_batch_finished)
        self._batch_thread.error.connect(self._on_error)
        self._batch_thread.finished.connect(self._on_thread_finished)
        self._batch_thread.start()

    def _prepare_execution(self):
        """Prepare UI for execution."""
        self.results_table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting...")
        self.summary_label.setText("")
        self.batch_progress_label.setText("")

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.job_combo.setEnabled(False)
        self.batch_combo.setEnabled(False)
        self.single_radio.setEnabled(False)
        self.batch_radio.setEnabled(False)

        self._start_time = datetime.now()
        self._elapsed_timer.start(1000)

    def _on_cancel_clicked(self):
        """Cancel execution."""
        if self._execution_thread:
            self._append_log("Cancellation requested...")
            self._execution_thread.cancel()
        if self._batch_thread:
            self._append_log("Cancellation requested...")
            self._batch_thread.cancel()

    def _on_progress(self, completed: int, total: int, device: DeviceProgress):
        """Handle progress update."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(completed)
        self.progress_label.setText(f"Progress: {completed}/{total} devices")

        row = self.results_table.rowCount()
        self.results_table.insertRow(row)

        self.results_table.setItem(row, 0, QTableWidgetItem(device.device_name))
        self.results_table.setItem(row, 1, QTableWidgetItem(device.host))

        cred_text = device.credential_name or "default"
        cred_item = QTableWidgetItem(cred_text)
        if device.credential_name:
            cred_item.setForeground(QColor('#3498db'))
        else:
            cred_item.setForeground(QColor('#888'))
        self.results_table.setItem(row, 2, cred_item)

        status = "✓" if device.success else "✗"
        status_item = QTableWidgetItem(status)
        status_item.setForeground(QColor('#2ecc71' if device.success else '#e74c3c'))
        self.results_table.setItem(row, 3, status_item)

        duration = f"{device.duration_ms:.0f}ms" if device.duration_ms else "—"
        self.results_table.setItem(row, 4, QTableWidgetItem(duration))

        self.results_table.scrollToBottom()

    def _on_batch_job_starting(self, job_index: int, total_jobs: int, job_slug: str):
        """Handle batch job starting."""
        self.batch_progress_label.setText(f"Job {job_index}/{total_jobs}: {job_slug}")
        self.progress_bar.setValue(0)

    def _on_batch_job_finished(self, job_index: int, job_slug: str, result):
        """Handle individual job completion in batch."""
        if result:
            status = "✓" if result.success else "✗"
            self._append_log(
                f"Job {job_slug} {status}: "
                f"{result.success_count}/{result.total_devices} success, "
                f"{result.failed_count} failed"
            )

    def _on_job_finished(self, result):
        """Handle single job completion."""
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

        self._update_credential_coverage()

    def _on_batch_finished(self, results: list):
        """Handle batch completion."""
        self._elapsed_timer.stop()
        self.batch_progress_label.setVisible(False)

        total_jobs = len(results)
        successful = sum(1 for _, r in results if r and r.success)
        failed = total_jobs - successful

        status = "✓ Complete" if failed == 0 else "⚠ Partial"
        self.progress_label.setText(f"Batch {status}")

        self.summary_label.setText(f"Jobs: {successful}/{total_jobs} successful")

        self._append_log(f"\n{'='*50}")
        self._append_log(f"BATCH COMPLETE")
        self._append_log(f"Total jobs: {total_jobs}")
        self._append_log(f"Successful: {successful}")
        self._append_log(f"Failed: {failed}")

        total_devices = sum(r.total_devices for _, r in results if r)
        total_success = sum(r.success_count for _, r in results if r)
        self._append_log(f"Total devices: {total_devices}, Success: {total_success}")

        self._update_credential_coverage()

    def _on_error(self, message: str):
        """Handle execution error."""
        self._elapsed_timer.stop()
        self.progress_label.setText("✗ Error")
        self.batch_progress_label.setVisible(False)
        self._append_log(f"\nERROR: {message}")
        QMessageBox.critical(self, "Execution Error", message)

    def _on_thread_finished(self):
        """Handle thread completion."""
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.job_combo.setEnabled(True)
        self.batch_combo.setEnabled(True)
        self.single_radio.setEnabled(True)
        self.batch_radio.setEnabled(True)
        self.batch_progress_label.setVisible(False)
        self._elapsed_timer.stop()
        self._execution_thread = None
        self._batch_thread = None

    def _update_elapsed(self):
        """Update elapsed time display."""
        if self._start_time:
            elapsed = datetime.now() - self._start_time
            self.elapsed_label.setText(f"Elapsed: {elapsed.seconds}s")

    def _append_log(self, message: str):
        """Append message to log output."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{timestamp}] {message}")
        cursor = self.log_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_output.setTextCursor(cursor)

    def _clear_log(self):
        """Clear log output."""
        self.log_output.clear()

    def run_job_by_id(self, job_id: int):
        """External entry point to run a specific job."""
        self.single_radio.setChecked(True)
        self._on_mode_changed(0, True)

        for i in range(self.job_combo.count()):
            if self.job_combo.itemData(i) == job_id:
                self.job_combo.setCurrentIndex(i)
                break

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