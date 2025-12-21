# =============================================================================
# THEME DEFINITIONS - Matching tfsm_fire_tester
# =============================================================================

THEMES = {
    "light": {
        "name": "Light",
        "window_bg": "#FAFAFA",
        "surface_bg": "#FFFFFF",
        "surface_alt": "#F5F5F5",
        "primary": "#D35400",  # Velocity orange
        "primary_hover": "#E67E22",
        "primary_text": "#FFFFFF",
        "text": "#212121",
        "text_secondary": "#757575",
        "border": "#E0E0E0",
        "input_bg": "#FFFFFF",
        "input_border": "#BDBDBD",
        "input_focus": "#D35400",
        "success": "#27AE60",
        "warning": "#F39C12",
        "error": "#E74C3C",
        "info": "#3498DB",
        "table_header": "#EFEBE9",
        "table_alt_row": "#FAFAFA",
        "selection": "#FADBD8",
        "scrollbar_bg": "#F5F5F5",
        "scrollbar_handle": "#BDBDBD",
        "code_bg": "#F8F9FA",
        "nav_bg": "#FFFFFF",
        "nav_selected": "#D35400",
        "nav_hover": "#FEF5E7",
        "accent_gradient_start": "#D35400",
        "accent_gradient_end": "#E67E22",
    },
    "dark": {
        "name": "Dark",
        "window_bg": "#1A1A1A",
        "surface_bg": "#242424",
        "surface_alt": "#2D2D2D",
        "primary": "#E67E22",
        "primary_hover": "#F39C12",
        "primary_text": "#FFFFFF",
        "text": "#E0E0E0",
        "text_secondary": "#9E9E9E",
        "border": "#3E3E42",
        "input_bg": "#333333",
        "input_border": "#4A4A4A",
        "input_focus": "#E67E22",
        "success": "#2ECC71",
        "warning": "#F1C40F",
        "error": "#E74C3C",
        "info": "#3498DB",
        "table_header": "#2D2D30",
        "table_alt_row": "#282828",
        "selection": "#4A3728",
        "scrollbar_bg": "#1E1E1E",
        "scrollbar_handle": "#555555",
        "code_bg": "#1E1E1E",
        "nav_bg": "#1F1F1F",
        "nav_selected": "#E67E22",
        "nav_hover": "#333333",
        "accent_gradient_start": "#D35400",
        "accent_gradient_end": "#E67E22",
    },
    "cyber": {
        "name": "Cyber",
        "window_bg": "#0A0E14",
        "surface_bg": "#0D1117",
        "surface_alt": "#161B22",
        "primary": "#00d4ff",
        "primary_hover": "#00F5C4",
        "primary_text": "#0A0E14",
        "text": "#C0FFF0",
        "text_secondary": "#00d4ff",
        "border": "#00d4ff40",
        "input_bg": "#0D1117",
        "input_border": "#00d4ff60",
        "input_focus": "#00d4ff",
        "success": "#00F5C4",
        "warning": "#FFB800",
        "error": "#FF3366",
        "info": "#00B4D8",
        "table_header": "#161B22",
        "table_alt_row": "#0D1117",
        "selection": "#00d4ff30",
        "scrollbar_bg": "#161B22",
        "scrollbar_handle": "#00d4ff",
        "code_bg": "#0A0E14",
        "nav_bg": "#0D1117",
        "nav_selected": "#00d4ff",
        "nav_hover": "#00d4ff20",
        "accent_gradient_start": "#00d4ff",
        "accent_gradient_end": "#00F5C4",
    },

}


