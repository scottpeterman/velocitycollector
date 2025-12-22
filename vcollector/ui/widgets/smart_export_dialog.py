"""
Smart Export Dialog for VelocityCollector Output View

Parses  network output using TextFSM templates (auto-detected or manual)
and exports structured data to JSON or CSV formats.

Usage:
    from smart_export_dialog import SmartExportDialog

    dialog = SmartExportDialog(
        filepath=Path("/path/to/captured/output.txt"),
        capture_type="arp",  # helps with template matching
        parent=self
    )
    dialog.exec()
"""

import json
import csv
import sqlite3
import io
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QComboBox, QPushButton, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox, QFileDialog,
    QSplitter, QTextEdit, QProgressBar, QCheckBox, QSpinBox,
    QTabWidget, QWidget, QLineEdit, QApplication
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

import textfsm

# Try to import the TextFSM auto engine
TFSM_ENGINE_AVAILABLE = False
try:
    from vcollector.core.tfsm_fire import TextFSMAutoEngine
    TFSM_ENGINE_AVAILABLE = True
except ImportError:
    try:
        from tfsm_fire import TextFSMAutoEngine
        TFSM_ENGINE_AVAILABLE = True
    except ImportError:
        pass


@dataclass
class ParseResult:
    """Result of TextFSM parsing."""
    success: bool
    template_name: str
    headers: List[str]
    data: List[Any]  # Can be List[Dict] or List[List]
    score: float
    error: Optional[str] = None
    record_count: int = 0

    def to_dicts(self) -> List[Dict[str, Any]]:
        """Convert to list of dictionaries."""
        if not self.data:
            return []

        # Check if data is already dicts
        if isinstance(self.data[0], dict):
            return self.data

        # Convert list of lists to list of dicts
        return [dict(zip(self.headers, row)) for row in self.data]


class TemplateMatchWorker(QThread):
    """Background worker for auto-matching templates."""

    finished = pyqtSignal(object)  # ParseResult
    progress = pyqtSignal(str)  # status message
    error = pyqtSignal(str)

    def __init__(self, db_path: str, content: str, filter_hint: str = ""):
        super().__init__()
        self.db_path = db_path
        self.content = content
        self.filter_hint = filter_hint

    def run(self):
        if not TFSM_ENGINE_AVAILABLE:
            self.error.emit("TextFSM Auto Engine not available")
            return

        try:
            self.progress.emit(f"Loading: {Path(self.db_path).name}")

            # Verify database exists
            if not Path(self.db_path).exists():
                self.error.emit(f"Database not found: {self.db_path}")
                return

            # Quick schema check before using engine
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(templates)")
            columns = [row[1] for row in cursor.fetchall()]
            conn.close()

            if 'cli_command' not in columns:
                self.error.emit(
                    f"Database schema mismatch.\n"
                    f"Expected 'cli_command' column.\n"
                    f"Found: {', '.join(columns)}\n"
                    f"DB: {self.db_path}"
                )
                return

            engine = TextFSMAutoEngine(self.db_path, verbose=False)

            self.progress.emit(f"Searching for matching templates...")

            # Use filter hint if provided
            result = engine.find_best_template(self.content, self.filter_hint)

            if len(result) == 4:
                best_template, best_parsed, best_score, template_content = result
            elif len(result) == 3:
                best_template, best_parsed, best_score = result
                template_content = None
            else:
                best_template, best_parsed, best_score = None, None, 0.0

            if best_template and best_parsed:
                # TextFSMAutoEngine returns list of dicts
                # Get headers from first dict's keys
                if isinstance(best_parsed[0], dict):
                    headers = list(best_parsed[0].keys())
                    data = best_parsed  # Keep as dicts for display
                else:
                    # Fallback for list of lists (shouldn't happen with engine)
                    if template_content:
                        try:
                            tfsm = textfsm.TextFSM(io.StringIO(template_content))
                            headers = tfsm.header
                        except:
                            headers = [f"col_{i}" for i in range(len(best_parsed[0]))] if best_parsed else []
                    else:
                        headers = [f"col_{i}" for i in range(len(best_parsed[0]))] if best_parsed else []
                    data = best_parsed

                parse_result = ParseResult(
                    success=True,
                    template_name=best_template,
                    headers=headers,
                    data=data,
                    score=best_score,
                    record_count=len(best_parsed)
                )
            else:
                parse_result = ParseResult(
                    success=False,
                    template_name="",
                    headers=[],
                    data=[],
                    score=0.0,
                    error="No matching template found"
                )

            self.finished.emit(parse_result)

        except Exception as e:
            self.error.emit(f"{str(e)}\n\nDatabase: {self.db_path}")


