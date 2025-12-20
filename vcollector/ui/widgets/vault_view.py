#!/usr/bin/env python3
"""
Vault View - Vault unlock and management UI

Provides:
- Vault initialization
- Unlock/lock functionality
- Password management
- Import/export operations
"""

from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QGroupBox, QMessageBox, QFileDialog,
    QDialog, QDialogButtonBox, QFormLayout, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

from vcollector.vault.resolver import CredentialResolver


class PasswordDialog(QDialog):
    """Dialog for entering or setting passwords."""

    def __init__(self, parent=None, title: str = "Password",
                 confirm: bool = False, message: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.confirm = confirm
        self.init_ui(message)

    def init_ui(self, message: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        if message:
            msg_label = QLabel(message)
            msg_label.setWordWrap(True)
            layout.addWidget(msg_label)

        form = QFormLayout()
        form.setSpacing(12)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Enter password")
        form.addRow("Password:", self.password_input)

        if self.confirm:
            self.confirm_input = QLineEdit()
            self.confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.confirm_input.setPlaceholderText("Confirm password")
            form.addRow("Confirm:", self.confirm_input)

        layout.addLayout(form)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.validate_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.password_input.setFocus()

    def validate_and_accept(self):
        password = self.password_input.text()

        if not password:
            QMessageBox.warning(self, "Error", "Password cannot be empty")
            return

        if self.confirm:
            if password != self.confirm_input.text():
                QMessageBox.warning(self, "Error", "Passwords do not match")
                return

            if len(password) < 8:
                QMessageBox.warning(
                    self, "Error",
                    "Password must be at least 8 characters"
                )
                return

        self.accept()

    def get_password(self) -> str:
        return self.password_input.text()


class VaultStatusCard(QFrame):
    """Card showing current vault status."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self._is_unlocked = False
        self._is_initialized = False
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        # Icon
        self.icon_label = QLabel("🔒")
        self.icon_label.setStyleSheet("font-size: 32px;")
        layout.addWidget(self.icon_label)

        # Status info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        self.status_title = QLabel("Vault Locked")
        self.status_title.setStyleSheet("font-weight: 600; font-size: 16px;")
        info_layout.addWidget(self.status_title)

        self.status_detail = QLabel("Enter master password to unlock")
        self.status_detail.setProperty("subheading", True)
        info_layout.addWidget(self.status_detail)

        layout.addLayout(info_layout)
        layout.addStretch()

        # Action button
        self.action_btn = QPushButton("Unlock")
        self.action_btn.setProperty("secondary", True)
        layout.addWidget(self.action_btn)

    def set_status(self, initialized: bool, unlocked: bool):
        """Update the status display."""
        self._is_initialized = initialized
        self._is_unlocked = unlocked

        if not initialized:
            self.icon_label.setText("⚠️")
            self.status_title.setText("Vault Not Initialized")
            self.status_detail.setText("Create a new vault to store credentials securely")
            self.action_btn.setText("Initialize Vault")
        elif unlocked:
            self.icon_label.setText("🔓")
            self.status_title.setText("Vault Unlocked")
            self.status_detail.setText("Credentials are accessible")
            self.action_btn.setText("Lock Vault")
        else:
            self.icon_label.setText("🔒")
            self.status_title.setText("Vault Locked")
            self.status_detail.setText("Enter master password to unlock")
            self.action_btn.setText("Unlock")


class VaultView(QWidget):
    """Vault management view with unlock, init, and password management."""

    # Signals
    vault_unlocked = pyqtSignal()
    vault_locked = pyqtSignal()
    vault_initialized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resolver: Optional[CredentialResolver] = None
        self.init_ui()
        self.init_resolver()

    def init_resolver(self):
        """Initialize the credential resolver."""
        try:
            self._resolver = CredentialResolver()
            self.refresh_status()
        except Exception as e:
            QMessageBox.warning(
                self, "Warning",
                f"Could not initialize vault resolver: {e}"
            )

    def init_ui(self):
        # Main layout for this widget - just holds the scroll area
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Content widget that goes inside scroll area
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        header = QLabel("Vault Management")
        header.setProperty("heading", True)
        layout.addWidget(header)

        description = QLabel(
            "The credential vault securely stores SSH credentials using "
            "Fernet encryption. Your master password is never stored - "
            "it's used to derive the encryption key."
        )
        description.setProperty("subheading", True)
        description.setWordWrap(True)
        layout.addWidget(description)

        # Status card
        self.status_card = VaultStatusCard()
        self.status_card.action_btn.clicked.connect(self.on_status_action)
        layout.addWidget(self.status_card)

        # Quick unlock section (when vault is initialized but locked)
        self.unlock_group = QGroupBox("Quick Unlock")
        unlock_layout = QVBoxLayout(self.unlock_group)

        password_layout = QHBoxLayout()
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter master password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.returnPressed.connect(self.unlock_vault)
        password_layout.addWidget(self.password_input)

        self.unlock_btn = QPushButton("Unlock")
        self.unlock_btn.clicked.connect(self.unlock_vault)
        password_layout.addWidget(self.unlock_btn)

        unlock_layout.addLayout(password_layout)
        layout.addWidget(self.unlock_group)

        # Vault actions
        self.actions_group = QGroupBox("Vault Actions")
        actions_layout = QVBoxLayout(self.actions_group)

        # Row 1: Password management
        row1 = QHBoxLayout()

        self.change_pass_btn = QPushButton("Change Master Password")
        self.change_pass_btn.setProperty("secondary", True)
        self.change_pass_btn.clicked.connect(self.change_password)
        row1.addWidget(self.change_pass_btn)

        row1.addStretch()
        actions_layout.addLayout(row1)

        # Row 2: Import/Export
        row2 = QHBoxLayout()

        self.export_btn = QPushButton("Export Credentials (Encrypted)")
        self.export_btn.setProperty("secondary", True)
        self.export_btn.clicked.connect(self.export_vault)
        self.export_btn.setToolTip(
            "Export all credentials to an encrypted backup file"
        )
        row2.addWidget(self.export_btn)

        self.import_btn = QPushButton("Import Backup")
        self.import_btn.setProperty("secondary", True)
        self.import_btn.clicked.connect(self.import_vault)
        row2.addWidget(self.import_btn)

        row2.addStretch()
        actions_layout.addLayout(row2)

        # Row 3: Dangerous actions
        row3 = QHBoxLayout()

        self.reset_btn = QPushButton("Reset Vault")
        self.reset_btn.setProperty("danger", True)
        self.reset_btn.clicked.connect(self.reset_vault)
        self.reset_btn.setToolTip(
            "Delete the vault and all stored credentials. This cannot be undone!"
        )
        row3.addWidget(self.reset_btn)

        row3.addStretch()
        actions_layout.addLayout(row3)

        layout.addWidget(self.actions_group)

        # Environment variable hint
        env_hint = QGroupBox("Automation Hint")
        env_layout = QVBoxLayout(env_hint)

        hint_text = QLabel(
            "For automated/scheduled jobs, set the VCOLLECTOR_VAULT_PASS "
            "environment variable instead of entering the password interactively."
        )
        hint_text.setProperty("subheading", True)
        hint_text.setWordWrap(True)
        env_layout.addWidget(hint_text)

        example = QLabel("export VCOLLECTOR_VAULT_PASS='your_password'")
        example.setStyleSheet(
            "font-family: monospace; padding: 8px; "
            "background-color: rgba(0,0,0,0.1); border-radius: 4px;"
        )
        env_layout.addWidget(example)

        layout.addWidget(env_hint)

        layout.addStretch()

        # Set the content widget into scroll area
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

    def refresh_status(self):
        """Refresh the vault status display."""
        if not self._resolver:
            return

        initialized = self._resolver.is_initialized()
        unlocked = self._resolver.is_unlocked

        self.status_card.set_status(initialized, unlocked)

        # Show/hide sections based on state
        self.unlock_group.setVisible(initialized and not unlocked)
        self.actions_group.setVisible(initialized)

        # Enable/disable buttons
        self.change_pass_btn.setEnabled(unlocked)
        self.export_btn.setEnabled(unlocked)
        self.import_btn.setEnabled(unlocked)

        # Clear password field
        self.password_input.clear()

    def on_status_action(self):
        """Handle the main status card action button."""
        if not self._resolver:
            return

        if not self._resolver.is_initialized():
            self.init_vault()
        elif self._resolver.is_unlocked:
            self.lock_vault()
        else:
            self.password_input.setFocus()

    def init_vault(self):
        """Initialize a new vault."""
        dialog = PasswordDialog(
            self,
            title="Initialize Vault",
            confirm=True,
            message="Create a master password for your credential vault. "
                    "This password will be used to encrypt all stored credentials. "
                    "Choose a strong password and keep it safe - there is no recovery option!"
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            password = dialog.get_password()
            try:
                self._resolver.init_vault(password)
                QMessageBox.information(
                    self, "Success",
                    "Vault initialized successfully! Your vault is now unlocked."
                )
                self.vault_initialized.emit()
                self.vault_unlocked.emit()
                self.refresh_status()
            except Exception as e:
                QMessageBox.critical(
                    self, "Error",
                    f"Failed to initialize vault: {e}"
                )

    def unlock_vault(self):
        """Unlock the vault with the entered password."""
        if not self._resolver:
            return

        password = self.password_input.text()
        if not password:
            QMessageBox.warning(self, "Error", "Please enter a password")
            return

        try:
            if self._resolver.unlock_vault(password):
                self.vault_unlocked.emit()
                self.refresh_status()
            else:
                QMessageBox.warning(
                    self, "Error",
                    "Incorrect password. Please try again."
                )
                self.password_input.selectAll()
                self.password_input.setFocus()
        except Exception as e:
            QMessageBox.critical(
                self, "Error",
                f"Failed to unlock vault: {e}"
            )

    def lock_vault(self):
        """Lock the vault."""
        if not self._resolver:
            return

        self._resolver.lock_vault()
        self.vault_locked.emit()
        self.refresh_status()

    def change_password(self):
        """Change the master password."""
        if not self._resolver or not self._resolver.is_unlocked:
            QMessageBox.warning(
                self, "Error",
                "Vault must be unlocked to change password"
            )
            return

        # Verify current password
        current_dialog = PasswordDialog(
            self,
            title="Verify Current Password",
            message="Enter your current master password to continue."
        )

        if current_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Verify it's correct by trying to unlock (already unlocked, but validates)
        # In a real implementation, you'd verify against stored hash

        # Get new password
        new_dialog = PasswordDialog(
            self,
            title="Set New Password",
            confirm=True,
            message="Enter your new master password."
        )

        if new_dialog.exec() == QDialog.DialogCode.Accepted:
            # TODO: Implement password change in resolver
            # This would require re-encrypting all credentials
            QMessageBox.information(
                self, "Not Implemented",
                "Password change functionality is not yet implemented. "
                "This would require re-encrypting all stored credentials."
            )

    def export_vault(self):
        """Export credentials to encrypted backup."""
        if not self._resolver or not self._resolver.is_unlocked:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Vault Backup",
            "vcollector_vault_backup.json",
            "JSON Files (*.json)"
        )

        if file_path:
            # TODO: Implement export in resolver
            QMessageBox.information(
                self, "Not Implemented",
                "Export functionality is not yet implemented."
            )

    def import_vault(self):
        """Import credentials from backup."""
        if not self._resolver or not self._resolver.is_unlocked:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Vault Backup",
            "",
            "JSON Files (*.json)"
        )

        if file_path:
            # TODO: Implement import in resolver
            QMessageBox.information(
                self, "Not Implemented",
                "Import functionality is not yet implemented."
            )

    def reset_vault(self):
        """Reset the vault (delete all credentials)."""
        reply = QMessageBox.warning(
            self,
            "Confirm Reset",
            "This will DELETE the vault and ALL stored credentials.\n\n"
            "This action cannot be undone!\n\n"
            "Are you sure you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Double confirm
            reply2 = QMessageBox.critical(
                self,
                "Final Confirmation",
                "LAST CHANCE!\n\n"
                "Type 'DELETE' to confirm vault reset:",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
            )

            # TODO: Actually implement vault deletion
            # This would delete the credentials table and vault_metadata

    @property
    def resolver(self) -> Optional[CredentialResolver]:
        """Get the credential resolver instance."""
        return self._resolver

    @property
    def is_unlocked(self) -> bool:
        """Check if vault is unlocked."""
        return self._resolver is not None and self._resolver.is_unlocked