def get_stylesheet(theme_name: str) -> str:
    """Generate comprehensive stylesheet for the given theme"""
    t = THEMES.get(theme_name, THEMES["dark"])

    return f"""
        /* ===== GLOBAL ===== */
        QMainWindow {{
            background-color: {t['window_bg']};
            color: {t['text']};
        }}

        QMainWindow > QWidget {{
            background-color: {t['window_bg']};
        }}

        QDialog {{
            background-color: {t['window_bg']};
            color: {t['text']};
        }}

        QWidget {{
            color: {t['text']};
            font-family: 'Segoe UI', 'SF Pro Display', -apple-system, sans-serif;
            font-size: 13px;
        }}

        /* ===== MENU BAR ===== */
        QMenuBar {{
            background-color: {t['surface_bg']};
            color: {t['text']};
            border-bottom: 1px solid {t['border']};
            padding: 4px 0;
        }}

        QMenuBar::item {{
            background-color: transparent;
            padding: 6px 12px;
            border-radius: 4px;
            margin: 2px;
        }}

        QMenuBar::item:selected {{
            background-color: {t['nav_hover']};
        }}

        QMenu {{
            background-color: {t['surface_bg']};
            color: {t['text']};
            border: 1px solid {t['border']};
            border-radius: 8px;
            padding: 6px;
        }}

        QMenu::item {{
            padding: 8px 32px 8px 16px;
            border-radius: 4px;
            margin: 2px;
        }}

        QMenu::item:selected {{
            background-color: {t['selection']};
        }}

        QMenu::separator {{
            height: 1px;
            background-color: {t['border']};
            margin: 6px 8px;
        }}

        /* ===== TOOLBAR ===== */
        QToolBar {{
            background-color: {t['surface_alt']};
            border: none;
            border-bottom: 1px solid {t['border']};
            padding: 6px 12px;
            spacing: 8px;
        }}

        QToolBar QLabel {{
            color: {t['text_secondary']};
            font-weight: 500;
        }}

        QToolButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            padding: 6px 12px;
            color: {t['text']};
        }}

        QToolButton:hover {{
            background-color: {t['nav_hover']};
            border-color: {t['border']};
        }}

        QToolButton:pressed {{
            background-color: {t['selection']};
        }}

        /* ===== SPLITTER ===== */
        QSplitter {{
            background-color: {t['window_bg']};
        }}

        QSplitter::handle {{
            background-color: {t['border']};
        }}

        QSplitter::handle:horizontal {{
            width: 1px;
        }}

        QSplitter::handle:vertical {{
            height: 1px;
        }}

        /* ===== NAVIGATION LIST (Left Sidebar) ===== */
        QListWidget#navList {{
            background-color: {t['nav_bg']};
            border: none;
            border-right: 1px solid {t['border']};
            outline: none;
            padding: 8px;
        }}

        QListWidget#navList::item {{
            background-color: transparent;
            color: {t['text']};
            border-radius: 6px;
            padding: 12px 16px;
            margin: 2px 0;
            font-weight: 500;
        }}

        QListWidget#navList::item:hover {{
            background-color: {t['nav_hover']};
        }}

        QListWidget#navList::item:selected {{
            background-color: {t['nav_selected']};
            color: {t['primary_text']};
        }}

        /* ===== GROUP BOX ===== */
        QGroupBox {{
            background-color: {t['surface_bg']};
            border: 1px solid {t['border']};
            border-radius: 10px;
            margin-top: 16px;
            padding: 20px;
            padding-top: 36px;
            font-weight: 600;
        }}

        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 16px;
            top: 8px;
            padding: 0 10px;
            color: {t['text']};
            background-color: {t['surface_bg']};
            font-size: 14px;
        }}

        /* ===== TAB WIDGET ===== */
        QTabWidget {{
            background-color: {t['window_bg']};
        }}

        QTabWidget::pane {{
            background-color: {t['surface_bg']};
            border: 1px solid {t['border']};
            border-radius: 10px;
            padding: 12px;
        }}

        QTabBar::tab {{
            background-color: {t['surface_alt']};
            color: {t['text_secondary']};
            border: 1px solid {t['border']};
            border-bottom: none;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            padding: 10px 20px;
            margin-right: 4px;
            font-weight: 500;
        }}

        QTabBar::tab:selected {{
            background-color: {t['surface_bg']};
            color: {t['primary']};
            border-bottom: 3px solid {t['primary']};
        }}

        QTabBar::tab:hover:!selected {{
            background-color: {t['nav_hover']};
            color: {t['text']};
        }}

        /* ===== BUTTONS ===== */
        QPushButton {{
            background-color: {t['primary']};
            color: {t['primary_text']};
            border: none;
            border-radius: 8px;
            padding: 10px 20px;
            min-height: 20px;
            font-weight: 600;
            font-size: 13px;
        }}

        QPushButton:hover {{
            background-color: {t['primary_hover']};
        }}

        QPushButton:pressed {{
            background-color: {t['primary']};
            padding-top: 11px;
            padding-bottom: 9px;
        }}

        QPushButton:disabled {{
            background-color: {t['border']};
            color: {t['text_secondary']};
        }}

        QPushButton[secondary="true"] {{
            background-color: {t['surface_alt']};
            color: {t['text']};
            border: 1px solid {t['input_border']};
        }}

        QPushButton[secondary="true"]:hover {{
            background-color: {t['nav_hover']};
            border-color: {t['primary']};
        }}

        QPushButton[danger="true"] {{
            background-color: {t['error']};
        }}

        QPushButton[danger="true"]:hover {{
            background-color: #C0392B;
        }}

        QPushButton[success="true"] {{
            background-color: {t['success']};
        }}

        QPushButton[success="true"]:hover {{
            background-color: #229954;
        }}

        /* ===== INPUT FIELDS ===== */
        QLineEdit, QSpinBox {{
            background-color: {t['input_bg']};
            color: {t['text']};
            border: 1px solid {t['input_border']};
            border-radius: 8px;
            padding: 10px 14px;
            min-height: 20px;
            selection-background-color: {t['selection']};
        }}

        QLineEdit:focus, QSpinBox:focus {{
            border: 2px solid {t['input_focus']};
            padding: 9px 13px;
        }}

        QLineEdit:disabled {{
            background-color: {t['surface_alt']};
            color: {t['text_secondary']};
        }}

        QTextEdit, QPlainTextEdit {{
            background-color: {t['code_bg']};
            color: {t['text']};
            border: 1px solid {t['border']};
            border-radius: 8px;
            padding: 12px;
            font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', 'Monaco', monospace;
            font-size: 12px;
            selection-background-color: {t['selection']};
        }}

        QTextEdit:focus, QPlainTextEdit:focus {{
            border: 2px solid {t['input_focus']};
        }}

        /* ===== COMBO BOX ===== */
        QComboBox {{
            background-color: {t['input_bg']};
            color: {t['text']};
            border: 1px solid {t['input_border']};
            border-radius: 8px;
            padding: 10px 14px;
            min-width: 140px;
        }}

        QComboBox:focus {{
            border: 2px solid {t['input_focus']};
        }}

        QComboBox::drop-down {{
            border: none;
            width: 28px;
        }}

        QComboBox::down-arrow {{
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid {t['text_secondary']};
            margin-right: 10px;
        }}

        QComboBox QAbstractItemView {{
            background-color: {t['surface_bg']};
            color: {t['text']};
            border: 1px solid {t['border']};
            border-radius: 8px;
            selection-background-color: {t['selection']};
            padding: 4px;
        }}

        /* ===== TABLES ===== */
        QTableWidget, QTableView {{
            background-color: {t['surface_bg']};
            color: {t['text']};
            border: 1px solid {t['border']};
            border-radius: 8px;
            gridline-color: {t['border']};
            selection-background-color: {t['selection']};
        }}

        QTableWidget::item, QTableView::item {{
            padding: 10px 12px;
            border-bottom: 1px solid {t['border']};
        }}

        QTableWidget::item:selected, QTableView::item:selected {{
            background-color: {t['selection']};
        }}

        QTableWidget::item:alternate {{
            background-color: {t['table_alt_row']};
        }}

        QHeaderView {{
            background-color: {t['table_header']};
        }}

        QHeaderView::section {{
            background-color: {t['table_header']};
            color: {t['text']};
            border: none;
            border-bottom: 2px solid {t['border']};
            border-right: 1px solid {t['border']};
            padding: 12px 10px;
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        QTableCornerButton::section {{
            background-color: {t['table_header']};
            border: none;
        }}

        /* ===== TREE WIDGET ===== */
        QTreeWidget {{
            background-color: {t['surface_bg']};
            color: {t['text']};
            border: 1px solid {t['border']};
            border-radius: 8px;
            outline: none;
        }}

        QTreeWidget::item {{
            padding: 8px 4px;
            border-radius: 4px;
        }}

        QTreeWidget::item:hover {{
            background-color: {t['nav_hover']};
        }}

        QTreeWidget::item:selected {{
            background-color: {t['selection']};
        }}

        QTreeWidget::branch:has-children:!has-siblings:closed,
        QTreeWidget::branch:closed:has-children:has-siblings {{
            border-image: none;
            image: none;
        }}

        QTreeWidget::branch:open:has-children:!has-siblings,
        QTreeWidget::branch:open:has-children:has-siblings {{
            border-image: none;
            image: none;
        }}

        /* ===== CHECKBOX ===== */
        QCheckBox {{
            spacing: 10px;
        }}

        QCheckBox::indicator {{
            width: 20px;
            height: 20px;
            border: 2px solid {t['input_border']};
            border-radius: 5px;
            background-color: {t['input_bg']};
        }}

        QCheckBox::indicator:checked {{
            background-color: {t['primary']};
            border-color: {t['primary']};
        }}

        QCheckBox::indicator:hover {{
            border-color: {t['primary']};
        }}

        /* ===== PROGRESS BAR ===== */
        QProgressBar {{
            background-color: {t['surface_alt']};
            border: none;
            border-radius: 6px;
            height: 12px;
            text-align: center;
            font-size: 10px;
            color: {t['text']};
        }}

        QProgressBar::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {t['accent_gradient_start']},
                stop:1 {t['accent_gradient_end']});
            border-radius: 6px;
        }}

        /* ===== SCROLL AREA ===== */
        QScrollArea {{
            background-color: {t['window_bg']};
            border: none;
        }}

        QScrollArea QWidget {{
            background-color: {t['window_bg']};
        }}

        /* ===== SCROLL BARS ===== */
        QScrollBar:vertical {{
            background-color: {t['scrollbar_bg']};
            width: 14px;
            border-radius: 7px;
            margin: 4px;
        }}

        QScrollBar::handle:vertical {{
            background-color: {t['scrollbar_handle']};
            min-height: 40px;
            border-radius: 5px;
            margin: 2px;
        }}

        QScrollBar::handle:vertical:hover {{
            background-color: {t['primary']};
        }}

        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}

        QScrollBar:horizontal {{
            background-color: {t['scrollbar_bg']};
            height: 14px;
            border-radius: 7px;
            margin: 4px;
        }}

        QScrollBar::handle:horizontal {{
            background-color: {t['scrollbar_handle']};
            min-width: 40px;
            border-radius: 5px;
            margin: 2px;
        }}

        QScrollBar::handle:horizontal:hover {{
            background-color: {t['primary']};
        }}

        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}

        /* ===== STATUS BAR ===== */
        QStatusBar {{
            background-color: {t['surface_alt']};
            color: {t['text_secondary']};
            border-top: 1px solid {t['border']};
            padding: 6px 12px;
            font-size: 12px;
        }}

        QStatusBar::item {{
            border: none;
        }}

        /* ===== LABELS ===== */
        QLabel {{
            color: {t['text']};
            min-height: 16px;
        }}

        QLabel[heading="true"] {{
            font-size: 18px;
            font-weight: 700;
            color: {t['text']};
            letter-spacing: -0.3px;
        }}

        QLabel[subheading="true"] {{
            color: {t['text_secondary']};
            font-size: 13px;
        }}

        QLabel[stat="true"] {{
            font-size: 21px;
            font-weight: 700;
            color: {t['primary']};
        }}

        QLabel[stat_label="true"] {{
            font-size: 10px;
            font-weight: 600;
            color: {t['text_secondary']};
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        /* ===== FRAMES ===== */
        QFrame[card="true"] {{
            background-color: {t['surface_bg']};
            border: 1px solid {t['border']};
            border-radius: 12px;
            padding: 16px;
        }}

        QFrame[separator="true"] {{
            background-color: {t['border']};
            max-height: 1px;
            min-height: 1px;
        }}

        /* ===== TOOLTIPS ===== */
        QToolTip {{
            background-color: {t['surface_bg']};
            color: {t['text']};
            border: 1px solid {t['border']};
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 12px;
        }}
        /* ===== LIST WIDGET ===== */
        QListWidget {{
            background-color: {t['surface_bg']};
            color: {t['text']};
            border: 1px solid {t['border']};
            border-radius: 8px;
            outline: none;
            padding: 4px;
        }}

        QListWidget::item {{
            padding: 8px 12px;
            border-radius: 4px;
            margin: 2px;
        }}

        QListWidget::item:hover {{
            background-color: {t['nav_hover']};
        }}

        QListWidget::item:selected {{
            background-color: {t['selection']};
        }}

        QListWidget::item:alternate {{
            background-color: {t['table_alt_row']};
        }}
        
    # Add this to styles.py after the existing QPushButton[success="true"] section
# (around line 280, after the success button hover style)

        /* ===== FLAT/SECONDARY BUTTONS ===== */
        QPushButton[flat="true"] {{
            background-color: {t['surface_alt']};
            color: {t['text']};
            border: 1px solid {t['input_border']};
            font-weight: 500;
        }}

        QPushButton[flat="true"]:hover {{
            background-color: {t['nav_hover']};
            border-color: {t['primary']};
            color: {t['text']};
        }}

        QPushButton[flat="true"]:pressed {{
            background-color: {t['selection']};
        }}
    """
