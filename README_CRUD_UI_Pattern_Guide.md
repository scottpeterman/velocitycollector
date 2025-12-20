# VelocityCollector DCIM UI Pattern Guide

This document describes the UI architecture for DCIM (Data Center Infrastructure Management) views in VelocityCollector. Use this as a reference when building new entity views (Sites, Platforms, Roles, Manufacturers).

## Architecture Overview

Each DCIM entity follows a consistent three-file pattern:

```
vcollector/ui/widgets/
├── devices_view.py        # Main list/table view
├── device_dialogs.py      # Detail and Edit dialogs
├── sites_view.py          # (future)
├── site_dialogs.py        # (future)
├── platforms_view.py      # (future)
├── platform_dialogs.py    # (future)
└── ...
```

## Component Responsibilities

### ListView (`*_view.py`)

The main view widget displayed in the application's content area. Responsibilities:

| Component | Purpose |
|-----------|---------|
| **Header** | Title, Add button, Refresh button |
| **StatCards** | Quick metrics (total count, active count, related counts) |
| **Filter Bar** | Search input, dropdown filters, Clear button |
| **Table** | Sortable data grid with selection and context menu |
| **Status Bar** | Operation feedback ("Showing 13 devices", "Copied: 172.16.1.1") |

**Key behaviors:**
- Debounced search (300ms delay before query)
- Filter dropdowns populated from database
- Context menu for row actions
- Keyboard shortcuts for power users
- Signals for external integration (selection, double-click)

### DetailDialog (`*_dialogs.py` → `*DetailDialog`)

Read-only modal for inspecting a record without risk of accidental edits.

**Structure:**
- Tabbed layout grouping related fields
- All fields displayed as `QLabel` (selectable text)
- Color-coded status indicators
- "Edit" button to transition to edit mode
- "Close" button

**When to use tabs:**
- 8+ fields warrant organization
- Logical groupings exist (identity, network, hardware, metadata)
- Keep General/Identity tab first with most-used fields

### EditDialog (`*_dialogs.py` → `*EditDialog`)

Modal form for creating or updating records.

**Dual-mode operation:**
```python
# Create mode - no existing record
dialog = DeviceEditDialog(repo, device=None)

# Edit mode - modifying existing record  
dialog = DeviceEditDialog(repo, device=existing_device)
```

**Structure:**
- Same tab layout as DetailDialog for consistency
- Form inputs appropriate to field type
- Required fields marked with `*`
- Validation message area (red text)
- Button row: [Delete] ... [Cancel] [Save]

**Key behaviors:**
- Loads dropdown options from repository on init
- Pre-populates form in edit mode
- Validates before save
- Handles unique constraint violations gracefully
- Emits `device_saved(id)` signal on success
- Delete with confirmation (edit mode only)

## Implementation Patterns

### Repository Integration

Views receive an optional repository instance, defaulting to a new connection:

```python
def __init__(self, repo: Optional[DCIMRepository] = None, parent=None):
    super().__init__(parent)
    self.repo = repo or DCIMRepository()
```

This allows:
- Shared connection when embedded in main app
- Independent connection for standalone testing
- Dependency injection for unit tests

### Dropdown Population

Load lookup data in a dedicated method called during init:

```python
def load_filters(self):
    """Load filter dropdown options from database."""
    self.site_filter.clear()
    self.site_filter.addItem("All Sites", None)  # None = no filter
    for site in self.repo.get_sites():
        self.site_filter.addItem(site.name, site.slug)  # display, data
```

For edit dialogs, store the lookup lists for validation:

```python
def load_lookups(self):
    self._sites = self.repo.get_sites()
    self.site_combo.clear()
    self.site_combo.addItem("— Select Site —", None)
    for site in self._sites:
        self.site_combo.addItem(f"{site.name} ({site.slug})", site.id)
```

### Table Data Binding

Store the record ID in the first column's UserRole data:

```python
def _populate_table(self):
    for row, device in enumerate(self._devices):
        name_item = QTableWidgetItem(device.name)
        name_item.setData(Qt.ItemDataRole.UserRole, device.id)
        self.device_table.setItem(row, 0, name_item)
```

Retrieve it when needed:

```python
device_id = self.device_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
```

### Search Debouncing

Prevent excessive queries while typing:

```python
def __init__(self):
    self._search_timer = QTimer()
    self._search_timer.setSingleShot(True)
    self._search_timer.timeout.connect(self._do_search)

def _on_search_changed(self, text: str):
    self._search_timer.stop()
    self._search_timer.start(300)  # 300ms debounce

def _do_search(self):
    self._load_devices()
```

### Form Validation

Collect errors into a list, display all at once:

```python
def validate(self) -> bool:
    errors = []
    
    if not self.name_input.text().strip():
        errors.append("Name is required")
    
    if not self.site_combo.currentData():
        errors.append("Site selection is required")
    
    ip4 = self.ip4_input.text().strip()
    if ip4 and not self._validate_ip4(ip4):
        errors.append("Invalid IPv4 address format")
    
    if errors:
        self.validation_label.setText("• " + "\n• ".join(errors))
        return False
    
    self.validation_label.setText("")
    return True
```

### Signal Flow

```
┌─────────────────┐     device_saved(id)     ┌─────────────────┐
│  EditDialog     │ ──────────────────────►  │  ListView       │
└─────────────────┘                          └─────────────────┘
                                                     │
                                                     ▼
                                              refresh_data()
                                                     │
                                                     ▼
                                             _update_stats()
                                             _load_devices()
```

