# VCollector PyQt6 Style Guide

This guide documents the widget patterns and styling conventions established in the vault_view.py reference implementation. Follow these patterns for visual consistency across all views.

---

## Theme System

The app uses a centralized theme system (`themes.py`) with three themes: `light`, `dark`, and `cyber`. Stylesheets are generated dynamically via `get_stylesheet(theme_name)`.

### Key Theme Colors

| Token | Purpose |
|-------|---------|
| `primary` | Primary action buttons, accents |
| `primary_hover` | Button hover state |
| `primary_text` | Text on primary-colored backgrounds |
| `text` | Main body text |
| `text_secondary` | Subdued text, labels, hints |
| `surface_bg` | Card/panel backgrounds |
| `surface_alt` | Alternate surface (toolbars, secondary buttons) |
| `window_bg` | Main window background |
| `border` | Borders, dividers |
| `input_bg` | Input field backgrounds |
| `input_border` | Input field borders |
| `input_focus` | Focused input border color |

---

## View Structure

Every view should follow this hierarchy for proper scrolling on scaled displays:

```python
def init_ui(self):
    # 1. Main layout - zero margins, holds only the scroll area
    main_layout = QVBoxLayout(self)
    main_layout.setContentsMargins(0, 0, 0, 0)

    # 2. Scroll area - enables scrolling on small/scaled displays
    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QFrame.Shape.NoFrame)
    scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    # 3. Content widget - actual view content lives here
    content_widget = QWidget()
    layout = QVBoxLayout(content_widget)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(16)

    # ... add widgets to layout ...

    layout.addStretch()  # Push content to top

    # 4. Wire it up
    scroll_area.setWidget(content_widget)
    main_layout.addWidget(scroll_area)
```

### Standard Margins & Spacing

| Context | Value |
|---------|-------|
| Content margins | `24, 24, 24, 24` |
| Section spacing | `16` |
| Within group boxes | `12` |
| Button rows | `QHBoxLayout` with `addStretch()` at end |

---

## Widget Patterns

### Headers

```python
# Page title
header = QLabel("Page Title")
header.setProperty("heading", True)

# Section description
description = QLabel("Explanatory text goes here.")
description.setProperty("subheading", True)
description.setWordWrap(True)
```

### Buttons

**Primary action** (default style):
```python
btn = QPushButton("Primary Action")
# No property needed - uses primary color
```

**Secondary action**:
```python
btn = QPushButton("Secondary Action")
btn.setProperty("secondary", True)
```

**Danger action** (destructive):
```python
btn = QPushButton("Delete")
btn.setProperty("danger", True)
```

**Success action**:
```python
btn = QPushButton("Confirm")
btn.setProperty("success", True)
```

> **Critical**: Always call `setProperty()` for non-primary buttons. Without it, the stylesheet selectors won't match and you'll get contrast issues, especially in the cyber theme.

### Button Rows

```python
row = QHBoxLayout()
row.addWidget(action_btn_1)
row.addWidget(action_btn_2)
row.addStretch()  # Pushes buttons to the left
parent_layout.addLayout(row)
```

### Cards (Status Cards, Info Cards)

```python
class MyCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)  # Enables card styling
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Card content...
```

### Group Boxes (Sections)

```python
group = QGroupBox("Section Title")
group_layout = QVBoxLayout(group)
# Add widgets to group_layout
parent_layout.addWidget(group)
```

### Form Inputs

```python
# Text input
self.text_input = QLineEdit()
self.text_input.setPlaceholderText("Enter value")

# Password input
self.password_input = QLineEdit()
self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
self.password_input.setPlaceholderText("Enter password")

# Connect enter key
self.text_input.returnPressed.connect(self.on_submit)
```

### Input + Button Row

```python
row_layout = QHBoxLayout()

self.input_field = QLineEdit()
self.input_field.setPlaceholderText("Enter value")
row_layout.addWidget(self.input_field)

self.action_btn = QPushButton("Submit")
row_layout.addWidget(self.action_btn)

parent_layout.addLayout(row_layout)
```

### Hint/Help Boxes

```python
hint_group = QGroupBox("Hint Title")
hint_layout = QVBoxLayout(hint_group)

hint_text = QLabel("Explanatory text here.")
hint_text.setProperty("subheading", True)
hint_text.setWordWrap(True)
hint_layout.addWidget(hint_text)

# Code example
code = QLabel("example --command here")
code.setStyleSheet(
    "font-family: monospace; padding: 8px; "
    "background-color: rgba(0,0,0,0.1); border-radius: 4px;"
)
hint_layout.addWidget(code)
```

---

## Dialogs

```python
class MyDialog(QDialog):
    def __init__(self, parent=None, title: str = "Dialog Title"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        
        # Content...
        
        # Standard button box
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
```

---

## Signals Pattern

Define signals at class level for view state changes:

```python
class MyView(QWidget):
    # Signals
    data_changed = pyqtSignal()
    item_selected = pyqtSignal(str)  # With payload
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # ...
    
    def some_action(self):
        # Emit when state changes
        self.data_changed.emit()
```

---

## Message Boxes

```python
# Information
QMessageBox.information(self, "Title", "Message")

# Warning
QMessageBox.warning(self, "Title", "Warning message")

# Error
QMessageBox.critical(self, "Title", "Error message")

# Confirmation
reply = QMessageBox.warning(
    self,
    "Confirm Action",
    "Are you sure?",
    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    QMessageBox.StandardButton.No  # Default
)
if reply == QMessageBox.StandardButton.Yes:
    # proceed
```

---

## Property Reference

These properties trigger specific stylesheet selectors:

| Widget | Property | Value | Effect |
|--------|----------|-------|--------|
| `QLabel` | `heading` | `True` | Large, bold title |
| `QLabel` | `subheading` | `True` | Secondary/muted text |
| `QLabel` | `stat` | `True` | Large stat number |
| `QLabel` | `stat_label` | `True` | Stat caption |
| `QPushButton` | `secondary` | `True` | Muted button style |
| `QPushButton` | `danger` | `True` | Red/destructive style |
| `QPushButton` | `success` | `True` | Green/confirm style |
| `QFrame` | `card` | `True` | Elevated card surface |
| `QFrame` | `separator` | `True` | Horizontal divider line |

---

## Checklist for New Views

- [ ] Main layout has zero margins
- [ ] Content wrapped in `QScrollArea`
- [ ] Scroll area has `setWidgetResizable(True)`
- [ ] Scroll area has `setFrameShape(QFrame.Shape.NoFrame)`
- [ ] Content widget uses `24, 24, 24, 24` margins
- [ ] Layout ends with `addStretch()` 
- [ ] Page header uses `setProperty("heading", True)`
- [ ] Descriptions use `setProperty("subheading", True)` and `setWordWrap(True)`
- [ ] Secondary buttons use `setProperty("secondary", True)`
- [ ] Danger buttons use `setProperty("danger", True)`
- [ ] Cards use `setProperty("card", True)`
- [ ] Button rows end with `addStretch()`
- [ ] Signals defined for state changes parent needs to know about