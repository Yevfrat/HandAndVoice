from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem,
)

from automation.web_controller import validate_url
from services.file_indexer import BLOCKED_EXTENSIONS, normalize_extensions
from ui.page_base import Page


class WebAliasDialog(QDialog):
    def __init__(self, parent=None, alias: str = "", url: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Website alias")
        form = QFormLayout(self)
        self.alias = QLineEdit(alias)
        self.alias.setPlaceholderText("spoken name, e.g. Alim")
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://...")
        form.addRow("Spoken name", self.alias)
        form.addRow("Address (https only)", self.url)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setMinimumWidth(480)

    def _check(self) -> None:
        if not self.alias.text().strip():
            QMessageBox.warning(self, "Missing name", "Please enter a spoken name.")
            return
        try:
            validate_url(self.url.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid address", str(exc))
            return
        self.accept()


class FilesWebsitesPage(Page):
    def __init__(self, controller):
        super().__init__("Files & Websites",
                         "Say \"Hand, File <name>\", \"Hand, Web <alias>\" or \"Hand, Search <words>\". "
                         "Only file names are indexed, never contents.")
        self.controller = controller
        self.store = controller.store
        self.aliases = controller.aliases

        # ---- indexed folders ----
        self.root.addWidget(self._dim("Indexed folders (names like Documents, or full paths)"))
        self.folders = QListWidget()
        self.folders.setMaximumHeight(110)
        self.root.addWidget(self.folders)
        row = QHBoxLayout()
        for label, fn in (("Add folder...", self._add_folder), ("Remove selected", self._remove_folder),
                          ("Rebuild index", self.controller.rebuild_index)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        self.index_status = QLabel("")
        self.index_status.setObjectName("dim")
        row.addWidget(self.index_status, 1)
        self.root.addLayout(row)

        # ---- extensions ----
        self.root.addWidget(self._dim(
            "File extensions to index (programs and scripts are never indexed: "
            + ", ".join(sorted(BLOCKED_EXTENSIONS)[:6]) + "...)"))
        ext_row = QHBoxLayout()
        self.extensions = QLineEdit()
        save_ext = QPushButton("Save extensions")
        save_ext.clicked.connect(self._save_extensions)
        ext_row.addWidget(self.extensions, 1)
        ext_row.addWidget(save_ext)
        self.root.addLayout(ext_row)

        # ---- web aliases ----
        self.root.addWidget(self._dim("Website aliases"))
        self.web_table = QTableWidget(0, 2)
        self.web_table.setHorizontalHeaderLabels(["Spoken name", "Address"])
        self.web_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.web_table.verticalHeader().hide()
        self.web_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.web_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.web_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.root.addWidget(self.web_table, 1)
        wrow = QHBoxLayout()
        for label, fn in (("Add", self._add_web), ("Edit", self._edit_web), ("Delete", self._delete_web)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            wrow.addWidget(b)
        wrow.addStretch(1)
        self.root.addLayout(wrow)
        self.reload()

    @staticmethod
    def _dim(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("dim")
        lab.setWordWrap(True)
        return lab

    def set_index_count(self, value: str) -> None:
        self.index_status.setText(f"{value} files indexed")

    def reload(self) -> None:
        s = self.store.settings
        self.folders.clear()
        self.folders.addItems(s["index_folders"])
        self.extensions.setText(", ".join(s["index_extensions"]))
        data = self.aliases.load_web_aliases()
        self.web_table.setRowCount(len(data))
        for r, (alias, url) in enumerate(data.items()):
            self.web_table.setItem(r, 0, QTableWidgetItem(alias))
            self.web_table.setItem(r, 1, QTableWidgetItem(url))

    # ---- folders / extensions ----
    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to index")
        if path:
            folders = list(self.store.get("index_folders"))
            if path not in folders:
                folders.append(path)
                self.controller.set_index_folders(folders)
                self.reload()

    def _remove_folder(self) -> None:
        item = self.folders.currentItem()
        if item:
            folders = [f for f in self.store.get("index_folders") if f != item.text()]
            self.controller.set_index_folders(folders)
            self.reload()

    def _save_extensions(self) -> None:
        exts = [e for e in self.extensions.text().replace(";", ",").split(",")]
        self.controller.set_index_extensions(exts)
        self.reload()

    # ---- web aliases ----
    def _selected_alias(self) -> str | None:
        row = self.web_table.currentRow()
        return self.web_table.item(row, 0).text() if row >= 0 else None

    def _save_web(self, alias: str, url: str, replace: str | None = None) -> None:
        data = self.aliases.load_web_aliases()
        if replace and replace in data:
            del data[replace]
        data[alias.strip()] = validate_url(url)
        self.aliases.save_web_aliases(data)
        self.controller.reload_grammar()
        self.reload()

    def _add_web(self) -> None:
        dlg = WebAliasDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._save_web(dlg.alias.text(), dlg.url.text())

    def _edit_web(self) -> None:
        alias = self._selected_alias()
        if not alias:
            return
        dlg = WebAliasDialog(self, alias, self.aliases.load_web_aliases().get(alias, ""))
        if dlg.exec() == QDialog.Accepted:
            self._save_web(dlg.alias.text(), dlg.url.text(), replace=alias)

    def _delete_web(self) -> None:
        alias = self._selected_alias()
        if alias and QMessageBox.question(self, "Delete", f"Delete the website alias '{alias}'?") == QMessageBox.Yes:
            data = self.aliases.load_web_aliases()
            data.pop(alias, None)
            self.aliases.save_web_aliases(data)
            self.controller.reload_grammar()
            self.reload()
