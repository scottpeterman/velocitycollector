"""
VelocityCollector Job Dialogs

Dialog windows for viewing and editing job definitions.
- JobDetailDialog: Read-only view of job details
- JobEditDialog: Create/Edit form with validation
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QSpinBox, QTextEdit, QPushButton, QMessageBox, QFrame,
    QWidget, QTabWidget, QCheckBox, QGroupBox
)
from PyQt6.QtCore import Qt, pyqtSignal

from vcollector.dcim.jobs_repo import JobsRepository, Job, CaptureType

from typing import Optional, List
from datetime import datetime
import re

# Common capture types for dropdown
CAPTURE_TYPES = [
    ("arp", "ARP Tables"),
    ("mac", "MAC Address Tables"),
    ("config", "Configuration"),
    ("inventory", "Hardware Inventory"),
    ("interfaces", "Interface Status"),
    ("routes", "Routing Tables"),
    ("bgp", "BGP Neighbors/Routes"),
    ("ospf", "OSPF Status"),
    ("vlans", "VLAN Configuration"),
    ("spanning", "Spanning Tree"),
    ("cdp", "CDP Neighbors"),
    ("lldp", "LLDP Neighbors"),
    ("version", "Version/System Info"),
    ("custom", "Custom"),
]

# Common vendors
VENDORS = [
    ("", "— Any/Multi-vendor —"),
    ("arista", "Arista"),
    ("cisco", "Cisco"),
    ("juniper", "Juniper"),
    ("hp", "HP/Aruba"),
    ("dell", "Dell"),
    ("fortinet", "Fortinet"),
    ("paloalto", "Palo Alto"),
    ("linux", "Linux"),
]

# Common paging disable commands by vendor
PAGING_COMMANDS = {
    "arista": "terminal length 0",
    "cisco": "terminal length 0",
    "juniper": "set cli screen-length 0",
    "hp": "screen-length 0 temporary",
    "dell": "terminal length 0",
    "fortinet": "config system console\nset output standard\nend",
    "paloalto": "set cli pager off",
    "linux": "",
}


class JobDetailDialog(QDialog):
    """
    Read-only dialog showing full job details.
    """

    edit_requested = pyqtSignal(int)  # job_id
    run_requested = pyqtSignal(int)  # job_id

    def __init__(self, job: Job, parent=None):
        super().__init__(parent)
        self.job = job
        self.setWindowTitle(f"Job: {job.name}")
        self.setMinimumWidth(550)
        self.setMinimumHeight(500)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        tabs = QTabWidget()

        # === General Tab ===
        general_tab = QWidget()
        general_layout = QFormLayout(general_tab)
        general_layout.setSpacing(8)
        general_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        general_layout.addRow("Name:", self._label(self.job.name))
        general_layout.addRow("Slug:", self._label(self.job.slug))
        general_layout.addRow("Capture Type:", self._label((self.job.capture_type or "").upper()))
        general_layout.addRow("Vendor:", self._label((self.job.vendor or "Any").title()))
        general_layout.addRow("Status:", self._status_label("enabled" if self.job.is_enabled else "disabled"))

        general_layout.addRow(self._separator())

        general_layout.addRow("Credential:", self._label(self.job.credential_name or "Default"))
        general_layout.addRow("Protocol:", self._label(self.job.protocol or "ssh"))

        tabs.addTab(general_tab, "General")

        # === Commands Tab ===
        commands_tab = QWidget()
        commands_layout = QVBoxLayout(commands_tab)

        cmd_form = QFormLayout()
        cmd_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        cmd_form.addRow("Paging Disable:", self._label(self.job.paging_disable_command))
        commands_layout.addLayout(cmd_form)

        commands_layout.addWidget(QLabel("Command:"))
        cmd_text = QTextEdit()
        cmd_text.setPlainText(self.job.command or "")
        cmd_text.setReadOnly(True)
        cmd_text.setMaximumHeight(100)
        commands_layout.addWidget(cmd_text)

        cmd_form2 = QFormLayout()
        cmd_form2.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        cmd_form2.addRow("Output Directory:", self._label(self.job.output_directory))
        cmd_form2.addRow("Filename Pattern:", self._label(self.job.filename_pattern))
        commands_layout.addLayout(cmd_form2)

        # TextFSM
        tfsm_group = QGroupBox("TextFSM Parsing")
        tfsm_layout = QFormLayout(tfsm_group)
        tfsm_layout.addRow("Enabled:", self._label("Yes" if self.job.use_textfsm else "No"))
        tfsm_layout.addRow("Template:", self._label(self.job.textfsm_template))
        tfsm_layout.addRow("Min Score:", self._label(str(self.job.validation_min_score)))
        commands_layout.addWidget(tfsm_group)

        commands_layout.addStretch()
        tabs.addTab(commands_tab, "Commands")

        # === Execution Tab ===
        exec_tab = QWidget()
        exec_layout = QFormLayout(exec_tab)
        exec_layout.setSpacing(8)
        exec_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        exec_layout.addRow("Max Workers:", self._label(str(self.job.max_workers)))
        exec_layout.addRow("Timeout (sec):", self._label(str(self.job.timeout_seconds)))
        exec_layout.addRow("Inter-command Delay:", self._label(f"{self.job.inter_command_delay}s"))

        exec_layout.addRow(self._separator())

        exec_layout.addRow("Base Path:", self._label(self.job.base_path))
        exec_layout.addRow("Store Failures:", self._label("Yes" if self.job.store_failures else "No"))

        tabs.addTab(exec_tab, "Execution")

        # === Filters Tab ===
        filters_tab = QWidget()
        filters_layout = QFormLayout(filters_tab)
        filters_layout.setSpacing(8)
        filters_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        filters_layout.addRow("Filter Source:", self._label(self.job.device_filter_source))
        filters_layout.addRow("Device Status:", self._label(self.job.device_filter_status))
        filters_layout.addRow("Name Pattern:", self._label(self.job.device_filter_name_pattern))

        # Note: Platform/Site/Role IDs would need lookups to show names
        if self.job.device_filter_platform_id:
            filters_layout.addRow("Platform ID:", self._label(str(self.job.device_filter_platform_id)))
        if self.job.device_filter_site_id:
            filters_layout.addRow("Site ID:", self._label(str(self.job.device_filter_site_id)))
        if self.job.device_filter_role_id:
            filters_layout.addRow("Role ID:", self._label(str(self.job.device_filter_role_id)))

        tabs.addTab(filters_tab, "Device Filters")

        # === History Tab ===
        history_tab = QWidget()
        history_layout = QFormLayout(history_tab)
        history_layout.setSpacing(8)
        history_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        history_layout.addRow("Total Runs:", self._label(str(self.job.run_count or 0)))
        history_layout.addRow("Last Run:", self._label(self._format_timestamp(self.job.last_run_at)))
        history_layout.addRow("Last Status:", self._status_label(self.job.last_run_status))

        history_layout.addRow(self._separator())

        history_layout.addRow("Created:", self._label(self._format_timestamp(self.job.created_at)))
        history_layout.addRow("Updated:", self._label(self._format_timestamp(self.job.updated_at)))

        if self.job.legacy_job_id:
            history_layout.addRow(self._separator())
            history_layout.addRow("Legacy Job ID:", self._label(str(self.job.legacy_job_id)))
            history_layout.addRow("Legacy File:", self._label(self.job.legacy_job_file))
            history_layout.addRow("Migrated:", self._label(self._format_timestamp(self.job.migrated_at)))

        tabs.addTab(history_tab, "History")

        layout.addWidget(tabs)

        # Description
        if self.job.description:
            layout.addWidget(QLabel("Description:"))
            desc = QTextEdit()
            desc.setPlainText(self.job.description)
            desc.setReadOnly(True)
            desc.setMaximumHeight(60)
            layout.addWidget(desc)

        # Button row
        button_layout = QHBoxLayout()

        run_btn = QPushButton("▶ Run")
        run_btn.clicked.connect(self._on_run)
        if not self.job.is_enabled:
            run_btn.setEnabled(False)
            run_btn.setToolTip("Job is disabled")
        button_layout.addWidget(run_btn)

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

    def _status_label(self, status: Optional[str]) -> QLabel:
        label = QLabel(status or "—")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        colors = {
            'success': '#2ecc71',
            'enabled': '#2ecc71',
            'partial': '#f39c12',
            'running': '#3498db',
            'failed': '#e74c3c',
            'disabled': '#7f8c8d',
        }
        if status in colors:
            label.setStyleSheet(f"color: {colors[status]}; font-weight: bold;")

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
        self.edit_requested.emit(self.job.id)
        self.accept()

    def _on_run(self):
        self.run_requested.emit(self.job.id)
        self.accept()


class JobEditDialog(QDialog):
    """
    Create/Edit dialog for job definitions.
    """

    job_saved = pyqtSignal(int)  # job_id

    def __init__(self, repo: JobsRepository, job: Optional[Job] = None, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.job = job
        self.is_edit_mode = job is not None
        self.saved_job_id: Optional[int] = None

        self.setWindowTitle("Edit Job" if self.is_edit_mode else "New Job")
        self.setMinimumWidth(550)
        self.setMinimumHeight(550)

        self.init_ui()

        if self.is_edit_mode:
            self.populate_form()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        tabs = QTabWidget()

        # === Identity Tab ===
        identity_tab = QWidget()
        identity_layout = QFormLayout(identity_tab)
        identity_layout.setSpacing(10)
        identity_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Job name (required)")
        self.name_input.setMaxLength(100)
        self.name_input.textChanged.connect(self._auto_generate_slug)
        identity_layout.addRow("Name: *", self.name_input)

        self.slug_input = QLineEdit()
        self.slug_input.setPlaceholderText("URL-friendly identifier (auto-generated)")
        self.slug_input.setMaxLength(100)
        identity_layout.addRow("Slug: *", self.slug_input)

        self.capture_type_combo = QComboBox()
        for value, label in CAPTURE_TYPES:
            self.capture_type_combo.addItem(label, value)
        identity_layout.addRow("Capture Type: *", self.capture_type_combo)

        self.vendor_combo = QComboBox()
        for value, label in VENDORS:
            self.vendor_combo.addItem(label, value if value else None)
        self.vendor_combo.currentIndexChanged.connect(self._on_vendor_changed)
        identity_layout.addRow("Vendor:", self.vendor_combo)

        self.enabled_check = QCheckBox("Job is enabled")
        self.enabled_check.setChecked(True)
        identity_layout.addRow("", self.enabled_check)

        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText("Optional description")
        self.description_input.setMaximumHeight(60)
        identity_layout.addRow("Description:", self.description_input)

        tabs.addTab(identity_tab, "Identity")

        # === Commands Tab ===
        commands_tab = QWidget()
        commands_layout = QVBoxLayout(commands_tab)

        cmd_form = QFormLayout()
        cmd_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.paging_input = QLineEdit()
        self.paging_input.setPlaceholderText("e.g., terminal length 0")
        cmd_form.addRow("Paging Disable:", self.paging_input)

        commands_layout.addLayout(cmd_form)

        commands_layout.addWidget(QLabel("Command(s): *"))
        self.command_input = QTextEdit()
        self.command_input.setPlaceholderText("Command to execute on devices\ne.g., show ip arp")
        self.command_input.setMaximumHeight(100)
        commands_layout.addWidget(self.command_input)

        cmd_form2 = QFormLayout()
        cmd_form2.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.output_dir_input = QLineEdit()
        self.output_dir_input.setPlaceholderText("e.g., arp, mac, config")
        cmd_form2.addRow("Output Directory:", self.output_dir_input)

        self.filename_input = QLineEdit()
        self.filename_input.setText("{device_name}.txt")
        self.filename_input.setPlaceholderText("{device_name}.txt")
        cmd_form2.addRow("Filename Pattern:", self.filename_input)

        commands_layout.addLayout(cmd_form2)

        # TextFSM group
        tfsm_group = QGroupBox("TextFSM Parsing")
        tfsm_layout = QFormLayout(tfsm_group)

        self.use_textfsm_check = QCheckBox("Enable TextFSM parsing")
        tfsm_layout.addRow("", self.use_textfsm_check)

        self.textfsm_template_input = QLineEdit()
        self.textfsm_template_input.setPlaceholderText("e.g., arp, cisco_ios_show_ip_arp")
        tfsm_layout.addRow("Template Filter:", self.textfsm_template_input)

        self.min_score_input = QSpinBox()
        self.min_score_input.setRange(0, 100)
        self.min_score_input.setValue(0)
        tfsm_layout.addRow("Min Quality Score:", self.min_score_input)

        self.store_failures_check = QCheckBox("Store output even if validation fails")
        self.store_failures_check.setChecked(True)
        tfsm_layout.addRow("", self.store_failures_check)

        commands_layout.addWidget(tfsm_group)
        commands_layout.addStretch()

        tabs.addTab(commands_tab, "Commands")

        # === Execution Tab ===
        exec_tab = QWidget()
        exec_layout = QFormLayout(exec_tab)
        exec_layout.setSpacing(10)
        exec_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.max_workers_input = QSpinBox()
        self.max_workers_input.setRange(1, 100)
        self.max_workers_input.setValue(10)
        exec_layout.addRow("Max Workers:", self.max_workers_input)

        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(10, 600)
        self.timeout_input.setValue(60)
        self.timeout_input.setSuffix(" seconds")
        exec_layout.addRow("Timeout:", self.timeout_input)

        self.delay_input = QSpinBox()
        self.delay_input.setRange(0, 30)
        self.delay_input.setValue(1)
        self.delay_input.setSuffix(" seconds")
        exec_layout.addRow("Inter-command Delay:", self.delay_input)

        exec_layout.addRow(self._separator())

        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems(["ssh", "telnet", "netconf", "api"])
        exec_layout.addRow("Protocol:", self.protocol_combo)

        self.base_path_input = QLineEdit()
        self.base_path_input.setText("~/.vcollector/collections")
        exec_layout.addRow("Base Path:", self.base_path_input)

        tabs.addTab(exec_tab, "Execution")

        # === Device Filters Tab ===
        filters_tab = QWidget()
        filters_layout = QFormLayout(filters_tab)
        filters_layout.setSpacing(10)
        filters_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.filter_source_combo = QComboBox()
        self.filter_source_combo.addItems(["database", "file", "manual"])
        filters_layout.addRow("Device Source:", self.filter_source_combo)

        self.filter_status_combo = QComboBox()
        self.filter_status_combo.addItems(["active", "planned", "staged", "offline", "any"])
        filters_layout.addRow("Device Status:", self.filter_status_combo)

        self.name_pattern_input = QLineEdit()
        self.name_pattern_input.setPlaceholderText("Regex pattern (e.g., ^core-.*)")
        filters_layout.addRow("Name Pattern:", self.name_pattern_input)

        # Note: Platform/Site/Role would need dropdowns populated from dcim.db
        # For now, just ID inputs
        filters_layout.addRow(self._separator())
        filters_layout.addRow(QLabel("Advanced filters (by ID):"))

        self.platform_id_input = QSpinBox()
        self.platform_id_input.setRange(0, 99999)
        self.platform_id_input.setSpecialValueText("Any")
        filters_layout.addRow("Platform ID:", self.platform_id_input)

        self.site_id_input = QSpinBox()
        self.site_id_input.setRange(0, 99999)
        self.site_id_input.setSpecialValueText("Any")
        filters_layout.addRow("Site ID:", self.site_id_input)

        self.role_id_input = QSpinBox()
        self.role_id_input.setRange(0, 99999)
        self.role_id_input.setSpecialValueText("Any")
        filters_layout.addRow("Role ID:", self.role_id_input)

        tabs.addTab(filters_tab, "Device Filters")

        layout.addWidget(tabs)

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
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        slug = re.sub(r'-+', '-', slug)
        return slug

    def _on_vendor_changed(self, index: int):
        """Auto-fill paging command when vendor changes."""
        vendor = self.vendor_combo.currentData()
        if vendor and vendor in PAGING_COMMANDS:
            # Only auto-fill if paging field is empty
            if not self.paging_input.text():
                self.paging_input.setText(PAGING_COMMANDS[vendor])

    def populate_form(self):
        """Populate form from existing job."""
        if not self.job:
            return

        # Identity
        self.name_input.setText(self.job.name or "")
        self.slug_input.setText(self.job.slug or "")

        # Capture type
        idx = self.capture_type_combo.findData(self.job.capture_type)
        if idx >= 0:
            self.capture_type_combo.setCurrentIndex(idx)

        # Vendor
        idx = self.vendor_combo.findData(self.job.vendor)
        if idx >= 0:
            self.vendor_combo.setCurrentIndex(idx)

        self.enabled_check.setChecked(self.job.is_enabled)
        self.description_input.setPlainText(self.job.description or "")

        # Commands
        self.paging_input.setText(self.job.paging_disable_command or "")
        self.command_input.setPlainText(self.job.command or "")
        self.output_dir_input.setText(self.job.output_directory or "")
        self.filename_input.setText(self.job.filename_pattern or "{device_name}.txt")

        # TextFSM
        self.use_textfsm_check.setChecked(self.job.use_textfsm)
        self.textfsm_template_input.setText(self.job.textfsm_template or "")
        self.min_score_input.setValue(self.job.validation_min_score or 0)
        self.store_failures_check.setChecked(self.job.store_failures)

        # Execution
        self.max_workers_input.setValue(self.job.max_workers or 10)
        self.timeout_input.setValue(self.job.timeout_seconds or 60)
        self.delay_input.setValue(self.job.inter_command_delay or 1)

        idx = self.protocol_combo.findText(self.job.protocol or "ssh")
        if idx >= 0:
            self.protocol_combo.setCurrentIndex(idx)

        self.base_path_input.setText(self.job.base_path or "~/.vcollector/collections")

        # Filters
        idx = self.filter_source_combo.findText(self.job.device_filter_source or "database")
        if idx >= 0:
            self.filter_source_combo.setCurrentIndex(idx)

        idx = self.filter_status_combo.findText(self.job.device_filter_status or "active")
        if idx >= 0:
            self.filter_status_combo.setCurrentIndex(idx)

        self.name_pattern_input.setText(self.job.device_filter_name_pattern or "")
        self.platform_id_input.setValue(self.job.device_filter_platform_id or 0)
        self.site_id_input.setValue(self.job.device_filter_site_id or 0)
        self.role_id_input.setValue(self.job.device_filter_role_id or 0)

    def validate(self) -> bool:
        errors = []

        name = self.name_input.text().strip()
        if not name:
            errors.append("Job name is required")

        slug = self.slug_input.text().strip()
        if not slug:
            errors.append("Slug is required")
        elif not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$', slug):
            errors.append("Slug must contain only lowercase letters, numbers, and hyphens")

        command = self.command_input.toPlainText().strip()
        if not command:
            errors.append("Command is required")

        if errors:
            self.validation_label.setText("• " + "\n• ".join(errors))
            return False

        self.validation_label.setText("")
        return True

    def _collect_form_data(self) -> dict:
        filter_status = self.filter_status_combo.currentText()

        return {
            'name': self.name_input.text().strip(),
            'slug': self.slug_input.text().strip(),
            'capture_type': self.capture_type_combo.currentData(),
            'vendor': self.vendor_combo.currentData(),
            'is_enabled': self.enabled_check.isChecked(),
            'description': self.description_input.toPlainText().strip() or None,
            'paging_disable_command': self.paging_input.text().strip() or None,
            'command': self.command_input.toPlainText().strip(),
            'output_directory': self.output_dir_input.text().strip() or None,
            'filename_pattern': self.filename_input.text().strip() or "{device_name}.txt",
            'use_textfsm': self.use_textfsm_check.isChecked(),
            'textfsm_template': self.textfsm_template_input.text().strip() or None,
            'validation_min_score': self.min_score_input.value(),
            'store_failures': self.store_failures_check.isChecked(),
            'max_workers': self.max_workers_input.value(),
            'timeout_seconds': self.timeout_input.value(),
            'inter_command_delay': self.delay_input.value(),
            'protocol': self.protocol_combo.currentText(),
            'base_path': self.base_path_input.text().strip() or "~/.vcollector/collections",
            'device_filter_source': self.filter_source_combo.currentText(),
            'device_filter_status': filter_status if filter_status != "any" else None,
            'device_filter_name_pattern': self.name_pattern_input.text().strip() or None,
            'device_filter_platform_id': self.platform_id_input.value() or None,
            'device_filter_site_id': self.site_id_input.value() or None,
            'device_filter_role_id': self.role_id_input.value() or None,
        }

    def _on_save(self):
        if not self.validate():
            return

        data = self._collect_form_data()

        try:
            if self.is_edit_mode:
                success = self.repo.update_job(self.job.id, **data)
                if success:
                    self.saved_job_id = self.job.id
                    self.job_saved.emit(self.job.id)
                    self.accept()
                else:
                    self.validation_label.setText("Failed to update job")
            else:
                name = data.pop('name')
                slug = data.pop('slug')
                capture_type = data.pop('capture_type')
                command = data.pop('command')
                job_id = self.repo.create_job(name, slug, capture_type, command, **data)
                self.saved_job_id = job_id
                self.job_saved.emit(job_id)
                self.accept()

        except Exception as e:
            error_msg = str(e)
            if "UNIQUE constraint failed" in error_msg:
                self.validation_label.setText("A job with this slug already exists")
            else:
                self.validation_label.setText(f"Error saving job: {error_msg}")

    def _on_delete(self):
        if not self.job:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete job '{self.job.name}'?\n\n"
            "This will not delete job history or captured data.\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.repo.delete_job(self.job.id)
                if success:
                    self.saved_job_id = None
                    self.accept()
                else:
                    self.validation_label.setText("Failed to delete job")
            except Exception as e:
                self.validation_label.setText(f"Error deleting job: {e}")


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    repo = JobsRepository()

    jobs = repo.get_jobs()
    if jobs:
        job = repo.get_job(job_id=jobs[0].id)

        detail = JobDetailDialog(job)
        detail.edit_requested.connect(lambda id: print(f"Edit requested for job {id}"))
        detail.run_requested.connect(lambda id: print(f"Run requested for job {id}"))

        if detail.exec() == QDialog.DialogCode.Accepted:
            print("Detail dialog closed")
    else:
        create = JobEditDialog(repo, job=None)
        create.job_saved.connect(lambda id: print(f"New job created: {id}"))

        if create.exec() == QDialog.DialogCode.Accepted:
            print(f"Create dialog saved job: {create.saved_job_id}")

    repo.close()