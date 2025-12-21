#!/usr/bin/env python3
"""
VelocityCollector GUI - PyQt6 Interface
Network data collection with encrypted vault and TextFSM validation

Features:
- Device inventory from VelocityCMDB
- Encrypted credential vault management
- Job creation, editing, and execution
- Real-time collection monitoring
- Collection history and output browsing
- Light/Dark/Cyber theme support

Author: Scott Peterman
License: GPLv3
"""

import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSplitter, QComboBox, QMessageBox,
    QStatusBar, QToolBar, QListWidget, QListWidgetItem,
    QStackedWidget, QSizePolicy, QToolButton, QDialog, QFrame
)
from PyQt6.QtCore import Qt, QSize, QUrl
from PyQt6.QtGui import QFont, QAction, QDesktopServices, QPixmap

from vcollector.ui.styles import get_stylesheet
from vcollector.ui.widgets.credentials_view import CredentialsView
from vcollector.ui.widgets.devices_view import DevicesView
from vcollector.ui.widgets.history_view import HistoryView
from vcollector.ui.widgets.jobs_view import JobsView
from vcollector.ui.widgets.output_view import OutputView
from vcollector.ui.widgets.platforms_view import PlatformsView
from vcollector.ui.widgets.run_view import RunView
from vcollector.ui.widgets.sites_view import SitesView
from vcollector.ui.widgets.vault_view import VaultView

# Import vault resolver
try:
    from vcollector.vault.resolver import CredentialResolver

    VAULT_AVAILABLE = True
except ImportError:
    VAULT_AVAILABLE = False
    CredentialResolver = None

# Version info
__version__ = "0.3.0"
__author__ = "Scott Peterman"


# =============================================================================
# ABOUT DIALOG
# =============================================================================

