import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.command import CommandType
from voice.command_parser import CommandParser, FuzzyResult
from voice.text_normalizer import normalize, strip_unk

COMMANDS = json.loads((Path(__file__).parent.parent / "config" / "voice_commands.json").read_text())


class FakeResolver:
    def __init__(self, exact=None, fuzzy=None):
        self.exact = {normalize(k): SimpleNamespace(name=v) for k, v in (exact or {}).items()}
        self.fuzzy = fuzzy or FuzzyResult("none")

    def find_exact(self, text):
        return self.exact.get(normalize(text))

    def find_fuzzy(self, text):
        return self.fuzzy


@pytest.fixture
def parser():
    resolver = FakeResolver(exact={"Claude": "Claude", "File Explorer": "File Explorer",
                                   "VS Code": "VS Code", "Visual Studio Code": "VS Code"})
    return CommandParser(COMMANDS, resolver, "hand")


def ctype(res):
    assert res.kind == "command", res
    return res.command.type


def test_exact_commands(parser):
    assert ctype(parser.parse("minimize")) == CommandType.MINIMIZE
    assert ctype(parser.parse("Go To Top")) == CommandType.GO_TO_TOP
    assert ctype(parser.parse("  CLOSE ALL APPS ")) == CommandType.CLOSE_ALL_APPS


def test_wake_word_is_removed(parser):
    assert ctype(parser.parse("hand minimize")) == CommandType.MINIMIZE


def test_dangerous_commands_require_confirmation(parser):
    for phrase in ("shut down", "restart", "close all apps", "close window"):
        assert parser.parse(phrase).command.requires_confirmation, phrase
    assert not parser.parse("minimize").command.requires_confirmation


def test_command_fields(parser):
    cmd = parser.parse("minimize", mode="VOICE_ONLY", confidence=0.8).command
    assert (cmd.source, cmd.mode, cmd.confidence, cmd.raw) == ("voice", "VOICE_ONLY", 0.8, "minimize")


def test_exact_app_before_prefix(parser):
    res = parser.parse("file explorer")
    assert ctype(res) == CommandType.LAUNCH_APP
    assert res.command.target == "File Explorer"


def test_app_alias_launches_canonical_name(parser):
    assert parser.parse("visual studio code").command.target == "VS Code"


@pytest.mark.parametrize("text,prefix_type,arg", [
    ("file economics", CommandType.FILE_OPEN, "economics"),
    ("f economics", CommandType.FILE_OPEN, "economics"),
    ("ef economics", CommandType.FILE_OPEN, "economics"),
    ("web alim", CommandType.WEB_OPEN, "alim"),
    ("w alim", CommandType.WEB_OPEN, "alim"),
    ("double u alim", CommandType.WEB_OPEN, "alim"),
    ("double-u alim", CommandType.WEB_OPEN, "alim"),
    ("search weather in london", CommandType.SEARCH, "weather in london"),
])
def test_prefixes(parser, text, prefix_type, arg):
    res = parser.parse(text)
    assert ctype(res) == prefix_type
    assert res.command.target == arg


def test_prefix_without_argument_is_error(parser):
    assert parser.parse("file").kind == "error"
    assert parser.parse("search").kind == "error"


def test_number_words_become_digits(parser):
    assert parser.parse("file chapter one").command.target == "chapter 1"


@pytest.mark.parametrize("text", ["next", "close", "desktop", "hand", "banana", "pause the media"])
def test_ambiguous_or_unknown_is_not_executed(parser, text):
    res = parser.parse(text)
    assert res.kind in ("error", "ignored")
    assert res.command is None


def test_unrecognized_message(parser):
    res = parser.parse("banana split")
    assert res.kind == "error"
    assert res.message == "Command not recognized: banana split"


def test_pause_and_pause_media_are_distinct(parser):
    assert ctype(parser.parse("pause")) == CommandType.MODE_PAUSE
    assert ctype(parser.parse("pause media")) == CommandType.PAUSE_MEDIA
    assert ctype(parser.parse("play media")) == CommandType.PLAY_MEDIA


def test_confirmation_words_only_while_pending(parser):
    for word in ("confirm", "cancel", "escape"):
        assert parser.parse(word).kind == "error"
        assert parser.parse(word, confirmation_pending=True).command.type == CommandType[word.upper()]


def test_cancel_works_for_open_picker(parser):
    assert parser.parse("cancel", picker_active=True).command.type == CommandType.CANCEL
    assert parser.parse("confirm", picker_active=True).kind == "error"


def test_numbers_only_with_picker(parser):
    assert parser.parse("two").kind == "error"
    res = parser.parse("hand two", picker_active=True)
    assert (res.command.type, res.command.target) == (CommandType.PICK_OPTION, "2")


def test_unk_is_stripped_and_empty_ignored(parser):
    assert ctype(parser.parse("[unk] minimize [unk]")) == CommandType.MINIMIZE
    assert parser.parse("[unk]").kind == "ignored"
    assert parser.parse("").kind == "ignored"
    assert parser.parse("hand").kind == "ignored"


def test_fuzzy_single_launches():
    app = SimpleNamespace(name="Calculator")
    p = CommandParser(COMMANDS, FakeResolver(fuzzy=FuzzyResult("single", [app])))
    res = p.parse("calculater")
    assert res.command.type == CommandType.LAUNCH_APP and res.command.target == "Calculator"


def test_fuzzy_ambiguous_opens_picker_and_never_guesses():
    apps = [SimpleNamespace(name="Code"), SimpleNamespace(name="Code Editor")]
    p = CommandParser(COMMANDS, FakeResolver(fuzzy=FuzzyResult("ambiguous", apps)))
    res = p.parse("code")
    assert res.kind == "picker"
    assert res.candidates == ["Code", "Code Editor"]
    assert res.command is None


def test_taught_variants_map_heard_text_to_a_command(parser):
    parser.set_variants({"Mini Meter": "minimize", "mu to": "mute", "pause": "mute", "x": "no such command"})
    assert ctype(parser.parse("hand mini meter")) == CommandType.MINIMIZE
    assert ctype(parser.parse("mu to")) == CommandType.MUTE
    assert ctype(parser.parse("pause")) == CommandType.MODE_PAUSE       # real phrases cannot be overridden
    assert parser.parse("x").kind == "error"                              # variants must hit a real command
    parser.set_variants({})
    assert parser.parse("mini meter").kind == "error"


def test_every_voice_command_type_exists_in_enum():
    for phrase, spec in COMMANDS.items():
        assert spec["type"] in CommandType.__members__, phrase


def test_normalizer():
    assert normalize("Double-U  Alim!") == "double u alim"
    assert strip_unk("Hand [unk] Minimize") == "hand minimize"
