"""
VelocityCollector Output View

Browse captured output files organized by capture type.
View file contents, open externally, manage captured data.
Search within and across capture files.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QAbstractItemView,
    QMenu, QMessageBox, QDialog, QPlainTextEdit, QFrame, QScrollArea,
    QSplitter, QFileDialog, QApplication, QGroupBox, QCheckBox, QProgressBar,
    QTabWidget
)
from PyQt6.QtCore import Qt, pyqtSignal, QProcess, QThread, QTimer
from PyQt6.QtGui import QAction, QColor, QShortcut, QKeySequence, QFont, QTextCharFormat, QTextCursor

from pathlib import Path
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
import os
import subprocess
import platform
import re


@dataclass
class CaptureFile:
    """Represents a captured output file."""
    device_name: str
    capture_type: str
    filepath: Path
    file_size: int
    captured_at: Optional[datetime]
    job_history_id: Optional[int] = None

    @property
    def filename(self) -> str:
        return self.filepath.name

    @property
    def size_formatted(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        else:
            return f"{self.file_size / (1024 * 1024):.1f} MB"


@dataclass
class SearchResult:
    """Represents a search match within a file."""
    capture_file: CaptureFile
    line_number: int
    line_content: str
    match_start: int
    match_end: int


class SearchWorker(QThread):
    """Background thread for searching file contents."""

    progress = pyqtSignal(int, int)  # current, total
    result_found = pyqtSignal(object)  # SearchResult
    finished_search = pyqtSignal(int)  # total matches

    def __init__(self, files: List[CaptureFile], pattern: str, case_sensitive: bool = False, regex: bool = False):
        super().__init__()
        self.files = files
        self.pattern = pattern
        self.case_sensitive = case_sensitive
        self.regex = regex
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total_matches = 0

        # Compile pattern
        flags = 0 if self.case_sensitive else re.IGNORECASE
        try:
            if self.regex:
                compiled = re.compile(self.pattern, flags)
            else:
                compiled = re.compile(re.escape(self.pattern), flags)
        except re.error as e:
            self.finished_search.emit(0)
            return

        for i, capture_file in enumerate(self.files):
            if self._cancelled:
                break

            self.progress.emit(i + 1, len(self.files))

            try:
                content = capture_file.filepath.read_text(errors='replace')
                lines = content.split('\n')

                for line_num, line in enumerate(lines, 1):
                    if self._cancelled:
                        break

                    for match in compiled.finditer(line):
                        result = SearchResult(
                            capture_file=capture_file,
                            line_number=line_num,
                            line_content=line,
                            match_start=match.start(),
                            match_end=match.end()
                        )
                        self.result_found.emit(result)
                        total_matches += 1

                        # Limit results per file to prevent flooding
                        if total_matches > 10000:
                            self.finished_search.emit(total_matches)
                            return

            except Exception as e:
                continue  # Skip unreadable files

        self.finished_search.emit(total_matches)


class FileViewerDialog(QDialog):
    """Dialog to view file contents with optional search highlighting."""

    def __init__(self, filepath: Path, search_term: str = None, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.search_term = search_term
        self.setWindowTitle(f"View: {filepath.name}")
        self.setMinimumSize(700, 500)
        self.resize(900, 600)
        self.init_ui()
        self.load_content()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header with file info
        header_layout = QHBoxLayout()

        self.filename_label = QLabel(str(self.filepath))
        self.filename_label.setProperty("subheading", True)
        self.filename_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(self.filename_label, 1)

        self.size_label = QLabel("")
        header_layout.addWidget(self.size_label)

        layout.addLayout(header_layout)

        # Find bar (for in-file search)
        find_layout = QHBoxLayout()
        find_layout.addWidget(QLabel("Find:"))

        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Search in file...")
        self.find_input.returnPressed.connect(self._find_next)
        if self.search_term:
            self.find_input.setText(self.search_term)
        find_layout.addWidget(self.find_input)

        find_prev_btn = QPushButton("◀ Prev")
        find_prev_btn.clicked.connect(self._find_prev)
        find_layout.addWidget(find_prev_btn)

        find_next_btn = QPushButton("Next ▶")
        find_next_btn.clicked.connect(self._find_next)
        find_layout.addWidget(find_next_btn)

        self.match_label = QLabel("")
        find_layout.addWidget(self.match_label)

        find_layout.addStretch()
        layout.addLayout(find_layout)

        # Content viewer - plain text with monospace font
        self.content_view = QPlainTextEdit()
        self.content_view.setReadOnly(True)
        self.content_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        # Set monospace font
        font = QFont()
        if platform.system() == "Windows":
            font.setFamily("Consolas")
            font.setPointSize(10)
        elif platform.system() == "Darwin":
            font.setFamily("Monaco")
            font.setPointSize(11)
        else:
            font.setFamily("Monospace")
            font.setPointSize(10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.content_view.setFont(font)

        layout.addWidget(self.content_view)

        # Button bar
        button_layout = QHBoxLayout()

        copy_btn = QPushButton("Copy All")
        copy_btn.clicked.connect(self._copy_all)
        button_layout.addWidget(copy_btn)

        open_external_btn = QPushButton("Open External")
        open_external_btn.clicked.connect(self._open_external)
        button_layout.addWidget(open_external_btn)

        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(self._open_folder)
        button_layout.addWidget(open_folder_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.find_input.setFocus())
        QShortcut(QKeySequence("F3"), self, self._find_next)
        QShortcut(QKeySequence("Shift+F3"), self, self._find_prev)

    def load_content(self):
        """Load file content."""
        try:
            if self.filepath.exists():
                size = self.filepath.stat().st_size
                self.size_label.setText(f"({self._format_size(size)})")

                # Read content (limit to 1MB for display)
                if size > 1024 * 1024:
                    content = self.filepath.read_text(errors='replace')[:1024 * 1024]
                    content += f"\n\n... [Truncated - file is {self._format_size(size)}] ..."
                else:
                    content = self.filepath.read_text(errors='replace')

                self.content_view.setPlainText(content)

                # If search term provided, highlight and jump to first match
                if self.search_term:
                    QTimer.singleShot(100, self._find_next)
            else:
                self.content_view.setPlainText(f"File not found: {self.filepath}")
        except Exception as e:
            self.content_view.setPlainText(f"Error reading file: {e}")

    def _find_next(self):
        """Find next occurrence of search term."""
        term = self.find_input.text()
        if not term:
            return

        # Find from current cursor position
        found = self.content_view.find(term)
        if not found:
            # Wrap around to beginning
            cursor = self.content_view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.content_view.setTextCursor(cursor)
            found = self.content_view.find(term)

        self._update_match_count(term)

    def _find_prev(self):
        """Find previous occurrence of search term."""
        term = self.find_input.text()
        if not term:
            return

        found = self.content_view.find(term, QTextDocument.FindFlag.FindBackward)
        if not found:
            # Wrap around to end
            cursor = self.content_view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.content_view.setTextCursor(cursor)
            found = self.content_view.find(term, QTextDocument.FindFlag.FindBackward)

        self._update_match_count(term)

    def _update_match_count(self, term: str):
        """Count and display total matches."""
        content = self.content_view.toPlainText()
        count = content.lower().count(term.lower())
        self.match_label.setText(f"{count} matches")

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"

    def _copy_all(self):
        """Copy all content to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.content_view.toPlainText())

    def _open_external(self):
        """Open file in default external editor."""
        try:
            if platform.system() == "Windows":
                os.startfile(str(self.filepath))
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(self.filepath)])
            else:
                subprocess.run(["xdg-open", str(self.filepath)])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to open file: {e}")

    def _open_folder(self):
        """Open containing folder."""
        try:
            folder = self.filepath.parent
            if platform.system() == "Windows":
                subprocess.run(["explorer", "/select,", str(self.filepath)])
            elif platform.system() == "Darwin":
                subprocess.run(["open", "-R", str(self.filepath)])
            else:
                subprocess.run(["xdg-open", str(folder)])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to open folder: {e}")


