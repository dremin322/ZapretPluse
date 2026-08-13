from __future__ import annotations
from PySide6.QtGui import QColor

DEFAULT_ACCENT = "#3B82F6"


def _hex(value: str, fallback: str = DEFAULT_ACCENT) -> str:
    c = QColor(value)
    return c.name().upper() if c.isValid() else fallback


def _mix(color: str, other: str, amount: float) -> str:
    a = QColor(_hex(color)); b = QColor(_hex(other))
    amount = max(0.0, min(1.0, float(amount)))
    r = round(a.red() * (1 - amount) + b.red() * amount)
    g = round(a.green() * (1 - amount) + b.green() * amount)
    bl = round(a.blue() * (1 - amount) + b.blue() * amount)
    return QColor(r, g, bl).name().upper()


def build_style(theme: str = "dark", accent: str = DEFAULT_ACCENT) -> str:
    accent = _hex(accent)
    light = theme == "light"
    if light:
        bg, sidebar, card, card2 = "#F6F8FB", "#FFFFFF", "#FFFFFF", "#F8FAFC"
        text, text2, muted = "#162033", "#26344B", "#728096"
        border, border2 = "#E1E6ED", "#D7DEE8"
        hover = "#F0F4F9"
        input_bg = "#FFFFFF"
        track = "#E8EDF4"
        selected = _mix(accent, "#FFFFFF", .90)
        accent_hover = _mix(accent, "#000000", .08)
        accent_pressed = _mix(accent, "#000000", .18)
        accent_soft = _mix(accent, "#FFFFFF", .86)
        button_text = "#FFFFFF"
        button_bg, button_fg, button_hover = card2, text2, hover
        power_bg, power_border = accent, accent
        ok = "#22A96F"; warn = "#B9851C"; err = "#D94B56"
    else:
        bg, sidebar, card, card2 = "#090D12", "#0C1117", "#10161D", "#0E141B"
        text, text2, muted = "#F3F6FA", "#D9E0E8", "#8E9AAB"
        border, border2 = "#222B36", "#2B3541"
        hover = "#151C24"
        input_bg = "#0D1319"
        track = "#202937"
        # Dark theme deliberately mirrors the light theme instead of becoming
        # "black controls on black cards": controls become light surfaces with dark text.
        selected = "#F4F7FB"
        accent_hover = "#EEF2F7"
        accent_pressed = "#E2E8F0"
        accent_soft = "#EAF0F8"
        button_text = "#101419"
        button_bg, button_fg, button_hover = "#F5F7FA", "#101419", "#E8EDF4"
        power_bg, power_border = "#FFFFFF", "#FFFFFF"
        ok = "#35C98A"; warn = "#DDB45E"; err = "#FF737D"

    return f"""
* {{ font-family:'Segoe UI Variable','Segoe UI'; font-size:13px; }}
QWidget {{ color:{text}; background:{bg}; }}
QMainWindow, QWidget#appRoot, QWidget#page, QScrollArea#pageScroll, QStackedWidget#contentStack {{ background:{bg}; border:0; }}
QFrame#sidebar {{ background:{sidebar}; border-right:1px solid {border}; }}
QLabel {{ background:transparent; border:0; }}
QLabel#brand {{ font-size:22px; font-weight:700; color:{text}; }}
QLabel#brandCompact {{ font-size:18px; font-weight:700; color:{text}; }}
QLabel#pageTitle {{ font-size:24px; font-weight:700; color:{text}; }}
QLabel#headline {{ font-size:23px; font-weight:700; color:{text}; }}
QLabel#headlineSmall {{ font-size:18px; font-weight:650; color:{text}; }}
QLabel#section {{ font-size:17px; font-weight:650; color:{text}; }}
QLabel#value {{ font-size:14px; font-weight:500; color:{text}; }}
QLabel#valueSmall {{ font-size:13px; font-weight:600; color:{text}; }}
QLabel#fieldLabel {{ color:{muted}; font-weight:600; }}
QLabel#muted, QLabel#tiny {{ color:{muted}; }}
QLabel#mutedSmall {{ color:{muted}; font-size:11px; }}
QLabel#tiny {{ font-size:11px; }}
QLabel#ok {{ color:{ok}; font-weight:600; }}
QLabel#warn {{ color:{warn}; font-weight:600; }}
QLabel#error {{ color:{err}; font-weight:600; }}
QLabel#accent {{ color:{accent}; font-weight:600; }}
QLabel#compact {{ color:{text2}; padding:2px 0; }}
QLabel#pill {{ color:{text2}; background:{card2}; border:1px solid {border}; border-radius:8px; padding:5px 9px; font-size:11px; }}
QLabel#statusInfo {{ color:{muted}; padding:0; margin:0; min-height:0; }}
QFrame#card, QFrame#hero {{ background:{card}; border:1px solid {border}; border-radius:14px; }}
QFrame#hero {{ background:{card2}; border-color:{border2}; }}
QFrame#footer {{ background:{card2}; border:1px solid {border}; border-radius:11px; }}
QFrame#separator {{ background:{border}; border:0; }}
QPushButton {{ background:{button_bg}; color:{button_fg}; border:1px solid {border2}; border-radius:9px; padding:9px 13px; }}
QPushButton:hover {{ background:{button_hover}; border-color:{border2}; }}
QPushButton:pressed {{ background:{accent_pressed}; }}
QPushButton:disabled {{ color:{muted}; background:{card}; border-color:{border}; }}
QPushButton#power {{
    background:{power_bg}; border:1px solid {power_border}; color:{button_text}; font-weight:700; font-size:14px;
    padding:10px 17px; border-radius:10px; min-height:20px;
}}
QPushButton#power:hover {{ background:{accent_hover}; border-color:{accent_hover}; }}
QPushButton#power:pressed {{ background:{accent_pressed}; border-color:{accent_pressed}; }}
QPushButton#power:disabled, QPushButton#power[busy="true"] {{
    background:{accent_pressed}; border-color:{accent_pressed}; color:{button_text};
}}
QPushButton#secondary {{ background:{button_bg}; text-align:left; padding:9px 10px; border:1px solid {border2}; color:{button_fg}; }}
QPushButton#secondary:hover {{ background:{button_hover}; }}
QPushButton#iconButton {{ min-width:32px; max-width:32px; min-height:32px; max-height:32px; padding:6px; background:transparent; border:0; border-radius:8px; }}
QPushButton#iconButton:hover {{ background:{hover}; border:0; }}
QPushButton#refreshButton {{ min-width:28px; max-width:28px; min-height:28px; max-height:28px; padding:5px; background:transparent; border:0; border-radius:7px; }}
QPushButton#refreshButton:hover {{ background:{hover}; border:0; }}
QPushButton#refreshButton:pressed {{ background:{selected}; border:0; }}
QPushButton#navButton {{ background:transparent; border:0; text-align:left; color:{muted}; padding:10px 12px; border-radius:9px; font-size:13px; }}
QPushButton#navButton:hover {{ background:{hover}; color:{text}; }}
QPushButton#navButton:checked {{ background:{selected}; color:{accent}; font-weight:600; }}
QPushButton#accentPreview {{ background:{accent}; border:1px solid {accent}; min-width:46px; max-width:46px; min-height:28px; max-height:28px; border-radius:8px; }}
QLineEdit,QListWidget,QComboBox,QSpinBox,QTextEdit {{ background:{input_bg}; color:{text}; border:1px solid {border2}; border-radius:9px; padding:8px 10px; selection-background-color:{accent}; }}
QLineEdit:focus,QListWidget:focus,QComboBox:focus,QSpinBox:focus,QTextEdit:focus {{ border-color:{accent}; }}
QListWidget {{ outline:none; padding:5px; }}
QListWidget::item {{ padding:7px; border-radius:7px; }}
QListWidget::item:selected {{ background:{selected}; color:{text}; }}
QTabWidget::pane {{ border:0; }}
QTabBar::tab {{ background:transparent; color:{muted}; padding:9px 13px; margin-right:3px; border-radius:8px; }}
QTabBar::tab:selected {{ background:{selected}; color:{accent}; }}
QScrollBar:vertical {{ background:transparent; width:8px; margin:3px; }}
QScrollBar::handle:vertical {{ background:{border2}; border-radius:4px; min-height:28px; }}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
QCheckBox::indicator {{ width:17px; height:17px; }}
QToolTip {{ background:{card}; color:{text}; border:1px solid {border2}; padding:6px; }}
"""


DARK_STYLE = build_style("dark", DEFAULT_ACCENT)
LIGHT_STYLE = build_style("light", DEFAULT_ACCENT)
STYLE = DARK_STYLE
