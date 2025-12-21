"""
VelocityCollector Batch Dialogs

Dialog for creating and editing batch job definitions.
Uses a dual-list picker for selecting and ordering jobs.

Path: vcollector/ui/widgets/batch_dialogs.py
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QGroupBox,
    QMessageBox, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal

from typing import Optional, List

from vcollector.core.batch_loader import BatchLoader, BatchDefinition
from vcollector.dcim.jobs_repo import JobsRepository, Job


class BatchEditDialog(QDialog):
    """
    Dialog for creating/editing batch job definitions.

    Features:
    - Dual-list picker (available jobs ↔ selected jobs)
    - Filter for available jobs
    - Reorder selected jobs with up/down buttons
    - Double-click or arrow buttons to move jobs
    """

    batch_saved = pyqtSignal(str)  # filename
    batch_deleted = pyqtSignal(str)  # filename

    def __init__(
        self,
        batch_loader: BatchLoader,
        jobs_repo: JobsRepository,
        batch: Optional[BatchDefinition] = None,
        parent=None
    ):
        super().__init__(parent)
        self.batch_loader = batch_loader
        self.jobs_repo = jobs_repo
        self.batch = batch
        self.is_edit_mode = batch is not None

        # Load all available jobs
        self._all_jobs = {j.slug: j for j in self.jobs_repo.get_jobs()}

        self.setWindowTitle("Edit Batch" if self.is_edit_mode else "New Batch")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)

        self.init_ui()

        if self.is_edit_mode:
            self.populate_from_batch()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # === Name Field ===
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Batch Name:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., Cisco Collection")
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)

        # === Dual List Picker ===
        picker_layout = QHBoxLayout()
        picker_layout.setSpacing(8)

        # Available jobs (left side)
        available_group = QGroupBox("Available Jobs")
        available_layout = QVBoxLayout(available_group)

        # Filter
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Type to filter...")
        self.filter_input.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.filter_input)
        available_layout.addLayout(filter_layout)

        self.available_list = QListWidget()
        self.available_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.available_list.setSortingEnabled(True)
        self.available_list.doubleClicked.connect(self._on_add_job)
        available_layout.addWidget(self.available_list)

        self.available_count_label = QLabel("0 jobs")
        self.available_count_label.setStyleSheet("color: #888;")
        available_layout.addWidget(self.available_count_label)

        picker_layout.addWidget(available_group)

        # Arrow buttons (center)
        arrows_layout = QVBoxLayout()
        arrows_layout.addStretch()

        self.add_btn = QPushButton(">")
        self.add_btn.setFixedWidth(50)
        self.add_btn.setProperty("flat", True)
        self.add_btn.setToolTip("Add selected jobs to batch")
        self.add_btn.clicked.connect(self._on_add_job)
        arrows_layout.addWidget(self.add_btn)

        self.add_all_btn = QPushButton(">>")
        self.add_all_btn.setFixedWidth(50)
        self.add_all_btn.setProperty("flat", True)
        self.add_all_btn.setToolTip("Add all visible jobs")
        self.add_all_btn.clicked.connect(self._on_add_all)
        arrows_layout.addWidget(self.add_all_btn)

        arrows_layout.addSpacing(16)

        self.remove_btn = QPushButton("<")
        self.remove_btn.setFixedWidth(50)
        self.remove_btn.setProperty("flat", True)
        self.remove_btn.setToolTip("Remove selected jobs from batch")
        self.remove_btn.clicked.connect(self._on_remove_job)
        arrows_layout.addWidget(self.remove_btn)

        self.remove_all_btn = QPushButton("<<")
        self.remove_all_btn.setFixedWidth(50)
        self.remove_all_btn.setProperty("flat", True)
        self.remove_all_btn.setToolTip("Remove all jobs")
        self.remove_all_btn.clicked.connect(self._on_remove_all)
        arrows_layout.addWidget(self.remove_all_btn)

        arrows_layout.addStretch()
        picker_layout.addLayout(arrows_layout)

        # Selected jobs (right side)
        selected_group = QGroupBox("Jobs in Batch (execution order)")
        selected_layout = QVBoxLayout(selected_group)

        self.selected_list = QListWidget()
        self.selected_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.selected_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.selected_list.doubleClicked.connect(self._on_remove_job)
        selected_layout.addWidget(self.selected_list)

        # Up/Down buttons
        order_layout = QHBoxLayout()

        self.up_btn = QPushButton("▲ Up")
        self.up_btn.setProperty("flat", True)
        self.up_btn.clicked.connect(self._on_move_up)
        order_layout.addWidget(self.up_btn)

        self.down_btn = QPushButton("▼ Down")
        self.down_btn.setProperty("flat", True)
        self.down_btn.clicked.connect(self._on_move_down)
        order_layout.addWidget(self.down_btn)

        order_layout.addStretch()

        self.selected_count_label = QLabel("0 jobs selected")
        self.selected_count_label.setStyleSheet("color: #888;")
        order_layout.addWidget(self.selected_count_label)

        selected_layout.addLayout(order_layout)

        picker_layout.addWidget(selected_group)

        # Equal stretch for both lists
        available_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        selected_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout.addLayout(picker_layout)

        # === Validation Label ===
        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #e74c3c;")
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)

        # === Buttons ===
        button_layout = QHBoxLayout()

        if self.is_edit_mode:
            self.delete_btn = QPushButton("Delete Batch")
            self.delete_btn.setProperty("danger", True)
            self.delete_btn.clicked.connect(self._on_delete)
            button_layout.addWidget(self.delete_btn)

        button_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setProperty("flat", True)
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.setProperty("primary", True)
        self.save_btn.clicked.connect(self._on_save)
        button_layout.addWidget(self.save_btn)

        layout.addLayout(button_layout)

        # Force style refresh for property-based styling
        flat_buttons = [self.add_btn, self.add_all_btn, self.remove_btn,
                        self.remove_all_btn, self.up_btn, self.down_btn, self.cancel_btn]
        for btn in flat_buttons:
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        # Populate available jobs
        self._populate_available_jobs()

    def _populate_available_jobs(self, filter_text: str = ""):
        """Populate available jobs list, excluding already selected ones."""
        self.available_list.clear()

        # Get currently selected slugs
        selected_slugs = set()
        for i in range(self.selected_list.count()):
            item = self.selected_list.item(i)
            selected_slugs.add(item.data(Qt.ItemDataRole.UserRole))

        filter_lower = filter_text.lower()
        count = 0

        for slug, job in sorted(self._all_jobs.items()):
            # Skip if already selected
            if slug in selected_slugs:
                continue

            # Apply filter
            display = f"{job.name} [{job.capture_type}]"
            if filter_lower and filter_lower not in display.lower() and filter_lower not in slug.lower():
                continue

            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, slug)
            item.setToolTip(f"Slug: {slug}")
            self.available_list.addItem(item)
            count += 1

        self.available_count_label.setText(f"{count} jobs")

    def _add_job_to_selected(self, slug: str):
        """Add a job to the selected list."""
        job = self._all_jobs.get(slug)
        if not job:
            # Job not in database, still add it (might be invalid)
            display = f"{slug} [unknown]"
        else:
            display = f"{job.name} [{job.capture_type}]"

        item = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, slug)
        item.setToolTip(f"Slug: {slug}")

        # Mark invalid jobs
        if slug not in self._all_jobs:
            item.setForeground(Qt.GlobalColor.red)
            item.setToolTip(f"Slug: {slug} (NOT FOUND)")

        self.selected_list.addItem(item)
        self._update_selected_count()

    def _update_selected_count(self):
        """Update the selected count label."""
        count = self.selected_list.count()
        self.selected_count_label.setText(f"{count} job{'s' if count != 1 else ''} selected")

    def populate_from_batch(self):
        """Populate form from existing batch."""
        if not self.batch:
            return

        self.name_input.setText(self.batch.name)

        # Add jobs in order
        for slug in self.batch.jobs:
            self._add_job_to_selected(slug)

        # Refresh available list (excludes selected)
        self._populate_available_jobs()

    def _on_filter_changed(self, text: str):
        """Handle filter text change."""
        self._populate_available_jobs(text)

    def _on_add_job(self):
        """Add selected jobs from available to selected list."""
        selected_items = self.available_list.selectedItems()
        if not selected_items:
            return

        for item in selected_items:
            slug = item.data(Qt.ItemDataRole.UserRole)
            self._add_job_to_selected(slug)

        # Refresh available list
        self._populate_available_jobs(self.filter_input.text())

    def _on_add_all(self):
        """Add all visible available jobs."""
        for i in range(self.available_list.count()):
            item = self.available_list.item(i)
            slug = item.data(Qt.ItemDataRole.UserRole)
            self._add_job_to_selected(slug)

        self._populate_available_jobs(self.filter_input.text())

    def _on_remove_job(self):
        """Remove selected jobs from selected list."""
        selected_items = self.selected_list.selectedItems()
        if not selected_items:
            return

        for item in selected_items:
            row = self.selected_list.row(item)
            self.selected_list.takeItem(row)

        self._update_selected_count()
        self._populate_available_jobs(self.filter_input.text())

    def _on_remove_all(self):
        """Remove all jobs from selected list."""
        self.selected_list.clear()
        self._update_selected_count()
        self._populate_available_jobs(self.filter_input.text())

    def _on_move_up(self):
        """Move selected job up in the list."""
        current_row = self.selected_list.currentRow()
        if current_row <= 0:
            return

        item = self.selected_list.takeItem(current_row)
        self.selected_list.insertItem(current_row - 1, item)
        self.selected_list.setCurrentRow(current_row - 1)

    def _on_move_down(self):
        """Move selected job down in the list."""
        current_row = self.selected_list.currentRow()
        if current_row < 0 or current_row >= self.selected_list.count() - 1:
            return

        item = self.selected_list.takeItem(current_row)
        self.selected_list.insertItem(current_row + 1, item)
        self.selected_list.setCurrentRow(current_row + 1)

    def _get_selected_slugs(self) -> List[str]:
        """Get ordered list of selected job slugs."""
        slugs = []
        for i in range(self.selected_list.count()):
            item = self.selected_list.item(i)
            slugs.append(item.data(Qt.ItemDataRole.UserRole))
        return slugs

    def _validate(self) -> bool:
        """Validate form data."""
        errors = []

        name = self.name_input.text().strip()
        if not name:
            errors.append("Batch name is required")

        slugs = self._get_selected_slugs()
        if not slugs:
            errors.append("Select at least one job")

        if errors:
            self.validation_label.setText("• " + "\n• ".join(errors))
            return False

        self.validation_label.setText("")
        return True

    def _on_save(self):
        """Save the batch definition."""
        if not self._validate():
            return

        name = self.name_input.text().strip()
        slugs = self._get_selected_slugs()

        # Generate filename from name if new batch
        if self.is_edit_mode:
            filename = self.batch.filename
        else:
            filename = name.lower().replace(' ', '-')
            # Remove special characters
            filename = ''.join(c for c in filename if c.isalnum() or c == '-')
            filename = filename.strip('-') + '.yaml'

            # Check if file already exists
            existing = [b.filename for b in self.batch_loader.list_batches(validate=False)]
            if filename in existing:
                reply = QMessageBox.question(
                    self,
                    "File Exists",
                    f"A batch file named '{filename}' already exists.\n\nOverwrite it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        try:
            batch = BatchDefinition(
                name=name,
                filename=filename,
                jobs=slugs,
            )

            self.batch_loader.save_batch(batch)
            self.batch_saved.emit(filename)
            self.accept()

        except Exception as e:
            self.validation_label.setText(f"Error saving: {e}")

    def _on_delete(self):
        """Delete the batch file."""
        if not self.batch:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete batch '{self.batch.name}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.batch_loader.delete_batch(self.batch.filename)
                self.batch_deleted.emit(self.batch.filename)
                self.accept()
            except Exception as e:
                self.validation_label.setText(f"Error deleting: {e}")


# For standalone testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    # Would need actual instances for real testing
    # loader = BatchLoader()
    # repo = JobsRepository()
    # dialog = BatchEditDialog(loader, repo)
    # dialog.exec()

    print("Run from main application for testing")
    sys.exit(0)