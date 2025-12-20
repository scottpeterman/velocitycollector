#!/usr/bin/env python3
"""
Stat Cards - Statistics display widgets
"""

from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel


class StatCard(QFrame):
    """A statistics display card showing a value and label."""

    def __init__(self, title: str, value: str, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.setFixedHeight(100)
        self.setMinimumWidth(140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)

        self.value_label = QLabel(value)
        self.value_label.setProperty("stat", True)
        layout.addWidget(self.value_label)

        self.title_label = QLabel(title)
        self.title_label.setProperty("stat_label", True)
        layout.addWidget(self.title_label)

        layout.addStretch()

    def set_value(self, value: str):
        """Update the displayed value."""
        self.value_label.setText(value)

    def set_title(self, title: str):
        """Update the title/label."""
        self.title_label.setText(title)