class AboutDialog(QDialog):
    """Custom About dialog with proper styling and links."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About VelocityCollector")
        self.setMinimumSize(500, 580)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(40, 32, 40, 24)

        # Logo/Title area
        logo_label = QLabel("⚡")
        logo_label.setStyleSheet("font-size: 56px;")
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setFixedHeight(70)
        layout.addWidget(logo_label)

        title = QLabel("VelocityCollector")
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFixedHeight(36)
        layout.addWidget(title)

        # version = QLabel(f"Version {__version__}")
        # version.setStyleSheet("font-size: 13px; color: gray;")
        # version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # version.setFixedHeight(20)
        # layout.addWidget(version)

        # Spacer
        layout.addSpacing(12)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        layout.addWidget(line)

        # Spacer
        layout.addSpacing(12)

        # Description
        desc = QLabel(
            "Network data collection platform with encrypted credential vault,\n"
            "TextFSM validation, and concurrent execution.\n\n"
            "Part of the Velocity Suite for network automation."
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setStyleSheet("font-size: 12px; line-height: 140%;")
        desc.setFixedHeight(80)
        layout.addWidget(desc)

        # Spacer
        layout.addSpacing(8)

        # Features section
        features_label = QLabel("Key Features")
        features_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        features_label.setFixedHeight(24)
        layout.addWidget(features_label)

        # Features as a single formatted label for cleaner layout
        features_text = """  📡  Device inventory from NetBox/VelocityCMDB
  🔐  Encrypted credential vault (Fernet AES-128)
  📋  Job-based collection with scheduling
  ⚡  Concurrent SSH execution with workers
  🔍  TextFSM template validation & scoring
  📊  Collection history and output browsing"""

        features_label = QLabel(features_text)
        features_label.setStyleSheet("font-size: 12px; line-height: 160%;")
        features_label.setContentsMargins(8, 0, 0, 0)
        features_label.setFixedHeight(130)
        layout.addWidget(features_label)

        # Flexible spacer to push buttons down
        layout.addStretch(1)

        # Links row
        links_layout = QHBoxLayout()
        links_layout.setSpacing(12)

        links_layout.addStretch()

        github_btn = QPushButton("GitHub")
        github_btn.setProperty("secondary", True)
        github_btn.setFixedWidth(100)
        github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        github_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/scottpeterman/velocitycollector"))
        )
        links_layout.addWidget(github_btn)

        # docs_btn = QPushButton("Documentation")
        # docs_btn.setProperty("secondary", True)
        # docs_btn.setFixedWidth(120)
        # docs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # docs_btn.clicked.connect(
        #     lambda: QDesktopServices.openUrl(QUrl("https://github.com/scottpeterman/velocitycollector#readme"))
        # )
        # links_layout.addWidget(docs_btn)

        linkedin_btn = QPushButton("LinkedIn")
        linkedin_btn.setProperty("secondary", True)
        linkedin_btn.setFixedWidth(100)
        linkedin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        linkedin_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://www.linkedin.com/in/scott-peterman-networkeng"))
        )
        links_layout.addWidget(linkedin_btn)

        links_layout.addStretch()

        layout.addLayout(links_layout)

        # Spacer
        layout.addSpacing(16)

        # Copyright
        copyright_label = QLabel(f"© 2024-2025 {__author__}  •  GPLv3 License")
        copyright_label.setStyleSheet("font-size: 11px; color: gray;")
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copyright_label.setFixedHeight(18)
        layout.addWidget(copyright_label)

        # Spacer
        layout.addSpacing(8)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.setFixedHeight(32)
        close_btn.clicked.connect(self.accept)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)


class NavigationItem(QListWidgetItem):
    """Custom navigation list item."""

    def __init__(self, text: str, icon_char: str = ""):
        display = f"  {icon_char}  {text}" if icon_char else f"  {text}"
        super().__init__(display)
        self.setData(Qt.ItemDataRole.UserRole, text)


# =============================================================================
# MAIN WINDOW
# =============================================================================

class VelocityCollectorGUI(QMainWindow):
    """Main application window."""

    # View indices
    VIEW_DEVICES = 0
    VIEW_SITES = 1
    VIEW_PLATFORMS = 2
    VIEW_JOBS = 3
    VIEW_CREDENTIALS = 4
    VIEW_HISTORY = 5
    VIEW_OUTPUT = 6
    VIEW_RUN = 7
    VIEW_VAULT = 8

    def __init__(self):
        super().__init__()
        self.setWindowTitle("VelocityCollector")
        self.setGeometry(100, 100, 1400, 900)
        self.setMinimumSize(1000, 700)

        self.current_theme = "dark"

        # Initialize resolver
        self._resolver: Optional[CredentialResolver] = None
        if VAULT_AVAILABLE:
            try:
                self._resolver = CredentialResolver()
            except Exception as e:
                print(f"Warning: Could not initialize vault resolver: {e}")

        self.init_ui()
        self.apply_theme(self.current_theme)
        self.connect_signals()

    def init_ui(self):
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Menu bar
        self.create_menu_bar()

        # Toolbar
        self.create_toolbar()

        # Main content area
        content_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left navigation
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setFixedWidth(200)

        nav_items = [
            ("Devices", "📡"),
            ("Sites", "🏢"),
            ("Platforms", "🖥"),
            ("Jobs", "📋"),
            ("Credentials", "🔑"),
            ("History", "📊"),
            ("Output", "📁"),
        ]

        for text, icon in nav_items:
            item = NavigationItem(text, icon)
            self.nav_list.addItem(item)

        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self.on_nav_changed)

        content_splitter.addWidget(self.nav_list)

        # Stacked widget for views
        self.view_stack = QStackedWidget()

        # Create view instances
        self.devices_view = DevicesView()
        self.sites_view = SitesView()
        self.platforms_view = PlatformsView()
        self.jobs_view = JobsView()
        self.credentials_view = CredentialsView()
        self.history_view = HistoryView()
        self.output_view = OutputView()
        self.run_view = RunView()
        self.vault_view = VaultView()

        # Add views to stack
        self.view_stack.addWidget(self.devices_view)  # 0
        self.view_stack.addWidget(self.sites_view)  # 1
        self.view_stack.addWidget(self.platforms_view)  # 2
        self.view_stack.addWidget(self.jobs_view)  # 3
        self.view_stack.addWidget(self.credentials_view)  # 4
        self.view_stack.addWidget(self.history_view)  # 5
        self.view_stack.addWidget(self.output_view)  # 6
        self.view_stack.addWidget(self.run_view)  # 7
        self.view_stack.addWidget(self.vault_view)  # 8

        content_splitter.addWidget(self.view_stack)
        content_splitter.setSizes([200, 1200])

        main_layout.addWidget(content_splitter)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Wire up resolver to credentials view
        if self._resolver:
            self.credentials_view.set_resolver(self._resolver)

        self.update_status()

    def connect_signals(self):
        """Connect signals between views."""
        # Vault signals
        self.vault_view.vault_unlocked.connect(self.on_vault_unlocked)
        self.vault_view.vault_locked.connect(self.on_vault_locked)
        self.vault_view.vault_initialized.connect(self.on_vault_initialized)

        # Credentials "Go to Vault" button
        self.credentials_view.unlock_link.clicked.connect(
            lambda: self.show_view(self.VIEW_VAULT)
        )

    def on_vault_unlocked(self):
        """Handle vault unlock."""
        # Share resolver with credentials view
        if self.vault_view.resolver:
            self.credentials_view.set_resolver(self.vault_view.resolver)
        self.credentials_view.refresh_credentials()
        self.update_status()

    def on_vault_locked(self):
        """Handle vault lock."""
        self.credentials_view.refresh_credentials()
        self.update_status()

    def on_vault_initialized(self):
        """Handle vault initialization."""
        if self.vault_view.resolver:
            self.credentials_view.set_resolver(self.vault_view.resolver)
        self.update_status()

    def create_menu_bar(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        open_db_action = QAction("Open Assets DB...", self)
        open_db_action.setShortcut("Ctrl+O")
        open_db_action.triggered.connect(self.open_assets_db)
        file_menu.addAction(open_db_action)

        file_menu.addSeparator()

        settings_action = QAction("Settings...", self)
        settings_action.setShortcut("Ctrl+,")
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Edit menu
        edit_menu = menubar.addMenu("&Edit")

        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.refresh_current_view)
        edit_menu.addAction(refresh_action)

        # Tools menu
        tools_menu = menubar.addMenu("&Tools")

        unlock_vault_action = QAction("Unlock Vault...", self)
        unlock_vault_action.triggered.connect(lambda: self.show_view(self.VIEW_VAULT))
        tools_menu.addAction(unlock_vault_action)

        tools_menu.addSeparator()

        run_job_action = QAction("Run Job...", self)
        run_job_action.setShortcut("Ctrl+R")
        run_job_action.triggered.connect(lambda: self.show_view(self.VIEW_RUN))
        tools_menu.addAction(run_job_action)

        tools_menu.addSeparator()

        tfsm_tester_action = QAction("TextFSM Tester...", self)
        tfsm_tester_action.triggered.connect(self.open_tfsm_tester)
        tools_menu.addAction(tfsm_tester_action)

        validate_jobs_action = QAction("Validate Jobs...", self)
        tools_menu.addAction(validate_jobs_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        docs_action = QAction("Documentation", self)
        docs_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/scottpeterman/velocitycollector#readme"))
        )
        help_menu.addAction(docs_action)

        help_menu.addSeparator()

        about_action = QAction("About VelocityCollector", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def create_toolbar(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(toolbar)

        # Quick nav buttons
        devices_btn = QToolButton()
        devices_btn.setText("Devices")
        devices_btn.clicked.connect(lambda: self.show_view(self.VIEW_DEVICES))
        toolbar.addWidget(devices_btn)

        jobs_btn = QToolButton()
        jobs_btn.setText("Jobs")
        jobs_btn.clicked.connect(lambda: self.show_view(self.VIEW_JOBS))
        toolbar.addWidget(jobs_btn)

        toolbar.addSeparator()

        run_btn = QToolButton()
        run_btn.setText("Run")
        run_btn.clicked.connect(lambda: self.show_view(self.VIEW_RUN))
        toolbar.addWidget(run_btn)

        toolbar.addSeparator()

        vault_btn = QToolButton()
        vault_btn.setText("Vault")
        vault_btn.clicked.connect(lambda: self.show_view(self.VIEW_VAULT))
        toolbar.addWidget(vault_btn)

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # Theme selector
        theme_label = QLabel("Theme: ")
        toolbar.addWidget(theme_label)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark", "Cyber"])
        self.theme_combo.setCurrentText(self.current_theme.capitalize())
        self.theme_combo.currentTextChanged.connect(lambda t: self.apply_theme(t.lower()))
        self.theme_combo.setFixedWidth(100)
        toolbar.addWidget(self.theme_combo)

    def on_nav_changed(self, index: int):
        self.view_stack.setCurrentIndex(index)

    def show_view(self, index: int):
        """Show a specific view by index."""
        if index < self.nav_list.count():
            self.nav_list.setCurrentRow(index)
        self.view_stack.setCurrentIndex(index)

    def apply_theme(self, theme_name: str):
        self.current_theme = theme_name
        self.setStyleSheet(get_stylesheet(theme_name))

    def update_status(self):
        """Update status bar."""
        # Vault status
        if self._resolver is None:
            vault_status = "Vault: Not Available"
        elif not self._resolver.is_initialized():
            vault_status = "Vault: Not Initialized"
        elif self._resolver.is_unlocked:
            vault_status = "Vault: Unlocked ✓"
        else:
            vault_status = "Vault: Locked 🔒"

        # Check for actual resolver from vault view (it may have been initialized there)
        if hasattr(self, 'vault_view') and self.vault_view.resolver:
            resolver = self.vault_view.resolver
            if resolver.is_unlocked:
                vault_status = "Vault: Unlocked ✓"

        self.status_bar.showMessage(
            f"VelocityCollector │ Ready │ {vault_status}"
        )

    def refresh_current_view(self):
        """Refresh the currently active view."""
        current = self.view_stack.currentWidget()
        if hasattr(current, 'refresh'):
            current.refresh()
        elif hasattr(current, 'refresh_credentials'):
            current.refresh_credentials()

    def open_assets_db(self):
        """Open a different assets database."""
        from PyQt6.QtWidgets import QFileDialog

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Assets Database",
            "",
            "SQLite Database (*.db);;All Files (*)"
        )

        if file_path:
            # TODO: Update config and reload devices
            QMessageBox.information(
                self, "Info",
                f"Selected: {file_path}\n\nDatabase switching not yet implemented."
            )

    def open_tfsm_tester(self):
        """Open the TextFSM tester tool."""
        try:
            from vcollector.core.tfsm_fire_tester import TextFSMTester
            self.tfsm_window = TextFSMTester()
            self.tfsm_window.show()
        except ImportError:
            QMessageBox.warning(
                self, "Not Available",
                "TextFSM Tester is not available.\n\n"
                "Make sure tfsm_fire_tester.py is in vcollector/core/"
            )

    def show_about(self):
        """Show about dialog."""
        dialog = AboutDialog(self)
        dialog.exec()

    def closeEvent(self, event):
        """Handle window close."""
        # Lock vault on exit
        if hasattr(self, 'vault_view') and self.vault_view.resolver:
            self.vault_view.resolver.lock_vault()

        event.accept()


# =============================================================================
# MAIN
# =============================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Set application-wide font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = VelocityCollectorGUI()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()