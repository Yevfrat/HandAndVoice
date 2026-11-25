"""All colors, fonts and the global stylesheet live here."""

BG = "#0f1419"
BG_SIDEBAR = "#0b0f14"
BG_CARD = "#171d25"
BG_INPUT = "#1e2630"
BORDER = "#263040"
TEXT = "#e6edf3"
TEXT_DIM = "#8b98a8"
ACCENT = "#22d3ee"        # cyan
ACCENT_DARK = "#0e7490"
GREEN = "#34d399"
BLUE = "#60a5fa"
GREY = "#6b7280"
RED = "#f87171"
AMBER = "#fbbf24"

FONT_FAMILY = "Segoe UI"
FONT_SIZE = 10

# Microphone indicator states (spec section 3.2): key -> (label, dot color)
MIC_STATES = {
    "listening": ("Listening for 'Hand'", GREEN),
    "window": ("Command window open", BLUE),
    "off": ("Microphone OFF", GREY),
    "ptt": ("Listening (hold key)", GREEN),
    "ptt_idle": ("Push-to-talk: hold key to speak", GREY),
}

STYLESHEET = f"""
* {{ font-family: '{FONT_FAMILY}'; font-size: {FONT_SIZE}pt; color: {TEXT}; }}
QMainWindow, QWidget#root, QStackedWidget, QWidget#page {{ background: {BG}; }}
QWidget#sidebar {{ background: {BG_SIDEBAR}; border-right: 1px solid {BORDER}; }}
QLabel#logo {{ font-size: 15pt; font-weight: 600; color: {ACCENT}; padding: 18px 16px 12px 16px; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ padding: 11px 16px; margin: 2px 8px; border-radius: 8px; color: {TEXT_DIM}; }}
QListWidget#nav::item:hover {{ background: {BG_CARD}; color: {TEXT}; }}
QListWidget#nav::item:selected {{ background: {BG_CARD}; color: {ACCENT}; border-left: 3px solid {ACCENT}; }}
QFrame#header {{ background: {BG_SIDEBAR}; border-bottom: 1px solid {BORDER}; }}
QFrame#card {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#h1 {{ font-size: 18pt; font-weight: 600; }}
QLabel#cardTitle {{ color: {TEXT_DIM}; font-size: 9pt; }}
QLabel#cardValue {{ font-size: 13pt; font-weight: 600; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#error {{ color: {RED}; }}
QPushButton {{ background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_DARK}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px; }}
QSpinBox, QDoubleSpinBox {{ min-width: 92px; }}
QMenu {{ background: {BG_CARD}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_DARK}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
