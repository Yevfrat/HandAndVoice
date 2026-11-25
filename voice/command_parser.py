"""Turns recognized text into a Command. Order (spec 3.4):

1. exact command phrase  2. exact app name/alias  3. prefix (file/web/search)
4. fuzzy app match (picker when close)  5. otherwise: not recognized.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from core.command import Command, CommandType
from voice.text_normalizer import normalize, strip_wake_word

CONFIRMATION_TYPES = {CommandType.CONFIRM, CommandType.CANCEL, CommandType.ESCAPE}

PREFIX_TYPES = {
    "file": CommandType.FILE_OPEN, "f": CommandType.FILE_OPEN, "ef": CommandType.FILE_OPEN,
    "web": CommandType.WEB_OPEN, "w": CommandType.WEB_OPEN,
    "search": CommandType.SEARCH,
}


class AppLike(Protocol):
    name: str


@dataclass
class FuzzyResult:
    status: str                                   # "none" | "single" | "ambiguous"
    entries: list = field(default_factory=list)   # AppEntry-like objects (need .name)


class AppResolver(Protocol):
    def find_exact(self, text: str) -> Optional[AppLike]: ...
    def find_fuzzy(self, text: str) -> FuzzyResult: ...


@dataclass
class ParseResult:
    kind: str                                     # command | picker | ignored | error
    command: Optional[Command] = None
    message: str = ""
    candidates: list[str] = field(default_factory=list)


class CommandParser:
    def __init__(self, voice_commands: dict, resolver: AppResolver, wake_word: str = "hand"):
        self.commands = {normalize(k): v for k, v in voice_commands.items()}
        self.resolver = resolver
        self.wake_word = wake_word
        self.variants: dict[str, str] = {}

    def set_variants(self, variants: dict[str, str]) -> None:
        """How THIS user's pronunciation is heard ("mini meter" -> "minimize").
        Only maps onto real command phrases and never overrides a real phrase."""
        clean = {}
        for heard, phrase in variants.items():
            h, p = normalize(heard), normalize(phrase)
            if h and h not in self.commands and p in self.commands:
                clean[h] = p
        self.variants = clean

    def _make(self, ctype: CommandType, target: Optional[str], raw: str, mode: str,
              source: str, confidence: float, confirm: bool = False) -> Command:
        return Command(type=ctype, target=target, source=source, confidence=confidence,
                       mode=mode, requires_confirmation=confirm, raw=raw)

    def parse(self, text: str, *, mode: str = "HYBRID", source: str = "voice",
              confidence: float = 1.0, confirmation_pending: bool = False,
              picker_active: bool = False) -> ParseResult:
        t = strip_wake_word(normalize(text), self.wake_word)
        if not t:
            return ParseResult("ignored")

        def make(ctype, target=None, confirm=False):
            return self._make(ctype, target, t, mode, source, confidence, confirm)

        # numbers choose from a pending picker
        if len(t) == 1 and t in "123456789":
            if picker_active:
                return ParseResult("command", make(CommandType.PICK_OPTION, t))
            return ParseResult("error", message=f"Command not recognized: {t}")

        # 1. exact command phrase (after the user's own taught variants)
        t = self.variants.get(t, t)
        spec = self.commands.get(t)
        if spec is not None:
            ctype = CommandType(spec["type"])
            if ctype in CONFIRMATION_TYPES:
                pending = confirmation_pending or (picker_active and ctype != CommandType.CONFIRM)
                if not pending:
                    return ParseResult("error", message=f"Nothing to {t} right now")
            return ParseResult("command", make(ctype, None, bool(spec.get("requires_confirmation"))))

        # 2. exact app name / alias (before prefixes: "File Explorer" is an app)
        app = self.resolver.find_exact(t)
        if app is not None:
            return ParseResult("command", make(CommandType.LAUNCH_APP, app.name))

        # 3. prefixes
        tokens = t.split()
        ctype: Optional[CommandType] = None
        rest: list[str] = []
        if tokens[:2] == ["double", "u"]:
            ctype, rest = CommandType.WEB_OPEN, tokens[2:]
        elif tokens[0] in PREFIX_TYPES:
            ctype, rest = PREFIX_TYPES[tokens[0]], tokens[1:]
        if ctype is not None:
            if not rest:
                return ParseResult("error", message=f"Say a name after '{tokens[0]}'")
            return ParseResult("command", make(ctype, " ".join(rest)))

        # 4. fuzzy app match; never guess when the top candidates are close
        fuzzy = self.resolver.find_fuzzy(t)
        if fuzzy.status == "single":
            return ParseResult("command", make(CommandType.LAUNCH_APP, fuzzy.entries[0].name))
        if fuzzy.status == "ambiguous":
            return ParseResult("picker", message=f"Several apps match '{t}'",
                               candidates=[e.name for e in fuzzy.entries])

        # 5. nothing matched
        return ParseResult("error", message=f"Command not recognized: {t}")
