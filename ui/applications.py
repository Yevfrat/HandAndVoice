from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem,
)

from services.alias_manager import APP_KINDS
from ui.page_base import Page


class AliasDialog(QDialog):
    def __init__(self, parent=None, entry: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("App entry")
        entry = entry or {"name": "", "aliases": [], "kind": "exe", "target": "", "arguments": ""}
        form = QFormLayout(self)
        self.name = QLineEdit(entry["name"])
        self.aliases = QLineEdit(", ".join(entry["aliases"]))
        self.aliases.setPlaceholderText("comma separated, e.g. Visual Studio Code, Code Editor")
        self.kind = QComboBox()
        self.kind.addItems(APP_KINDS)
        self.kind.setCurrentText(entry["kind"])
        self.target = QLineEdit(entry["target"])
        self.target.setPlaceholderText("full path to .exe, Store app id, or https:// address. Empty = auto-detect")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.target, 1)
        row.addWidget(browse)
        self.args = QLineEdit(entry["arguments"])
        form.addRow("Name (spoken)", self.name)
        form.addRow("Other spoken names", self.aliases)
        form.addRow("Kind", self.kind)
        form.addRow("Target", row)
        form.addRow("Arguments", self.args)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setMinimumWidth(560)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a program", "", "Programs (*.exe *.lnk)")
        if path:
            self.target.setText(path)
            self.kind.setCurrentText("lnk" if path.lower().endswith(".lnk") else "exe")

    def entry(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "aliases": [a.strip() for a in self.aliases.text().split(",") if a.strip()],
            "kind": self.kind.currentText(),
            "target": self.target.text().strip(),
            "arguments": self.args.text().strip(),
        }


class ApplicationsPage(Page):
    def __init__(self, controller):
        super().__init__("Applications",
                         "Say the app name after the wake word, e.g. \"Hand, Calculator\". "
                         "Names must be common English words the speech model knows.")
        self.controller = controller
        self.registry = controller.registry
        self.aliases = controller.aliases

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search discovered apps...")
        self.search.textChanged.connect(self.reload)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.controller.reload_registry)
        top.addWidget(self.search, 1)
        top.addWidget(refresh)
        self.root.addLayout(top)

        self.apps = self._table(["Name", "Kind", "Source", "Target"], 3)
        self.root.addWidget(self.apps, 3)

        self.skipped = QLabel("")
        self.skipped.setObjectName("dim")
        self.skipped.setWordWrap(True)
        self.root.addWidget(self.skipped)

        head = QLabel("My aliases (independent entries)")
        head.setObjectName("dim")
        self.root.addWidget(head)
        self.alias_table = self._table(["Name", "Other names", "Kind", "Target"], 3)
        self.root.addWidget(self.alias_table, 2)
        btns = QHBoxLayout()
        for label, fn in (("Add", self._add), ("Edit", self._edit), ("Delete", self._delete)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        self.root.addLayout(btns)
        self.reload()

    @staticmethod
    def _table(headers: list[str], stretch_col: int) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.Stretch)
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        return t

    def set_skipped(self, text: str) -> None:
        if text:
            self.skipped.setText("Not speakable (not in the speech model's vocabulary): " + text
                                 + ". Add an alias made of common English words.")
        else:
            self.skipped.setText("")

    def reload(self) -> None:
        q = self.search.text().strip().lower()
        entries = [e for e in self.registry.entries if not q or q in e.name.lower()
                   or any(q in a.lower() for a in e.aliases)]
        self.apps.setRowCount(len(entries))
        for r, e in enumerate(entries):
            for c, val in enumerate((e.name, e.kind, e.source, e.target or "(not set)")):
                self.apps.setItem(r, c, QTableWidgetItem(val))
        mine = self.aliases.load_app_aliases()
        self.alias_table.setRowCount(len(mine))
        for r, e in enumerate(mine):
            for c, val in enumerate((e["name"], ", ".join(e["aliases"]), e["kind"], e["target"] or "(auto)")):
                self.alias_table.setItem(r, c, QTableWidgetItem(val))

    def _selected_name(self) -> str | None:
        row = self.alias_table.currentRow()
        if row < 0:
            return None
        return self.alias_table.item(row, 0).text()

    def _save(self, entry: dict, replace: str | None = None) -> None:
        if not entry["name"]:
            QMessageBox.warning(self, "Missing name", "Please enter a name.")
            return
        self.aliases.upsert_app_alias(entry, replace_name=replace)
        self.controller.reload_registry()
        self.reload()

    def _add(self) -> None:
        dlg = AliasDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._save(dlg.entry())

    def _edit(self) -> None:
        name = self._selected_name()
        if not name:
            return
        current = next((e for e in self.aliases.load_app_aliases() if e["name"] == name), None)
        if current is None:
            return
        dlg = AliasDialog(self, current)
        if dlg.exec() == QDialog.Accepted:
            self._save(dlg.entry(), replace=name)

    def _delete(self) -> None:
        name = self._selected_name()
        if not name:
            return
        if QMessageBox.question(self, "Delete alias", f"Delete the alias entry '{name}'?") == QMessageBox.Yes:
            self.aliases.delete_app_alias(name)
            self.controller.reload_registry()
            self.reload()
