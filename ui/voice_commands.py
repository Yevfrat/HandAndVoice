from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem,
)

from ui.page_base import Page

SPECIAL_ROWS = [
    ("<app name or alias>", "Open or focus an app, e.g. Claude, Calculator", "No"),
    ("File <name>", "Open the best matching indexed file (also: F <name>, EF <name>)", "No"),
    ("Web <alias>", "Open a website alias (also: W <alias>, Double-U <alias>); new tab if a browser is in front", "No"),
    ("Search <query>", "Google search in a new tab or the default browser", "No"),
    ("one ... nine", "Choose from a numbered list", "No"),
]


class VoiceCommandsPage(Page):
    def __init__(self, controller):
        super().__init__("Voice Commands",
                         "Say the wake word first, e.g. \"Hand, minimize\". If the app often hears "
                         "a command wrongly, teach it your pronunciation at the bottom.")
        self.controller = controller
        commands = controller.voice_commands
        rows = list(SPECIAL_ROWS) + [
            (phrase.title(), spec.get("description", ""), "Yes" if spec.get("requires_confirmation") else "No")
            for phrase, spec in commands.items()
        ]
        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(["Phrase", "Description", "Needs confirmation"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.verticalHeader().hide()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(val))
        table.resizeColumnToContents(0)
        self.root.addWidget(table, 3)

        # ---- teach a spoken variant ----
        head = QLabel("Teach a pronunciation: when the app hears  ...  it means  ...")
        head.setObjectName("dim")
        self.root.addWidget(head)
        row = QHBoxLayout()
        self.heard = QComboBox()
        self.heard.setEditable(True)
        self.heard.setMinimumWidth(260)
        self.heard.lineEdit().setPlaceholderText("what the app heard (pick from recent, or type)")
        self.command = QComboBox()
        for phrase in commands:
            self.command.addItem(phrase.title(), phrase)
        add = QPushButton("Add")
        add.clicked.connect(self._add)
        refresh = QPushButton("Refresh recent")
        refresh.clicked.connect(self.refresh_recent)
        row.addWidget(self.heard, 2)
        row.addWidget(QLabel("means"))
        row.addWidget(self.command, 1)
        row.addWidget(add)
        row.addWidget(refresh)
        self.root.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.root.addWidget(self.status)

        self.variants = QTableWidget(0, 2)
        self.variants.setHorizontalHeaderLabels(["App hears", "Means"])
        self.variants.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.variants.verticalHeader().hide()
        self.variants.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.variants.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.variants.setSelectionMode(QAbstractItemView.SingleSelection)
        self.root.addWidget(self.variants, 1)
        delete = QPushButton("Delete selected")
        delete.clicked.connect(self._delete)
        self.root.addWidget(delete)
        self.refresh_recent()
        self.reload_variants()

    def refresh_recent(self) -> None:
        current = self.heard.currentText()
        self.heard.clear()
        self.heard.addItems(list(self.controller.feedback.readings))
        self.heard.setEditText(current)

    def reload_variants(self) -> None:
        data = self.controller.load_variants()
        self.variants.setRowCount(len(data))
        for r, (heard, phrase) in enumerate(data.items()):
            self.variants.setItem(r, 0, QTableWidgetItem(heard))
            self.variants.setItem(r, 1, QTableWidgetItem(phrase))

    def _add(self) -> None:
        error = self.controller.add_variant(self.heard.currentText(), self.command.currentData())
        self.status.setText(error or "Saved. It works immediately.")
        if not error:
            self.heard.setEditText("")
            self.reload_variants()

    def _delete(self) -> None:
        row = self.variants.currentRow()
        if row >= 0:
            self.controller.delete_variant(self.variants.item(row, 0).text())
            self.reload_variants()