```
┌─────────────────┐     edit_requested(id)   ┌─────────────────┐
│  DetailDialog   │ ──────────────────────►  │  ListView       │
└─────────────────┘                          └─────────────────┘
                                                     │
                                                     ▼
                                             _edit_device(id)
                                                     │
                                                     ▼
                                             DeviceEditDialog()
```

### Keyboard Shortcuts

Standard shortcuts for all list views:

| Shortcut | Action |
|----------|--------|
| `Ctrl+N` | New record |
| `Enter` | Edit selected |
| `Delete` | Delete selected |
| `Ctrl+F` | Focus search |
| `F5` | Refresh |

```python
def init_shortcuts(self):
    QShortcut(QKeySequence("Ctrl+N"), self, self._on_add_device)
    QShortcut(QKeySequence("Return"), self, self._edit_selected)
    QShortcut(QKeySequence("Delete"), self, self._delete_selected)
    QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.search_input.setFocus())
    QShortcut(QKeySequence("F5"), self, self.refresh_data)
```

### Context Menu Structure

Consistent ordering for all entity context menus:

```
┌──────────────────────┐
│ View Details         │
│ Edit [Entity]        │
├──────────────────────┤
│ [Entity-specific     │
│  actions]            │
├──────────────────────┤
│ Copy ►               │
│   └─ Copy Name       │
│   └─ Copy [Field]    │
├──────────────────────┤
│ Set Status ►         │  (if applicable)
│   └─ Active          │
│   └─ Offline         │
│   └─ ...             │
├──────────────────────┤
│ Delete [Entity]      │
└──────────────────────┘
```

### Status Color Coding

Consistent colors across all views:

```python
STATUS_COLORS = {
    'active': '#2ecc71',           # Green
    'planned': '#3498db',          # Blue
    'staged': '#f39c12',           # Orange
    'failed': '#e74c3c',           # Red
    'offline': '#95a5a6',          # Gray
    'decommissioning': '#9b59b6',  # Purple
    'inventory': '#1abc9c',        # Teal
    'retired': '#7f8c8d',          # Dark Gray
    'staging': '#f39c12',          # Orange (site status)
}
```

### Relative Time Formatting

For "last seen" / "last collected" fields:

```python
def _format_relative_time(self, timestamp: Optional[str]) -> str:
    if not timestamp:
        return "Never"
    
    delta = datetime.now() - parse_timestamp(timestamp)
    
    if delta.days > 30:
        return dt.strftime("%Y-%m-%d")
    elif delta.days > 0:
        return f"{delta.days}d ago"
    elif delta.seconds >= 3600:
        return f"{delta.seconds // 3600}h ago"
    elif delta.seconds >= 60:
        return f"{delta.seconds // 60}m ago"
    else:
        return "Just now"
```

## Entity-Specific Notes

### Devices
- **Filters:** Site, Manufacturer, Status
- **Stats:** Total, Active, Sites, Manufacturers, Platforms
- **Special:** SSH action in context menu, credential override field

### Sites (to implement)
- **Filters:** Status, Region (if added)
- **Stats:** Total, Active, Device Count
- **Special:** Time zone selector, address fields

### Platforms (to implement)
- **Filters:** Manufacturer
- **Stats:** Total, Device Count per platform
- **Special:** Netmiko device type dropdown, paging command field

### Device Roles (to implement)
- **Filters:** None (typically small list)
- **Stats:** Total, Device Count per role
- **Special:** Color picker for role color

### Manufacturers (to implement)
- **Filters:** None (typically small list)
- **Stats:** Total, Platform Count, Device Count
- **Special:** Minimal fields (name, slug, description)

## File Template

When creating a new entity view, start with this structure:

```python
# vcollector/ui/widgets/[entity]_view.py

"""
VelocityCollector [Entity] View
"""

from PyQt6.QtWidgets import (...)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QShortcut, QKeySequence

from vcollector.dcim.dcim_repo import DCIMRepository, [Entity]
from vcollector.ui.widgets.stat_cards import StatCard
from vcollector.ui.widgets.[entity]_dialogs import [Entity]DetailDialog, [Entity]EditDialog


class [Entity]sView(QWidget):
    """[Entity] list view with filtering and CRUD operations."""
    
    # Signals
    [entity]_selected = pyqtSignal(int)
    [entity]_double_clicked = pyqtSignal(int)
    
    def __init__(self, repo: Optional[DCIMRepository] = None, parent=None):
        super().__init__(parent)
        self.repo = repo or DCIMRepository()
        self._[entities]: List[[Entity]] = []
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        
        self.init_ui()
        self.init_shortcuts()
        self.load_filters()
        self.refresh_data()
    
    def init_ui(self): ...
    def init_shortcuts(self): ...
    def load_filters(self): ...
    def refresh_data(self): ...
    def _update_stats(self): ...
    def _load_[entities](self): ...
    def _populate_table(self): ...
    # ... etc
```

## Testing

Each view should be runnable standalone:

```python
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow
    
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("[Entity] Management")
    window.resize(1200, 700)
    
    view = [Entity]sView()
    window.setCentralWidget(view)
    
    window.show()
    sys.exit(app.exec())
```

Run with: `python -m vcollector.ui.widgets.[entity]_view`