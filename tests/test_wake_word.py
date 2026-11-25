"""Wake word + listen mode logic with scripted fake recognizers (no audio, no Vosk)."""
from pathlib import Path

import pytest

from voice.listen_modes import AlwaysMode, PushToTalkMode, WakeWordMode
from voice.wake_word import WakeWordDetector


class FakeRec:
    """Maps an audio block (bytes) to (final_text_or_None, partial_text)."""

    def __init__(self, script=None, final_text=""):
        self.script = script or {}
        self.final_text = final_text
        self._partial = ""
        self.resets = 0

    def feed(self, data):
        final, partial = self.script.get(data, (None, ""))
        self._partial = partial
        return final

    def partial(self):
        return self._partial

    def finalize(self):
        return self.final_text

    def reset(self):
        self.resets += 1
        self._partial = ""


# ---------------- detector ----------------
def test_detector_fires_on_partial_and_final():
    rec = FakeRec({b"a": (None, "hand"), b"b": ("hand [unk]", ""), b"c": (None, "[unk]")})
    d = WakeWordDetector(rec, "hand")
    assert d.feed(b"a") is True
    assert d.feed(b"b") is True
    assert d.feed(b"c") is False
    assert rec.resets == 2          # reset after each hit so it cannot retrigger


def test_detector_ignores_the_wake_word_in_the_middle_of_other_speech():
    rec = FakeRec({b"a": (None, "[unk] hand"), b"b": ("[unk] hand [unk]", ""), b"c": (None, "[unk] [unk] hand"),
                   b"d": (None, "hand [unk]")})
    d = WakeWordDetector(rec, "hand")
    assert not d.feed(b"a") and not d.feed(b"b") and not d.feed(b"c")
    assert d.feed(b"d") is True                              # "Hand, <something>" said in one breath still works


def test_detector_needs_the_whole_word():
    rec = FakeRec({b"a": (None, "handle"), b"b": (None, "handy man")})
    d = WakeWordDetector(rec, "hand")
    assert not d.feed(b"a") and not d.feed(b"b")


# ---------------- WAKE_WORD mode ----------------
def make_wake():
    wake = FakeRec({b"HAND": (None, "hand"), b"HAND_CMD": (None, "hand")})
    cmd = FakeRec({b"CMD": ("minimize", ""), b"JUNK": ("[unk]", ""), b"HAND": ("[unk]", "")},
                  final_text="")
    return WakeWordMode(wake, cmd, "hand", window_seconds=5.0), wake, cmd


def test_speech_without_wake_word_does_nothing():
    mode, _, _ = make_wake()
    for t in range(20):
        assert mode.process(b"CMD", float(t)) == []       # a command word alone is ignored
    assert mode.mic_state == "listening"


def test_wake_word_opens_window_then_command_closes_it():
    mode, wake, cmd = make_wake()
    assert mode.process(b"HAND", 0.0) == []                # replay of "hand" audio gives [unk] only
    assert mode.window_open and mode.mic_state == "window"
    events = mode.process(b"CMD", 1.0)
    assert [(e.kind, e.text) for e in events] == [("command_text", "minimize")]
    assert not mode.window_open and mode.mic_state == "listening"


def test_window_closes_after_five_seconds_of_silence():
    mode, _, _ = make_wake()
    mode.process(b"HAND", 0.0)
    assert mode.process(b"SIL", 4.9) == [] and mode.window_open
    events = mode.process(b"SIL", 5.1)
    assert [e.kind for e in events] == ["window_timeout"] and not mode.window_open   # user is told


def test_text_spoken_just_before_the_window_closes_is_still_used():
    mode, _, cmd = make_wake()
    cmd.final_text = "maximize"
    mode.process(b"HAND", 0.0)
    events = mode.process(b"SIL", 5.5)
    assert [e.text for e in events] == ["maximize"]


def test_command_in_the_same_breath_as_wake_word_is_not_lost():
    wake = FakeRec({b"B2": (None, "hand")})
    cmd = FakeRec({b"B1": (None, ""), b"B2": ("claude", "")})
    mode = WakeWordMode(wake, cmd, "hand", 5.0)
    mode.process(b"B1", 0.0)                     # speech starts, wake word not recognized yet
    events = mode.process(b"B2", 0.25)           # wake word detected; B1+B2 replayed to the command rec
    assert [e.text for e in events] == ["claude"]


