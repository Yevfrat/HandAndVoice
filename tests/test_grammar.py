import json

import pytest

from voice.grammar import (
    PhraseSources, build_command_phrases, build_wake_grammar, clean_phrase, validate_wake_word,
)

CMDS = ["minimize", "close all apps", "go to top", "pause", "confirm"]


def sources(**kw):
    base = dict(command_phrases=CMDS, app_names=["Claude", "Visual Studio Code"], web_aliases=["Alim"])
    base.update(kw)
    return PhraseSources(**base)


def test_wake_grammar_is_exactly_hand_and_unk():
    assert json.loads(build_wake_grammar("hand")) == ["hand", "[unk]"]
    assert json.loads(build_wake_grammar(" HAND ")) == ["hand", "[unk]"]


def test_command_grammar_contents():
    phrases = build_command_phrases(sources()).phrases
    for expected in ("minimize", "close all apps", "claude", "visual studio code",
                     "file", "f", "ef", "web", "w", "double u", "search",
                     "one", "nine", "web alim", "w alim", "double u alim", "[unk]"):
        assert expected in phrases, expected
    assert "hand" not in phrases  # the wake word is never a command phrase


def test_always_mode_prefixes_every_phrase_with_wake_word():
    phrases = build_command_phrases(sources(), wake_word="hand").phrases
    assert "hand minimize" in phrases and "hand claude" in phrases
    assert phrases[-1] == "[unk]"
    assert all(p.startswith("hand ") for p in phrases[:-1])


def test_phrases_deduplicated():
    phrases = build_command_phrases(sources(app_names=["Claude", "claude", "CLAUDE"])).phrases
    assert phrases.count("claude") == 1


def test_unspeakable_names_are_skipped_and_reported():
    res = build_command_phrases(sources(app_names=["7-Zip", "Café", "Claude"]))
    assert "claude" in res.phrases
    assert res.skipped == ["7-Zip", "Café"]


def test_vocabulary_filter():
    vocab = {"claude", "minimize", "file", "web", "alim", "w", "f", "ef", "double", "u", "search",
             "close", "all", "apps", "go", "to", "top", "pause", "confirm", "visual", "studio", "code",
             "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"}
    res = build_command_phrases(sources(app_names=["Claude", "ChatGPT"], web_aliases=["Alim", "Zzyzx"]),
                                vocabulary=vocab)
    assert "claude" in res.phrases and "chatgpt" not in res.phrases
    assert "web alim" in res.phrases and "web zzyzx" not in res.phrases
    assert set(res.skipped) == {"ChatGPT", "Zzyzx"}


def test_wake_word_is_absorbed_but_never_a_command():
    plain = build_command_phrases(sources()).phrases
    absorbed = build_command_phrases(sources(), absorb_wake_word="hand").phrases
    assert "hand" not in plain and absorbed[0] == "hand"
    validate_wake_word("hand", CMDS)                     # still not a command phrase


def test_file_words_are_only_used_after_a_prefix():
    phrases = build_command_phrases(sources(file_words=["economics", "budget"])).phrases
    assert "file economics" in phrases and "ef budget" in phrases
    assert "economics" not in phrases and "budget" not in phrases   # bare words loosen the grammar


def test_extra_phrases_for_file_names():
    res = build_command_phrases(sources(extra_phrases=["file economics"]))
    assert "file economics" in res.phrases


def test_rebuild_after_registry_change():
    before = build_command_phrases(sources()).phrases
    after = build_command_phrases(sources(app_names=["Claude", "Telegram"])).phrases
    assert "telegram" not in before and "telegram" in after


def test_wake_word_must_not_be_a_command():
    validate_wake_word("hand", CMDS)  # fine
    for bad in ("minimize", "file", "one", "", "two words"):
        with pytest.raises(ValueError):
            validate_wake_word(bad, CMDS)


def test_clean_phrase():
    assert clean_phrase("  Visual  Studio-Code ") == "visual studio code"
    assert clean_phrase("Office 365") is None
    assert clean_phrase("Ünïcode") is None