# Need to import for find functionality
from PyQt6.QtGui import QTextDocument


class OutputView(QWidget):
    """Browse captured output files by capture type with content search."""

    # Signals
    view_history_requested = pyqtSignal(int)  # job_history_id

    def __init__(self, collections_path: Optional[Path] = None, parent=None):
        super().__init__(parent)

        # Default collections path
        if collections_path:
            self.collections_path = collections_path
        else:
            self.collections_path = Path.home() / ".vcollector" / "collections"

        self._files: List[CaptureFile] = []
        self._capture_types: List[str] = []
        self._search_results: List[SearchResult] = []
        self._search_worker: Optional[SearchWorker] = None
        self._showing_search_results = False

        self.init_ui()
        self.refresh_capture_types()
        self.refresh_files()

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
        title = QLabel("Captured Output")
        title.setProperty("heading", True)
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Collections path indicator
        path_label = QLabel(str(self.collections_path))
        path_label.setProperty("subheading", True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(path_label)

        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.setProperty("secondary", True)
        open_folder_btn.clicked.connect(self._open_collections_folder)
        header_layout.addWidget(open_folder_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh_all)
        header_layout.addWidget(refresh_btn)

        layout.addLayout(header_layout)

        # Stats row
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        self.total_files_card = self._create_stat_card("Total Files", "0")
        stats_layout.addWidget(self.total_files_card)

        self.total_size_card = self._create_stat_card("Total Size", "0 B")
        stats_layout.addWidget(self.total_size_card)

        self.capture_types_card = self._create_stat_card("Capture Types", "0")
        stats_layout.addWidget(self.capture_types_card)

        self.devices_card = self._create_stat_card("Devices", "0")
        stats_layout.addWidget(self.devices_card)

        stats_layout.addStretch()
        layout.addLayout(stats_layout)

        # Filter bar
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(12)

        filter_layout.addWidget(QLabel("Capture Type:"))
        self.type_filter = QComboBox()
        self.type_filter.setMinimumWidth(150)
        self.type_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.type_filter)

        filter_layout.addWidget(QLabel("Device:"))
        self.device_search = QLineEdit()
        self.device_search.setPlaceholderText("Filter by device name...")
        self.device_search.setMinimumWidth(200)
        self.device_search.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.device_search)

        filter_layout.addWidget(QLabel("Time:"))
        self.time_filter = QComboBox()
        self.time_filter.addItems(["All Time", "Last 24 Hours", "Last 7 Days", "Last 30 Days"])
        self.time_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.time_filter)

        filter_layout.addStretch()

        # Clear capture type button
        clear_type_btn = QPushButton("Clear Type...")
        clear_type_btn.setProperty("secondary", True)
        clear_type_btn.setToolTip("Delete all files for selected capture type")
        clear_type_btn.clicked.connect(self._clear_capture_type)
        filter_layout.addWidget(clear_type_btn)

        clear_btn = QPushButton("Clear Filters")
        clear_btn.setProperty("secondary", True)
        clear_btn.clicked.connect(self._clear_filters)
        filter_layout.addWidget(clear_btn)

        layout.addLayout(filter_layout)

        # =====================================================================
        # CONTENT SEARCH SECTION
        # =====================================================================
        search_group = QGroupBox("Content Search")
        search_layout = QVBoxLayout(search_group)

        # Search input row
        search_input_layout = QHBoxLayout()

        self.content_search = QLineEdit()
        self.content_search.setPlaceholderText("Search within file contents (grep-style)...")
        self.content_search.setMinimumWidth(300)
        self.content_search.returnPressed.connect(self._execute_search)
        search_input_layout.addWidget(self.content_search)

        self.search_scope = QComboBox()
        self.search_scope.addItems(["Current View", "All Capture Types"])
        self.search_scope.setToolTip("Search scope")
        search_input_layout.addWidget(self.search_scope)

        self.case_sensitive_check = QCheckBox("Case Sensitive")
        search_input_layout.addWidget(self.case_sensitive_check)

        self.regex_check = QCheckBox("Regex")
        search_input_layout.addWidget(self.regex_check)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._execute_search)
        search_input_layout.addWidget(self.search_btn)

        self.cancel_search_btn = QPushButton("Cancel")
        self.cancel_search_btn.clicked.connect(self._cancel_search)
        self.cancel_search_btn.setEnabled(False)
        search_input_layout.addWidget(self.cancel_search_btn)

        search_input_layout.addStretch()

        self.clear_search_btn = QPushButton("Clear Results")
        self.clear_search_btn.setProperty("secondary", True)
        self.clear_search_btn.clicked.connect(self._clear_search_results)
        self.clear_search_btn.setEnabled(False)
        search_input_layout.addWidget(self.clear_search_btn)

        search_layout.addLayout(search_input_layout)

        # Search progress bar (hidden by default)
        self.search_progress = QProgressBar()
        self.search_progress.setVisible(False)
        self.search_progress.setTextVisible(True)
        search_layout.addWidget(self.search_progress)

        # Search status
        self.search_status = QLabel("")
        search_layout.addWidget(self.search_status)

        layout.addWidget(search_group)
        # =====================================================================

        # Tab widget for Browse / Search Results
        self.results_tabs = QTabWidget()

        # ---- Tab 1: Browse Files ----
        browse_widget = QWidget()
        browse_layout = QVBoxLayout(browse_widget)
        browse_layout.setContentsMargins(0, 8, 0, 0)

        self.files_table = QTableWidget()
        self.files_table.setColumnCount(5)
        self.files_table.setHorizontalHeaderLabels([
            "Device", "Filename", "Size", "Captured", "Type"
        ])
        self.files_table.setAlternatingRowColors(True)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.files_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files_table.horizontalHeader().setStretchLastSection(True)
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setSortingEnabled(True)

        # Context menu
        self.files_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files_table.customContextMenuRequested.connect(self._show_context_menu)

        # Double-click to view
        self.files_table.doubleClicked.connect(self._on_double_click)

        browse_layout.addWidget(self.files_table)
        self.results_tabs.addTab(browse_widget, "📁 Browse Files")

        # ---- Tab 2: Search Results ----
        search_widget = QWidget()
        search_tab_layout = QVBoxLayout(search_widget)
        search_tab_layout.setContentsMargins(0, 8, 0, 0)

        self.search_results_table = QTableWidget()
        self.search_results_table.setColumnCount(5)
        self.search_results_table.setHorizontalHeaderLabels([
            "Device", "Type", "Line #", "Match Context", "File"
        ])
        self.search_results_table.setAlternatingRowColors(True)
        self.search_results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.search_results_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.search_results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.search_results_table.horizontalHeader().setStretchLastSection(True)
        self.search_results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.search_results_table.verticalHeader().setVisible(False)
        self.search_results_table.setSortingEnabled(True)

        # Double-click to open file at match
        self.search_results_table.doubleClicked.connect(self._on_search_result_double_click)

        # Search results status within tab
        self.search_results_status = QLabel("Run a search to see results here")
        self.search_results_status.setStyleSheet("color: #888; padding: 8px;")

        search_tab_layout.addWidget(self.search_results_table)
        search_tab_layout.addWidget(self.search_results_status)
        self.results_tabs.addTab(search_widget, "🔍 Search Results (0)")

        # Set minimum height for tab widget
        self.results_tabs.setMinimumHeight(400)

        layout.addWidget(self.results_tabs)

        # Status bar
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # Shortcuts
        QShortcut(QKeySequence("F5"), self, self.refresh_all)
        QShortcut(QKeySequence("Return"), self, self._view_selected)
        QShortcut(QKeySequence("Delete"), self, self._delete_selected)
        QShortcut(QKeySequence("Ctrl+C"), self, self._copy_path)
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.content_search.setFocus())
        QShortcut(QKeySequence("Escape"), self, self._clear_search_results)

    # =========================================================================
    # SEARCH METHODS
    # =========================================================================

    def _execute_search(self):
        """Execute content search across files."""
        pattern = self.content_search.text().strip()
        if not pattern:
            return

        # Cancel any existing search
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.cancel()
            self._search_worker.wait()

        # Determine files to search
        if self.search_scope.currentText() == "All Capture Types":
            files_to_search = self._get_all_files()
        else:
            files_to_search = self._get_filtered_files()

        if not files_to_search:
            self.search_status.setText("No files to search")
            return

        # Clear previous results
        self._search_results = []
        self.search_results_table.setRowCount(0)

        # Show progress
        self.search_progress.setVisible(True)
        self.search_progress.setValue(0)
        self.search_progress.setMaximum(len(files_to_search))
        self.search_btn.setEnabled(False)
        self.cancel_search_btn.setEnabled(True)
        self.search_status.setText(f"Searching {len(files_to_search)} files...")

        # Start search worker
        self._search_worker = SearchWorker(
            files=files_to_search,
            pattern=pattern,
            case_sensitive=self.case_sensitive_check.isChecked(),
            regex=self.regex_check.isChecked()
        )
        self._search_worker.progress.connect(self._on_search_progress)
        self._search_worker.result_found.connect(self._on_search_result)
        self._search_worker.finished_search.connect(self._on_search_finished)
        self._search_worker.start()

    def _cancel_search(self):
        """Cancel running search."""
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.cancel()
            self.search_status.setText("Search cancelled")

    def _on_search_progress(self, current: int, total: int):
        """Update search progress."""
        self.search_progress.setValue(current)
        self.search_progress.setFormat(f"Searching... {current}/{total} files")

    def _on_search_result(self, result: SearchResult):
        """Handle a search result."""
        self._search_results.append(result)

        # Add to results table
        row = self.search_results_table.rowCount()
        self.search_results_table.insertRow(row)

        # Device
        device_item = QTableWidgetItem(result.capture_file.device_name)
        device_item.setData(Qt.ItemDataRole.UserRole, result)
        self.search_results_table.setItem(row, 0, device_item)

        # Type
        self.search_results_table.setItem(row, 1, QTableWidgetItem(result.capture_file.capture_type))

        # Line number
        self.search_results_table.setItem(row, 2, QTableWidgetItem(str(result.line_number)))

        # Match context (truncate long lines)
        context = result.line_content.strip()
        if len(context) > 100:
            # Try to center the match
            start = max(0, result.match_start - 40)
            end = min(len(context), result.match_end + 40)
            context = ("..." if start > 0 else "") + context[start:end] + ("..." if end < len(result.line_content) else "")
        self.search_results_table.setItem(row, 3, QTableWidgetItem(context))

        # File path
        self.search_results_table.setItem(row, 4, QTableWidgetItem(result.capture_file.filename))

        # Switch to search results tab and update title
        if not self._showing_search_results:
            self._showing_search_results = True
            self.results_tabs.setCurrentIndex(1)  # Switch to Search Results tab
            self.clear_search_btn.setEnabled(True)

        # Update tab title with count
        self.results_tabs.setTabText(1, f"🔍 Search Results ({len(self._search_results)})")

    def _on_search_finished(self, total_matches: int):
        """Handle search completion."""
        self.search_progress.setVisible(False)
        self.search_btn.setEnabled(True)
        self.cancel_search_btn.setEnabled(False)

        files_with_matches = len(set(r.capture_file.filepath for r in self._search_results))
        status_msg = f"Found {total_matches} matches in {files_with_matches} files"
        self.search_status.setText(status_msg)
        self.search_results_status.setText(status_msg)

        # Update tab title with final count
        self.results_tabs.setTabText(1, f"🔍 Search Results ({total_matches})")

        if total_matches == 0:
            self._showing_search_results = False
            self.search_results_status.setText("No matches found")

    def _on_search_result_double_click(self, index):
        """Open file at search result location."""
        row = index.row()
        result_item = self.search_results_table.item(row, 0)
        if result_item:
            result: SearchResult = result_item.data(Qt.ItemDataRole.UserRole)
            if result:
                dialog = FileViewerDialog(
                    result.capture_file.filepath,
                    search_term=self.content_search.text(),
                    parent=self
                )
                dialog.exec()

    def _clear_search_results(self):
        """Clear search results and switch back to browse tab."""
        self._search_results = []
        self.search_results_table.setRowCount(0)
        self._showing_search_results = False
        self.clear_search_btn.setEnabled(False)
        self.search_status.setText("")
        self.search_results_status.setText("Run a search to see results here")
        self.content_search.clear()

        # Reset tab title and switch to browse tab
        self.results_tabs.setTabText(1, "🔍 Search Results (0)")
        self.results_tabs.setCurrentIndex(0)

    def _get_all_files(self) -> List[CaptureFile]:
        """Get all capture files (ignoring current filter)."""
        files = []
        if not self.collections_path.exists():
            return files

        for capture_type_dir in self.collections_path.iterdir():
            if not capture_type_dir.is_dir() or capture_type_dir.name.startswith('.'):
                continue

            for filepath in capture_type_dir.rglob("*.txt"):
                if filepath.is_file():
                    stat = filepath.stat()
                    files.append(CaptureFile(
                        device_name=filepath.stem,
                        capture_type=capture_type_dir.name,
                        filepath=filepath,
                        file_size=stat.st_size,
                        captured_at=datetime.fromtimestamp(stat.st_mtime)
                    ))

        return files

    def _get_filtered_files(self) -> List[CaptureFile]:
        """Get files matching current filter (for search scope)."""
        # Apply current filters
        filtered = self._files.copy()

        # Type filter
        selected_type = self.type_filter.currentText()
        if selected_type and selected_type != "All Types":
            filtered = [f for f in filtered if f.capture_type == selected_type]

        # Device filter
        device_pattern = self.device_search.text().strip().lower()
        if device_pattern:
            filtered = [f for f in filtered if device_pattern in f.device_name.lower()]

        # Time filter
        time_selection = self.time_filter.currentText()
        if time_selection != "All Time":
            now = datetime.now()
            if time_selection == "Last 24 Hours":
                cutoff = now - timedelta(hours=24)
            elif time_selection == "Last 7 Days":
                cutoff = now - timedelta(days=7)
            elif time_selection == "Last 30 Days":
                cutoff = now - timedelta(days=30)
            else:
                cutoff = None

            if cutoff:
                filtered = [f for f in filtered if f.captured_at and f.captured_at >= cutoff]

        return filtered

    # =========================================================================
    # EXISTING METHODS (abbreviated - keep your original implementations)
    # =========================================================================

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

    def refresh_all(self):
        """Refresh everything."""
        self.refresh_capture_types()
        self.refresh_files()

    def refresh_capture_types(self):
        """Scan collections directory for capture types."""
        self._capture_types = []

        if self.collections_path.exists():
            for item in self.collections_path.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    self._capture_types.append(item.name)

        self._capture_types.sort()

        # Update filter dropdown
        current = self.type_filter.currentText()
        self.type_filter.clear()
        self.type_filter.addItem("All Types")
        self.type_filter.addItems(self._capture_types)

        # Restore selection if possible
        idx = self.type_filter.findText(current)
        if idx >= 0:
            self.type_filter.setCurrentIndex(idx)

    def refresh_files(self):
        """Refresh file list from disk."""
        self._files = []

        if not self.collections_path.exists():
            self._update_table()
            self._update_stats()
            return

        # Scan all capture type directories
        for capture_type_dir in self.collections_path.iterdir():
            if not capture_type_dir.is_dir() or capture_type_dir.name.startswith('.'):
                continue

            capture_type = capture_type_dir.name

            for filepath in capture_type_dir.rglob("*.txt"):
                if filepath.is_file():
                    stat = filepath.stat()
                    self._files.append(CaptureFile(
                        device_name=filepath.stem,
                        capture_type=capture_type,
                        filepath=filepath,
                        file_size=stat.st_size,
                        captured_at=datetime.fromtimestamp(stat.st_mtime)
                    ))

        self._update_table()
        self._update_stats()

    def _update_table(self):
        """Update files table with current filter."""
        self.files_table.setSortingEnabled(False)
        self.files_table.setRowCount(0)

        filtered = self._get_filtered_files()

        for capture_file in filtered:
            row = self.files_table.rowCount()
            self.files_table.insertRow(row)

            # Device name (store filepath in UserRole)
            device_item = QTableWidgetItem(capture_file.device_name)
            device_item.setData(Qt.ItemDataRole.UserRole, str(capture_file.filepath))
            self.files_table.setItem(row, 0, device_item)

            # Filename
            self.files_table.setItem(row, 1, QTableWidgetItem(capture_file.filename))

            # Size
            self.files_table.setItem(row, 2, QTableWidgetItem(capture_file.size_formatted))

            # Captured time
            if capture_file.captured_at:
                time_str = capture_file.captured_at.strftime("%H:%M:%S")
            else:
                time_str = "—"
            self.files_table.setItem(row, 3, QTableWidgetItem(time_str))

            # Type
            self.files_table.setItem(row, 4, QTableWidgetItem(capture_file.capture_type.upper()))

        self.files_table.setSortingEnabled(True)
        self.status_label.setText(f"Showing {len(filtered)} of {len(self._files)} files")

    def _update_stats(self):
        """Update stat cards."""
        self._update_stat_card(self.total_files_card, str(len(self._files)))

        total_size = sum(f.file_size for f in self._files)
        self._update_stat_card(self.total_size_card, self._format_size(total_size))

        self._update_stat_card(self.capture_types_card, str(len(self._capture_types)))

        unique_devices = len(set(f.device_name for f in self._files))
        self._update_stat_card(self.devices_card, str(unique_devices))

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"

    def _on_filter_changed(self):
        """Handle filter change."""
        self._update_table()

    def _clear_filters(self):
        """Clear all filters."""
        self.type_filter.setCurrentIndex(0)
        self.device_search.clear()
        self.time_filter.setCurrentIndex(0)

    def _on_double_click(self, index):
        """Handle double-click on file row."""
        row = index.row()
        filepath = self.files_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if filepath:
            dialog = FileViewerDialog(Path(filepath), parent=self)
            dialog.exec()

    def _view_selected(self):
        """View selected file."""
        selected = self.files_table.selectedItems()
        if selected:
            row = selected[0].row()
            filepath = self.files_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if filepath:
                dialog = FileViewerDialog(Path(filepath), parent=self)
                dialog.exec()

    def _show_context_menu(self, position):
        """Show context menu for file table."""
        menu = QMenu(self)

        view_action = QAction("View File", self)
        view_action.triggered.connect(self._view_selected)
        menu.addAction(view_action)

        menu.addSeparator()

        open_external = QAction("Open External", self)
        open_external.triggered.connect(self._open_selected_external)
        menu.addAction(open_external)

        open_folder = QAction("Open Folder", self)
        open_folder.triggered.connect(self._open_selected_folder)
        menu.addAction(open_folder)

        menu.addSeparator()

        copy_path = QAction("Copy Path", self)
        copy_path.triggered.connect(self._copy_path)
        menu.addAction(copy_path)

        menu.addSeparator()

        delete_action = QAction("Delete", self)
        delete_action.triggered.connect(self._delete_selected)
        menu.addAction(delete_action)

        menu.exec(self.files_table.mapToGlobal(position))

    def _open_collections_folder(self):
        """Open collections folder in file manager."""
        try:
            folder = self.collections_path
            if not folder.exists():
                folder.mkdir(parents=True, exist_ok=True)

            if platform.system() == "Windows":
                subprocess.run(["explorer", str(folder)])
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(folder)])
            else:
                subprocess.run(["xdg-open", str(folder)])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to open folder: {e}")

    def _open_selected_external(self):
        """Open selected file in default application."""
        selected = self.files_table.selectedItems()
        if selected:
            row = selected[0].row()
            filepath = self.files_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if filepath:
                try:
                    if platform.system() == "Windows":
                        os.startfile(filepath)
                    elif platform.system() == "Darwin":
                        subprocess.run(["open", filepath])
                    else:
                        subprocess.run(["xdg-open", filepath])
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to open file: {e}")

    def _open_selected_folder(self):
        """Open containing folder for selected file."""
        selected = self.files_table.selectedItems()
        if selected:
            row = selected[0].row()
            filepath = self.files_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if filepath:
                try:
                    folder = Path(filepath).parent
                    if platform.system() == "Windows":
                        subprocess.run(["explorer", "/select,", filepath])
                    elif platform.system() == "Darwin":
                        subprocess.run(["open", "-R", filepath])
                    else:
                        subprocess.run(["xdg-open", str(folder)])
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to open folder: {e}")

    def _copy_path(self):
        """Copy selected file path to clipboard."""
        selected = self.files_table.selectedItems()
        if selected:
            row = selected[0].row()
            filepath = self.files_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if filepath:
                self._copy_to_clipboard(filepath)

    def _copy_to_clipboard(self, text: str):
        """Copy text to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.status_label.setText(f"Copied: {text}")

    def _delete_selected(self):
        """Delete selected files."""
        selected_rows = set(item.row() for item in self.files_table.selectedItems())
        if not selected_rows:
            return

        # Gather file paths
        filepaths = []
        for row in selected_rows:
            filepath = self.files_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if filepath:
                filepaths.append(Path(filepath))

        if not filepaths:
            return

        # Confirm deletion
        if len(filepaths) == 1:
            msg = f"Delete file?\n\n{filepaths[0].name}"
        else:
            msg = f"Delete {len(filepaths)} files?\n\n"
            msg += "\n".join(f.name for f in filepaths[:5])
            if len(filepaths) > 5:
                msg += f"\n... and {len(filepaths) - 5} more"

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            deleted = 0
            for fp in filepaths:
                try:
                    fp.unlink()
                    deleted += 1
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to delete {fp.name}: {e}")

            self.refresh_files()
            self.status_label.setText(f"Deleted {deleted} files")

    def _clear_capture_type(self):
        """Clear all files for selected capture type."""
        # Implementation from your original file
        pass  # Keep your existing implementation


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)

    window = QMainWindow()
    window.setWindowTitle("Captured Output - VelocityCollector")
    window.resize(1000, 700)

    view = OutputView()
    window.setCentralWidget(view)

    window.show()
    sys.exit(app.exec())