def test_unk_only_final_keeps_window_open():
    mode, _, _ = make_wake()
    mode.process(b"HAND", 0.0)
    assert mode.process(b"JUNK", 1.0) == []
    assert mode.window_open
    assert [e.text for e in mode.process(b"CMD", 2.0)] == ["minimize"]


def test_wake_word_alone_does_not_close_the_window():
    """"Hand" ... pause ... "minimize": the grammar hears "hand" first, the window must stay open."""
    wake = FakeRec({b"H": (None, "hand")})
    cmd = FakeRec({b"H": ("hand", ""), b"M": ("minimize", "")})
    mode = WakeWordMode(wake, cmd, "hand", 5.0)
    assert mode.process(b"H", 0.0) == [] and mode.window_open
    assert [e.text for e in mode.process(b"M", 1.0)] == ["minimize"]


def test_wake_word_prefix_is_removed_from_command_text():
    wake = FakeRec({b"H": (None, "hand")})
    cmd = FakeRec({b"H": ("hand next window", "")})
    mode = WakeWordMode(wake, cmd, "hand", 5.0)
    assert [e.text for e in mode.process(b"H", 0.0)] == ["next window"]


class PartialRec(FakeRec):
    """Never reports an utterance end by itself (like Vosk 0.3.x sometimes); only partial text."""

    def __init__(self, partials, final_text=""):
        super().__init__({}, final_text)
        self.seq = list(partials)

    def feed(self, data):
        self._partial = self.seq.pop(0) if self.seq else self._partial
        return None


def test_phrase_end_is_detected_when_the_text_stops_changing():
    wake = FakeRec({b"W": (None, "hand")})
    cmd = PartialRec(["hand", "hand mini", "hand minimize", "hand minimize", "hand minimize",
                      "hand minimize", "hand minimize"], final_text="hand minimize")
    mode = WakeWordMode(wake, cmd, "hand", 5.0)
    out = []
    t = 0.0
    for block in (b"W", b"b", b"b", b"b", b"b", b"b", b"b", b"b"):
        out += mode.process(block, t)
        t += 0.25
    assert [e.text for e in out] == ["minimize"]        # about 1 s after the last change, not 5 s
    assert t < 5 and not mode.window_open


def test_noise_or_wake_word_alone_never_ends_the_window_early():
    wake = FakeRec({b"W": (None, "hand")})
    cmd = PartialRec(["hand"] * 12 + ["[unk]"] * 6)
    mode = WakeWordMode(wake, cmd, "hand", 20.0)
    out = []
    for i, block in enumerate([b"W"] + [b"b"] * 17):
        out += mode.process(block, i * 0.25)
    assert out == [] and mode.window_open


def test_always_mode_ends_phrase_without_endpointer():
    rec = PartialRec(["hand", "hand next", "hand next window", "hand next window", "hand next window",
                      "hand next window", "hand next window"], final_text="hand next window")
    mode = AlwaysMode(rec, "hand")
    out = []
    for i in range(8):
        out += mode.process(b"b", i * 0.25)
    assert [e.text for e in out] == ["next window"]


def test_alternative_reading_is_passed_on_when_grammar_misses():
    cmd = FakeRec({b"2": ("[unk] [unk]", "")})
    wake = FakeRec({b"1": (None, "hand")})
    open_rec = FakeRec(final_text="hand paint")
    mode = WakeWordMode(wake, cmd, "hand", 5.0, open_recognizer=open_rec, known={"minimize"})
    mode.process(b"1", 0.0)
    events = mode.process(b"2", 0.5)
    assert [(e.text, e.alt) for e in events] == [("", "paint")]


def test_exact_grammar_phrase_skips_the_second_pass():
    cmd = FakeRec({b"2": ("minimize", "")})
    wake = FakeRec({b"1": (None, "hand")})
    open_rec = FakeRec(final_text="hand something else")
    mode = WakeWordMode(wake, cmd, "hand", 5.0, open_recognizer=open_rec, known={"minimize"})
    mode.process(b"1", 0.0)
    events = mode.process(b"2", 0.5)
    assert [(e.text, e.alt) for e in events] == [("minimize", "")] and open_rec.resets == 0


