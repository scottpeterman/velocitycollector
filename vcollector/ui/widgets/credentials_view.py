#!/usr/bin/env python3
"""
Credentials View - Credential management UI

Provides:
- List stored credentials
- Add new credentials (password or SSH key)
- Edit existing credentials
- Delete credentials
- Set default credential
"""

from typing import Optional, TYPE_CHECKING
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QGroupBox, QMessageBox, QFileDialog,
    QDialog, QDialogButtonBox, QFormLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QCheckBox,
    QTextEdit, QTabWidget, QComboBox, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QAction

if TYPE_CHECKING:
    from vcollector.vault.resolver import CredentialResolver
    from vcollector.vault.models import CredentialInfo


class AddCredentialDialog(QDialog):
    """Dialog for adding/editing a credential."""

    def __init__(self, parent=None, edit_mode: bool = False,
                 credential_name: str = "", username: str = ""):
        super().__init__(parent)
        self.edit_mode = edit_mode
        self.setWindowTitle("Edit Credential" if edit_mode else "Add Credential")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.init_ui(credential_name, username)

    def init_ui(self, credential_name: str, username: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Basic info
        basic_group = QGroupBox("Credential Info")
        basic_layout = QFormLayout(basic_group)
        basic_layout.setSpacing(12)

        self.name_input = QLineEdit(credential_name)
        self.name_input.setPlaceholderText("e.g., lab, production, backup")
        self.name_input.setEnabled(not self.edit_mode)  # Can't change name when editing
        basic_layout.addRow("Name:", self.name_input)

        self.username_input = QLineEdit(username)
        self.username_input.setPlaceholderText("SSH username")
        basic_layout.addRow("Username:", self.username_input)

        self.default_check = QCheckBox("Set as default credential")
        basic_layout.addRow("", self.default_check)

        layout.addWidget(basic_group)

        # Authentication tabs
        auth_tabs = QTabWidget()

        # Password tab
        password_widget = QWidget()
        password_layout = QFormLayout(password_widget)
        password_layout.setSpacing(12)
        password_layout.setContentsMargins(16, 16, 16, 16)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Enter password (leave blank to keep existing)")
        password_layout.addRow("Password:", self.password_input)

        self.confirm_password = QLineEdit()
        self.confirm_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password.setPlaceholderText("Confirm password")
        password_layout.addRow("Confirm:", self.confirm_password)

        auth_tabs.addTab(password_widget, "Password")

        # SSH Key tab
        key_widget = QWidget()
        key_layout = QVBoxLayout(key_widget)
        key_layout.setSpacing(12)
        key_layout.setContentsMargins(16, 16, 16, 16)

        key_file_layout = QHBoxLayout()
        self.key_path_input = QLineEdit()
        self.key_path_input.setPlaceholderText("Path to SSH private key file")
        key_file_layout.addWidget(self.key_path_input)

        browse_btn = QPushButton("Browse...")
        browse_btn.setProperty("secondary", True)
        browse_btn.clicked.connect(self.browse_key_file)
        key_file_layout.addWidget(browse_btn)
        key_layout.addLayout(key_file_layout)

        key_layout.addWidget(QLabel("Or paste key content directly:"))

        self.key_content_input = QTextEdit()
        self.key_content_input.setPlaceholderText(
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "...\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        self.key_content_input.setMaximumHeight(150)
        key_layout.addWidget(self.key_content_input)

        passphrase_layout = QFormLayout()
        self.key_passphrase_input = QLineEdit()
        self.key_passphrase_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_passphrase_input.setPlaceholderText("Key passphrase (if encrypted)")
        passphrase_layout.addRow("Passphrase:", self.key_passphrase_input)
        key_layout.addLayout(passphrase_layout)

        auth_tabs.addTab(key_widget, "SSH Key")

        layout.addWidget(auth_tabs)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.validate_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def browse_key_file(self):
        """Browse for SSH key file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH Private Key",
            str(Path.home() / ".ssh"),
            "All Files (*)"
        )
        if file_path:
            self.key_path_input.setText(file_path)
            # Try to load the key content
            try:
                with open(file_path, 'r') as f:
                    self.key_content_input.setPlainText(f.read())
            except Exception as e:
                QMessageBox.warning(
                    self, "Warning",
                    f"Could not read key file: {e}"
                )

    def validate_and_accept(self):
        """Validate inputs and accept dialog."""
        name = self.name_input.text().strip()
        username = self.username_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Error", "Credential name is required")
            self.name_input.setFocus()
            return

        if not username:
            QMessageBox.warning(self, "Error", "Username is required")
            self.username_input.setFocus()
            return

        # Validate name format (alphanumeric, dash, underscore)
        if not all(c.isalnum() or c in '-_' for c in name):
            QMessageBox.warning(
                self, "Error",
                "Name can only contain letters, numbers, dashes, and underscores"
            )
            self.name_input.setFocus()
            return

        # Check password confirmation
        password = self.password_input.text()
        if password and password != self.confirm_password.text():
            QMessageBox.warning(self, "Error", "Passwords do not match")
            self.password_input.setFocus()
            return

        # Must have either password or key
        key_content = self.key_content_input.toPlainText().strip()
        if not password and not key_content and not self.edit_mode:
            QMessageBox.warning(
                self, "Error",
                "Please provide either a password or SSH key"
            )
            return

        self.accept()

    def get_credential_data(self) -> dict:
        """Get the credential data from the form."""
        key_content = self.key_content_input.toPlainText().strip()

        # If key path is set but content is empty, try to load it
        key_path = self.key_path_input.text().strip()
        if key_path and not key_content:
            try:
                with open(key_path, 'r') as f:
                    key_content = f.read()
            except Exception:
                pass

        return {
            'name': self.name_input.text().strip(),
            'username': self.username_input.text().strip(),
            'password': self.password_input.text() or None,
            'ssh_key': key_content or None,
            'ssh_key_passphrase': self.key_passphrase_input.text() or None,
            'is_default': self.default_check.isChecked(),
        }


class CredentialsView(QWidget):
    """Credential management view."""

    # Signals
    credentials_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resolver: Optional['CredentialResolver'] = None
        self.init_ui()

    def set_resolver(self, resolver: 'CredentialResolver'):
        """Set the credential resolver instance."""
        self._resolver = resolver
        self.refresh_credentials()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Credential Vault")
        title.setProperty("heading", True)
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.add_btn = QPushButton("+ Add Credential")
        self.add_btn.clicked.connect(self.add_credential)
        header_layout.addWidget(self.add_btn)

        layout.addLayout(header_layout)

        # Vault status indicator
        self.status_frame = QFrame()
        self.status_frame.setProperty("card", True)
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(16, 12, 16, 12)

        self.status_icon = QLabel("🔒")
        self.status_icon.setStyleSheet("font-size: 20px;")
        status_layout.addWidget(self.status_icon)

        self.status_label = QLabel("Vault is locked. Unlock to manage credentials.")
        self.status_label.setProperty("subheading", True)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        self.unlock_link = QPushButton("Go to Vault")
        self.unlock_link.setProperty("secondary", True)
        status_layout.addWidget(self.unlock_link)

        layout.addWidget(self.status_frame)

        # Credentials table
        self.creds_table = QTableWidget()
        self.creds_table.setColumnCount(6)
        self.creds_table.setHorizontalHeaderLabels([
            "Name", "Username", "Type", "Default", "Created", "Actions"
        ])
        self.creds_table.setAlternatingRowColors(True)
        self.creds_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.creds_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.creds_table.horizontalHeader().setStretchLastSection(True)
        self.creds_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.creds_table.verticalHeader().setVisible(False)
        self.creds_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.creds_table.customContextMenuRequested.connect(self.show_context_menu)
        self.creds_table.doubleClicked.connect(self.edit_selected)

        layout.addWidget(self.creds_table)

        # Action buttons
        btn_layout = QHBoxLayout()

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setProperty("secondary", True)
        self.edit_btn.clicked.connect(self.edit_selected)
        btn_layout.addWidget(self.edit_btn)

        self.set_default_btn = QPushButton("Set as Default")
        self.set_default_btn.setProperty("secondary", True)
        self.set_default_btn.clicked.connect(self.set_default)
        btn_layout.addWidget(self.set_default_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(self.delete_selected)
        btn_layout.addWidget(self.delete_btn)

        btn_layout.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setProperty("secondary", True)
        self.refresh_btn.clicked.connect(self.refresh_credentials)
        btn_layout.addWidget(self.refresh_btn)

        layout.addLayout(btn_layout)

        # Usage hint
        hint_group = QGroupBox("Usage")
        hint_layout = QVBoxLayout(hint_group)

        hint_text = QLabel(
            "Credentials are used by collection jobs to authenticate to network devices. "
            "Each job can specify which credential set to use, or fall back to the default.\n\n"
            "Supported authentication methods:\n"
            "• Password authentication\n"
            "• SSH private key (with optional passphrase)\n"
            "• Combined password + key for privilege escalation"
        )
        hint_text.setProperty("subheading", True)
        hint_text.setWordWrap(True)
        hint_layout.addWidget(hint_text)

        layout.addWidget(hint_group)

        # Initial state
        self.update_ui_state()

    def update_ui_state(self):
        """Update UI based on vault state."""
        unlocked = self._resolver is not None and self._resolver.is_unlocked

        # Update status bar
        if self._resolver is None:
            self.status_icon.setText("⚠️")
            self.status_label.setText("Vault not initialized")
            self.status_frame.setVisible(True)
        elif unlocked:
            self.status_frame.setVisible(False)
        else:
            self.status_icon.setText("🔒")
            self.status_label.setText("Vault is locked. Unlock to manage credentials.")
            self.status_frame.setVisible(True)

        # Enable/disable controls
        self.add_btn.setEnabled(unlocked)
        self.creds_table.setEnabled(unlocked)
        self.edit_btn.setEnabled(unlocked)
        self.set_default_btn.setEnabled(unlocked)
        self.delete_btn.setEnabled(unlocked)

    def refresh_credentials(self):
        """Refresh the credentials table."""
        self.update_ui_state()

        if not self._resolver or not self._resolver.is_unlocked:
            self.creds_table.setRowCount(0)
            return

        try:
            credentials = self._resolver.list_credentials()
            self.creds_table.setRowCount(len(credentials))

            for row, cred in enumerate(credentials):
                # Name
                name_item = QTableWidgetItem(cred.name)
                name_item.setData(Qt.ItemDataRole.UserRole, cred.name)
                self.creds_table.setItem(row, 0, name_item)

                # Username
                self.creds_table.setItem(row, 1, QTableWidgetItem(cred.username))

                # Type
                auth_type = []
                if cred.has_password:
                    auth_type.append("Password")
                if cred.has_ssh_key:
                    auth_type.append("SSH Key")
                type_str = " + ".join(auth_type) if auth_type else "None"
                self.creds_table.setItem(row, 2, QTableWidgetItem(type_str))

                # Default
                default_item = QTableWidgetItem("✓" if cred.is_default else "")
                if cred.is_default:
                    default_item.setForeground(QColor("#27AE60"))
                default_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.creds_table.setItem(row, 3, default_item)

                # Created
                created = cred.created_at or ""
                if created:
                    # Format nicely
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                        created = dt.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass
                self.creds_table.setItem(row, 4, QTableWidgetItem(created))

                # Actions column (empty, handled by buttons/context menu)
                self.creds_table.setItem(row, 5, QTableWidgetItem(""))

        except Exception as e:
            QMessageBox.warning(
                self, "Error",
                f"Failed to load credentials: {e}"
            )

    def get_selected_credential_name(self) -> Optional[str]:
        """Get the name of the selected credential."""
        selected = self.creds_table.selectedItems()
        if not selected:
            return None

        row = selected[0].row()
        name_item = self.creds_table.item(row, 0)
        return name_item.data(Qt.ItemDataRole.UserRole) if name_item else None

    def add_credential(self):
        """Add a new credential."""
        if not self._resolver or not self._resolver.is_unlocked:
            QMessageBox.warning(
                self, "Error",
                "Vault must be unlocked to add credentials"
            )
            return

        dialog = AddCredentialDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_credential_data()
            try:
                self._resolver.add_credential(
                    name=data['name'],
                    username=data['username'],
                    password=data['password'],
                    ssh_key=data['ssh_key'],
                    ssh_key_passphrase=data['ssh_key_passphrase'],
                    is_default=data['is_default'],
                )
                self.refresh_credentials()
                self.credentials_changed.emit()
                QMessageBox.information(
                    self, "Success",
                    f"Credential '{data['name']}' added successfully"
                )
            except Exception as e:
                QMessageBox.critical(
                    self, "Error",
                    f"Failed to add credential: {e}"
                )

    def edit_selected(self):
        """Edit the selected credential."""
        name = self.get_selected_credential_name()
        if not name:
            QMessageBox.warning(self, "Warning", "Please select a credential to edit")
            return

        if not self._resolver or not self._resolver.is_unlocked:
            return

        # Get current data
        try:
            creds = self._resolver.list_credentials()
            current = next((c for c in creds if c.name == name), None)
            if not current:
                return

            dialog = AddCredentialDialog(
                self,
                edit_mode=True,
                credential_name=current.name,
                username=current.username
            )
            dialog.default_check.setChecked(current.is_default)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                data = dialog.get_credential_data()

                # For editing, we need to remove and re-add
                # (A more sophisticated implementation would have an update method)
                self._resolver.remove_credential(name)
                self._resolver.add_credential(
                    name=data['name'],
                    username=data['username'],
                    password=data['password'],
                    ssh_key=data['ssh_key'],
                    ssh_key_passphrase=data['ssh_key_passphrase'],
                    is_default=data['is_default'],
                )

                self.refresh_credentials()
                self.credentials_changed.emit()
                QMessageBox.information(
                    self, "Success",
                    f"Credential '{name}' updated successfully"
                )

        except Exception as e:
            QMessageBox.critical(
                self, "Error",
                f"Failed to edit credential: {e}"
            )

    def set_default(self):
        """Set the selected credential as default."""
        name = self.get_selected_credential_name()
        if not name:
            QMessageBox.warning(self, "Warning", "Please select a credential")
            return

        if not self._resolver or not self._resolver.is_unlocked:
            return

        try:
            if self._resolver.set_default(name):
                self.refresh_credentials()
                self.credentials_changed.emit()
            else:
                QMessageBox.warning(
                    self, "Error",
                    f"Could not set '{name}' as default"
                )
        except Exception as e:
            QMessageBox.critical(
                self, "Error",
                f"Failed to set default: {e}"
            )

    def delete_selected(self):
        """Delete the selected credential."""
        name = self.get_selected_credential_name()
        if not name:
            QMessageBox.warning(self, "Warning", "Please select a credential to delete")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete credential '{name}'?\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                if self._resolver.remove_credential(name):
                    self.refresh_credentials()
                    self.credentials_changed.emit()
                else:
                    QMessageBox.warning(
                        self, "Error",
                        f"Could not delete '{name}'"
                    )
            except Exception as e:
                QMessageBox.critical(
                    self, "Error",
                    f"Failed to delete credential: {e}"
                )

    def show_context_menu(self, position):
        """Show context menu for credential table."""
        name = self.get_selected_credential_name()
        if not name:
            return

        menu = QMenu(self)

        edit_action = menu.addAction("Edit")
        edit_action.triggered.connect(self.edit_selected)

        default_action = menu.addAction("Set as Default")
        default_action.triggered.connect(self.set_default)

        menu.addSeparator()

        delete_action = menu.addAction("Delete")
        delete_action.triggered.connect(self.delete_selected)

        menu.exec(self.creds_table.viewport().mapToGlobal(position))