class SmartExportDialog(QDialog):
    """
    Smart Export Dialog - Parse captured output with TextFSM and export to JSON/CSV.

    Features:
    - Auto-detect best matching template
    - Manual template selection from database
    - Preview parsed data in table
    - Export to JSON or CSV
    - Handles multi-record output
    - Schema-adaptive: works with different tfsm_templates.db schemas
    """

    # Common column name mappings for template name
    TEMPLATE_NAME_COLUMNS = ['cli_command', 'name', 'template_name', 'command']

    # Common locations for tfsm_templates.db
    DB_SEARCH_PATHS = [
        Path.home() / ".vcollector" / "tfsm_templates.db",
        Path.home() / ".vcollector" / "data" / "tfsm_templates.db",
        Path(__file__).parent.parent.parent / "core" / "tfsm_templates.db",  # vcollector/core/
        Path(__file__).parent.parent / "core" / "tfsm_templates.db",
        Path.cwd() / "tfsm_templates.db",
        Path.cwd() / "vcollector" / "core" / "tfsm_templates.db",
    ]

    def __init__(
        self,
        filepath: Path,
        capture_type: str = "",
        db_path: Optional[str] = None,
        parent=None
    ):
        super().__init__(parent)
        self.filepath = filepath
        self.capture_type = capture_type
        self.db_path = db_path or self._find_database()

        # State
        self._content: str = ""
        self._templates: List[Dict] = []
        self._current_result: Optional[ParseResult] = None
        self._worker: Optional[TemplateMatchWorker] = None

        # Schema detection
        self._name_column: str = "cli_command"  # Default, will be detected
        self._schema_detected: bool = False

        self.setWindowTitle(f"Smart Export - {filepath.name}")
        self.setMinimumSize(900, 700)
        self.resize(1100, 800)

        self._load_content()
        self._detect_schema()
        self._init_ui()
        self._load_templates()

    def _find_database(self) -> str:
        """Find tfsm_templates.db in common locations."""
        for path in self.DB_SEARCH_PATHS:
            try:
                if path.exists():
                    return str(path)
            except:
                continue

        # Default fallback
        return str(Path.home() / ".vcollector" / "tfsm_templates.db")

    def _detect_schema(self):
        """Detect the actual column names in the templates table."""
        db_path = Path(self.db_path)
        if not db_path.exists():
            return

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # Get column info
            cursor.execute("PRAGMA table_info(templates)")
            columns = [row[1] for row in cursor.fetchall()]
            conn.close()

            # Find the template name column
            for col in self.TEMPLATE_NAME_COLUMNS:
                if col in columns:
                    self._name_column = col
                    self._schema_detected = True
                    break

            # If no standard column found, use first text column that's not 'id'
            if not self._schema_detected and columns:
                for col in columns:
                    if col.lower() not in ['id', 'textfsm_content', 'textfsm_hash', 'created', 'source']:
                        self._name_column = col
                        self._schema_detected = True
                        break

        except Exception as e:
            print(f"Schema detection error: {e}")

    def _load_content(self):
        """Load file content."""
        try:
            self._content = self.filepath.read_text(errors='replace')
        except Exception as e:
            self._content = f"Error loading file: {e}"

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # File info header
        info_layout = QHBoxLayout()

        file_label = QLabel(f"<b>File:</b> {self.filepath.name}")
        info_layout.addWidget(file_label)

        size_label = QLabel(f"<b>Size:</b> {len(self._content):,} bytes")
        info_layout.addWidget(size_label)

        if self.capture_type:
            type_label = QLabel(f"<b>Type:</b> {self.capture_type.upper()}")
            info_layout.addWidget(type_label)

        info_layout.addStretch()

        # Show database path (truncated)
        db_display = Path(self.db_path).name if Path(self.db_path).exists() else "NOT FOUND"
        db_label = QLabel(f"<b>DB:</b> {db_display}")
        db_label.setToolTip(self.db_path)
        db_label.setStyleSheet("color: #888;")
        info_layout.addWidget(db_label)

        layout.addLayout(info_layout)

        # Main splitter - template selection / preview
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top section - Template selection
        template_group = QGroupBox("Template Selection")
        template_layout = QVBoxLayout(template_group)

        # Auto-detect row
        auto_row = QHBoxLayout()

        self.auto_detect_btn = QPushButton("🔍 Auto-Detect Template")
        self.auto_detect_btn.clicked.connect(self._on_auto_detect)
        self.auto_detect_btn.setMinimumWidth(180)
        auto_row.addWidget(self.auto_detect_btn)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter hint (e.g., 'cisco_ios', 'show ip arp')...")
        self.filter_input.setText(self._guess_filter())
        auto_row.addWidget(self.filter_input, 1)

        template_layout.addLayout(auto_row)

        # Manual selection row
        manual_row = QHBoxLayout()
        manual_row.addWidget(QLabel("Or select manually:"))

        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(400)
        self.template_combo.currentIndexChanged.connect(self._on_template_selected)
        manual_row.addWidget(self.template_combo, 1)

        self.parse_btn = QPushButton("Parse")
        self.parse_btn.clicked.connect(self._on_manual_parse)
        manual_row.addWidget(self.parse_btn)

        template_layout.addLayout(manual_row)

        # Status / progress
        status_row = QHBoxLayout()

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximum(0)  # Indeterminate
        status_row.addWidget(self.progress_bar)

        self.status_label = QLabel("Select a template or click Auto-Detect")
        self.status_label.setStyleSheet("color: #888;")
        status_row.addWidget(self.status_label, 1)

        template_layout.addLayout(status_row)

        splitter.addWidget(template_group)

        # Bottom section - Results preview
        results_group = QGroupBox("Parsed Results Preview")
        results_layout = QVBoxLayout(results_group)

        # Results info
        self.results_info = QLabel("No data parsed yet")
        results_layout.addWidget(self.results_info)

        # Results table
        self.results_table = QTableWidget()
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.verticalHeader().setVisible(False)

        # Monospace font for data
        font = QFont("Consolas" if hasattr(QFont, "Consolas") else "Monospace", 9)
        self.results_table.setFont(font)

        results_layout.addWidget(self.results_table)

        splitter.addWidget(results_group)

        # Set splitter sizes (40% template, 60% results)
        splitter.setSizes([300, 450])
        layout.addWidget(splitter)

        # Export options
        export_group = QGroupBox("Export Options")
        export_layout = QHBoxLayout(export_group)

        self.include_headers_check = QCheckBox("Include headers in CSV")
        self.include_headers_check.setChecked(True)
        export_layout.addWidget(self.include_headers_check)

        self.pretty_json_check = QCheckBox("Pretty-print JSON")
        self.pretty_json_check.setChecked(True)
        export_layout.addWidget(self.pretty_json_check)

        export_layout.addStretch()

        self.export_json_btn = QPushButton("📄 Export JSON")
        self.export_json_btn.clicked.connect(self._export_json)
        self.export_json_btn.setEnabled(False)
        export_layout.addWidget(self.export_json_btn)

        self.export_csv_btn = QPushButton("📊 Export CSV")
        self.export_csv_btn.clicked.connect(self._export_csv)
        self.export_csv_btn.setEnabled(False)
        export_layout.addWidget(self.export_csv_btn)

        self.copy_json_btn = QPushButton("📋 Copy JSON")
        self.copy_json_btn.clicked.connect(self._copy_json)
        self.copy_json_btn.setEnabled(False)
        export_layout.addWidget(self.copy_json_btn)

        layout.addWidget(export_group)

        # Dialog buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _guess_filter(self) -> str:
        """Guess a filter string based on capture type and filename."""
        hints = []

        # From capture type
        if self.capture_type:
            # Map capture types to common template patterns
            type_map = {
                'arp': 'arp',
                'mac': 'mac_address',
                'routes': 'route',
                'bgp-summary': 'bgp_summary',
                'bgp-neighbor': 'bgp_neighbor',
                'lldp': 'lldp',
                'inventory': 'inventory',
                'version': 'version',
                'interface-status': 'interface',
                'int-status': 'interface',
                'ospf-neighbor': 'ospf_neighbor',
            }
            if self.capture_type.lower() in type_map:
                hints.append(type_map[self.capture_type.lower()])
            else:
                hints.append(self.capture_type.lower())

        # Could also try to detect platform from filename patterns
        filename = self.filepath.name.lower()
        if 'cisco' in filename or filename.startswith(('c9', 'n9k', 'asr')):
            hints.insert(0, 'cisco')
        elif 'arista' in filename or filename.startswith('dcs-'):
            hints.insert(0, 'arista')
        elif 'juniper' in filename:
            hints.insert(0, 'juniper')

        return ' '.join(hints)

    def _load_templates(self):
        """Load available templates from database."""
        self.template_combo.clear()
        self.template_combo.addItem("— Select Template —", None)

        # OS-independent path to ~/.vcollector/tfsm_templates.db
        db_path = Path.home() / ".vcollector" / "tfsm_templates.db"

        if not db_path.exists():
            self.status_label.setText(f"Template database not found: {db_path}")
            return

        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Use detected column name
            name_col = self._name_column

            # Check if 'source' column exists
            cursor.execute("PRAGMA table_info(templates)")
            columns = [row[1] for row in cursor.fetchall()]
            has_source = 'source' in columns

            if has_source:
                query = f"SELECT id, {name_col}, source FROM templates ORDER BY {name_col}"
            else:
                query = f"SELECT id, {name_col} FROM templates ORDER BY {name_col}"

            cursor.execute(query)

            self._templates = []
            for row in cursor.fetchall():
                template = dict(row)
                self._templates.append(template)

                display = template.get(name_col, template.get('name', 'Unknown'))
                if has_source and template.get('source'):
                    display += f"  [{template['source']}]"

                self.template_combo.addItem(display, template['id'])

            conn.close()
            self.status_label.setText(f"Loaded {len(self._templates)} templates")

        except Exception as e:
            self.status_label.setText(f"Error loading templates: {e}")


    def _on_auto_detect(self):
        """Run auto-detection for best matching template."""
        # Try the engine first, but fall back to local matching if it fails
        if TFSM_ENGINE_AVAILABLE:
            # Disable UI during processing
            self.auto_detect_btn.setEnabled(False)
            self.parse_btn.setEnabled(False)
            self.progress_bar.setVisible(True)
            self.status_label.setText("Auto-detecting template...")

            filter_hint = self.filter_input.text().strip()

            self._worker = TemplateMatchWorker(
                self.db_path,
                self._content,
                filter_hint
            )
            self._worker.finished.connect(self._on_auto_detect_finished)
            self._worker.progress.connect(self._on_progress)
            self._worker.error.connect(self._on_auto_detect_error_fallback)
            self._worker.start()
        else:
            # Use local fallback directly
            self._run_local_auto_detect()

    def _on_auto_detect_error_fallback(self, error: str):
        """If engine fails (e.g., schema mismatch), try local fallback."""
        self.status_label.setText(f"Engine error: {error}. Trying fallback...")
        self._run_local_auto_detect()

    def _run_local_auto_detect(self):
        """Local auto-detect that works with any schema."""
        self.auto_detect_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Auto-detecting template (local)...")

        filter_hint = self.filter_input.text().strip().lower()

        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get all templates
            name_col = self._name_column
            cursor.execute(f"SELECT id, {name_col}, textfsm_content FROM templates")
            templates = cursor.fetchall()
            conn.close()

            best_result = None
            best_score = 0
            best_name = ""

            # Filter templates by hint if provided
            candidates = []
            for t in templates:
                name = t[name_col] or ""
                if filter_hint:
                    # Check if any hint word matches template name
                    hint_words = filter_hint.split()
                    if any(word in name.lower() for word in hint_words):
                        candidates.append(t)
                else:
                    candidates.append(t)

            # If no filter matches, use all templates
            if not candidates:
                candidates = templates

            # Try each candidate template
            for t in candidates:
                name = t[name_col] or ""
                content = t['textfsm_content']

                if not content:
                    continue

                try:
                    tfsm = textfsm.TextFSM(io.StringIO(content))
                    parsed = tfsm.ParseText(self._content)

                    if parsed:
                        # Score based on number of records and fields populated
                        score = len(parsed)
                        # Bonus for more fields with data
                        if parsed:
                            filled_fields = sum(1 for v in parsed[0] if v)
                            score += filled_fields * 0.5

                        if score > best_score:
                            best_score = score
                            best_name = name
                            best_result = ParseResult(
                                success=True,
                                template_name=name,
                                headers=tfsm.header,
                                data=parsed,
                                score=score,
                                record_count=len(parsed)
                            )
                except Exception:
                    continue

            self.auto_detect_btn.setEnabled(True)
            self.parse_btn.setEnabled(True)
            self.progress_bar.setVisible(False)

            if best_result:
                self._current_result = best_result
                self._display_results(best_result)
                self.status_label.setText(
                    f"✓ Matched: {best_name} (score: {best_score:.1f}, "
                    f"{best_result.record_count} records)"
                )

                # Try to select the matched template in combo
                for i in range(self.template_combo.count()):
                    if best_name in self.template_combo.itemText(i):
                        self.template_combo.setCurrentIndex(i)
                        break
            else:
                self.status_label.setText("✗ No matching template found")
                self._clear_results()

        except Exception as e:
            self.auto_detect_btn.setEnabled(True)
            self.parse_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.status_label.setText(f"Error: {e}")
            QMessageBox.warning(self, "Auto-Detect Error", str(e))

    def _on_progress(self, message: str):
        """Handle progress updates."""
        self.status_label.setText(message)

    def _on_error(self, error: str):
        """Handle worker errors."""
        self.auto_detect_btn.setEnabled(True)
        self.parse_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Error: {error}")
        QMessageBox.warning(self, "Auto-Detect Error", error)

    def _on_auto_detect_finished(self, result: ParseResult):
        """Handle auto-detection completion."""
        self.auto_detect_btn.setEnabled(True)
        self.parse_btn.setEnabled(True)
        self.progress_bar.setVisible(False)

        if result.success:
            self._current_result = result
            self._display_results(result)
            self.status_label.setText(
                f"✓ Matched: {result.template_name} (score: {result.score:.1f}, "
                f"{result.record_count} records)"
            )

            # Try to select the matched template in combo
            for i in range(self.template_combo.count()):
                if result.template_name in self.template_combo.itemText(i):
                    self.template_combo.setCurrentIndex(i)
                    break
        else:
            self.status_label.setText(f"✗ {result.error or 'No matching template found'}")
            self._clear_results()

    def _on_template_selected(self, index: int):
        """Handle manual template selection."""
        # Don't auto-parse on selection, wait for Parse button
        pass

    def _on_manual_parse(self):
        """Parse with manually selected template."""
        template_id = self.template_combo.currentData()
        if not template_id:
            QMessageBox.warning(self, "No Template", "Please select a template first.")
            return

        # Get template content from database
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Use detected column name
            name_col = self._name_column
            cursor.execute(
                f"SELECT {name_col}, textfsm_content FROM templates WHERE id = ?",
                (template_id,)
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                QMessageBox.warning(self, "Error", "Template not found in database.")
                return

            template_name = row[name_col] if name_col in row.keys() else row[0]
            template_content = row['textfsm_content']

            # Parse with template
            self.status_label.setText(f"Parsing with {template_name}...")

            try:
                tfsm = textfsm.TextFSM(io.StringIO(template_content))
                parsed = tfsm.ParseText(self._content)
                headers = tfsm.header

                if parsed:
                    result = ParseResult(
                        success=True,
                        template_name=template_name,
                        headers=headers,
                        data=parsed,
                        score=0.0,  # Manual selection, no score
                        record_count=len(parsed)
                    )
                    self._current_result = result
                    self._display_results(result)
                    self.status_label.setText(
                        f"✓ Parsed {result.record_count} records with {template_name}"
                    )
                else:
                    self.status_label.setText(f"✗ Template matched but produced no output")
                    self._clear_results()

            except Exception as e:
                self.status_label.setText(f"✗ Parse error: {e}")
                self._clear_results()

        except Exception as e:
            QMessageBox.warning(self, "Database Error", str(e))

    def _display_results(self, result: ParseResult):
        """Display parsed results in table."""
        self.results_table.clear()

        if not result.data:
            self.results_info.setText("No data to display")
            self.results_table.setRowCount(0)
            self.results_table.setColumnCount(0)
            self._enable_export(False)
            return

        # Check if data is list of dicts or list of lists
        first_row = result.data[0]

        if isinstance(first_row, dict):
            # Data is list of dicts (from TextFSMAutoEngine)
            headers = list(first_row.keys())
            self.results_table.setColumnCount(len(headers))
            self.results_table.setHorizontalHeaderLabels(headers)
            self.results_table.setRowCount(len(result.data))

            for row_idx, item in enumerate(result.data):
                for col_idx, (key, value) in enumerate(item.items()):
                    cell_item = QTableWidgetItem(str(value) if value is not None else "")
                    self.results_table.setItem(row_idx, col_idx, cell_item)

            # Update result headers for export
            result.headers = headers
        else:
            # Data is list of lists (from manual TextFSM parsing)
            headers = result.headers
            self.results_table.setColumnCount(len(headers))
            self.results_table.setHorizontalHeaderLabels(headers)
            self.results_table.setRowCount(len(result.data))

            for row_idx, row_data in enumerate(result.data):
                for col_idx, value in enumerate(row_data):
                    cell_item = QTableWidgetItem(str(value) if value is not None else "")
                    self.results_table.setItem(row_idx, col_idx, cell_item)

        # Resize columns
        self.results_table.resizeColumnsToContents()

        # Update info
        self.results_info.setText(
            f"<b>{result.record_count}</b> records, "
            f"<b>{len(headers)}</b> fields: {', '.join(headers)}"
        )

        self._enable_export(True)

    def _clear_results(self):
        """Clear results table."""
        self.results_table.clear()
        self.results_table.setRowCount(0)
        self.results_table.setColumnCount(0)
        self.results_info.setText("No data parsed yet")
        self._current_result = None
        self._enable_export(False)

    def _enable_export(self, enabled: bool):
        """Enable/disable export buttons."""
        self.export_json_btn.setEnabled(enabled)
        self.export_csv_btn.setEnabled(enabled)
        self.copy_json_btn.setEnabled(enabled)

    def _export_json(self):
        """Export results to JSON file."""
        if not self._current_result:
            return

        # Suggest filename
        base_name = self.filepath.stem
        suggested = f"{base_name}_parsed.json"

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Export JSON",
            suggested,
            "JSON Files (*.json);;All Files (*)"
        )

        if not filepath:
            return

        try:
            data = self._current_result.to_dicts()

            # Add metadata wrapper
            export_data = {
                "source_file": str(self.filepath),
                "template": self._current_result.template_name,
                "exported_at": datetime.now().isoformat(),
                "record_count": len(data),
                "fields": self._current_result.headers,
                "data": data
            }

            indent = 2 if self.pretty_json_check.isChecked() else None

            with open(filepath, 'w') as f:
                json.dump(export_data, f, indent=indent, default=str)

            self.status_label.setText(f"✓ Exported to {Path(filepath).name}")
            QMessageBox.information(
                self, "Export Complete",
                f"Exported {len(data)} records to:\n{filepath}"
            )

        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    def _export_csv(self):
        """Export results to CSV file."""
        if not self._current_result:
            return

        # Suggest filename
        base_name = self.filepath.stem
        suggested = f"{base_name}_parsed.csv"

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Export CSV",
            suggested,
            "CSV Files (*.csv);;All Files (*)"
        )

        if not filepath:
            return

        try:
            with open(filepath, 'w', newline='') as f:
                data = self._current_result.data
                first_row = data[0] if data else None

                if isinstance(first_row, dict):
                    # Data is list of dicts - use DictWriter
                    headers = self._current_result.headers
                    writer = csv.DictWriter(f, fieldnames=headers)

                    if self.include_headers_check.isChecked():
                        writer.writeheader()

                    writer.writerows(data)
                else:
                    # Data is list of lists
                    writer = csv.writer(f)

                    if self.include_headers_check.isChecked():
                        writer.writerow(self._current_result.headers)

                    for row in data:
                        writer.writerow(row)

            self.status_label.setText(f"✓ Exported to {Path(filepath).name}")
            QMessageBox.information(
                self, "Export Complete",
                f"Exported {len(self._current_result.data)} records to:\n{filepath}"
            )

        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    def _copy_json(self):
        """Copy JSON to clipboard."""
        if not self._current_result:
            return

        try:
            data = self._current_result.to_dicts()
            indent = 2 if self.pretty_json_check.isChecked() else None
            json_str = json.dumps(data, indent=indent, default=str)

            clipboard = QApplication.clipboard()
            clipboard.setText(json_str)

            self.status_label.setText(f"✓ Copied {len(data)} records to clipboard")

        except Exception as e:
            QMessageBox.warning(self, "Copy Error", str(e))