# ---------------- ALWAYS mode ----------------
def test_always_mode_accepts_only_wake_word_utterances():
    rec = FakeRec({
        b"1": ("hand minimize", ""), b"2": ("minimize", ""), b"3": ("telegram is nice", ""),
        b"4": ("[unk] hand claude", ""), b"5": ("hand", ""), b"6": ("hand [unk] go to top", ""),
    })
    mode = AlwaysMode(rec, "hand")
    assert [e.text for e in mode.process(b"1", 0)] == ["minimize"]
    assert mode.process(b"2", 0) == []
    assert mode.process(b"3", 0) == []
    assert mode.process(b"5", 0) == []
    assert [e.text for e in mode.process(b"6", 0)] == ["go to top"]
    assert mode.process(b"4", 0) == []          # wake word must START the utterance


# ---------------- PUSH_TO_TALK mode ----------------
def test_push_to_talk_collects_text_until_release():
    rec = FakeRec({b"1": ("claude", "")}, final_text="")
    mode = PushToTalkMode(rec)
    assert not mode.wants_mic and mode.mic_state == "ptt_idle"
    mode.press()
    assert mode.wants_mic and mode.mic_state == "ptt"
    assert mode.process(b"1", 0) == []
    mode.release()
    assert [e.text for e in mode.finish()] == ["claude"]
    assert mode.finish() == []


def test_push_to_talk_uses_final_result_on_release():
    rec = FakeRec(final_text="minimize")
    mode = PushToTalkMode(rec)
    mode.press()
    mode.release()
    assert [e.text for e in mode.finish()] == ["minimize"]


# ---------------- open-vocabulary second pass (file / search free text) ----------------
def test_search_text_is_redecoded_with_open_vocabulary():
    cmd = FakeRec({b"1": (None, ""), b"2": ("search [unk] [unk] [unk]", "")})
    open_rec = FakeRec(final_text="hand search weather in london")
    wake = FakeRec({b"1": (None, "hand")})
    mode = WakeWordMode(wake, cmd, "hand", 5.0, open_recognizer=open_rec)
    mode.process(b"1", 0.0)
    events = mode.process(b"2", 0.5)
    assert [e.text for e in events] == ["search weather in london"]


def test_open_vocab_not_used_when_grammar_understood():
    open_rec = FakeRec(final_text="should not be used")
    cmd = FakeRec({b"2": ("file economics", "")})
    wake = FakeRec({b"1": (None, "hand")})
    mode = WakeWordMode(wake, cmd, "hand", 5.0, open_recognizer=open_rec)
    mode.process(b"1", 0.0)
    assert [e.text for e in mode.process(b"2", 0.5)] == ["file economics"]
    assert open_rec.resets == 0


def test_open_vocab_failure_falls_back_to_grammar_text():
    cmd = FakeRec({b"2": ("file [unk] [unk]", "")})
    open_rec = FakeRec(final_text="nothing useful")       # prefix word not heard
    mode = AlwaysMode(FakeRec({b"2": ("hand file [unk]", "")}), "hand", open_rec)
    assert [e.text for e in mode.process(b"2", 0)] == ["file"]
    assert cmd is not None


def test_always_mode_open_vocab():
    always = AlwaysMode(FakeRec({b"1": ("hand search [unk] [unk]", "")}), "hand",
                        FakeRec(final_text="hand search red cars"))
    assert [e.text for e in always.process(b"1", 0)] == ["search red cars"]


def test_push_to_talk_open_vocab():
    rec = FakeRec({}, final_text="file [unk]")
    mode = PushToTalkMode(rec, FakeRec(final_text="file budget report"))
    mode.press()
    mode.process(b"x", 0)
    mode.release()
    assert [e.text for e in mode.finish()] == ["file budget report"]


# ---------------- optional real-audio test (you record the WAVs) ----------------
AUDIO = Path(__file__).parent / "fixtures" / "audio"


@pytest.mark.skipif(not (AUDIO / "hand.wav").exists(), reason="record tests/fixtures/audio/hand.wav")
def test_real_wav_wake_word():
    import wave

    from voice.recognizer import SpeechRecognizer, model_installed
    from voice.grammar import build_wake_grammar

    if not model_installed():
        pytest.skip("Vosk model not installed")
    def run(path):
        rec = SpeechRecognizer(build_wake_grammar("hand"))
        det = WakeWordDetector(rec, "hand")
        with wave.open(str(path), "rb") as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1
            while True:
                data = w.readframes(4000)
                if not data:
                    return False
                if det.feed(data):
                    return True
    assert run(AUDIO / "hand.wav")
    other = AUDIO / "background.wav"
    if other.exists():
        assert not run(other)
