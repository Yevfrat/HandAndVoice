"""Shared helpers for UI pages."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class Page(QWidget):
    """Base page: a title, a subtitle and a vertical body layout."""

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.setObjectName("page")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 24, 28, 24)
        self.root.setSpacing(14)
        h = QLabel(title)
        h.setObjectName("h1")
        self.root.addWidget(h)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("dim")
            s.setWordWrap(True)
            self.root.addWidget(s)


class PlaceholderPage(Page):
    """Page whose content arrives in a later build phase."""

    def __init__(self, title: str, subtitle: str, phase: int):
        super().__init__(title, subtitle)
        note = QLabel(f"This page is built in Phase {phase}.")
        note.setObjectName("dim")
        self.root.addWidget(note)
        self.root.addStretch(1)


def make_card(title: str, value: str = "-") -> tuple[QFrame, QLabel]:
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 12, 16, 12)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    v = QLabel(value)
    v.setObjectName("cardValue")
    lay.addWidget(t)
    lay.addWidget(v)
    return card, v