# =============================================================================
# Integration function for OutputView
# =============================================================================

def add_smart_export_to_output_view(output_view_class):
    """
    Decorator/mixin to add Smart Export functionality to OutputView.

    Usage in output_view.py:
        from smart_export_dialog import add_smart_export_to_output_view, SmartExportDialog

        # In OutputView._show_context_menu(), add:
        smart_export_action = QAction("🔧 Smart Export (Parse && Export)", self)
        smart_export_action.triggered.connect(self._smart_export_selected)
        menu.addAction(smart_export_action)

        # Add method to OutputView:
        def _smart_export_selected(self):
            selected = self.files_table.selectedItems()
            if not selected:
                return
            row = selected[0].row()
            filepath = self.files_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            capture_type = self.files_table.item(row, 4).text().lower()

            if filepath:
                dialog = SmartExportDialog(
                    filepath=Path(filepath),
                    capture_type=capture_type,
                    parent=self
                )
                dialog.exec()
    """
    pass  # Documentation only - actual integration is in output_view.py


# =============================================================================
# Standalone test
# =============================================================================

if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Test with a sample file if provided
    if len(sys.argv) > 1:
        test_file = Path(sys.argv[1])
    else:
        # Default test location
        test_file = Path.home() / ".vcollector" / "collections" / "arp" / "test.txt"
        if not test_file.exists():
            print("Usage: python smart_export_dialog.py <path_to_captured_output>")
            print("\nOr place a test file at ~/.vcollector/collections/arp/test.txt")
            sys.exit(1)

    dialog = SmartExportDialog(
        filepath=test_file,
        capture_type=test_file.parent.name  # Use parent folder as type hint
    )
    dialog.exec()

    sys.exit(0)