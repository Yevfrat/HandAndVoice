"""Wires services, voice engine and dispatcher together. Qt-free: the UI calls
poll() from a QTimer and subscribes to feedback / state changes."""
from __future__ import annotations

import logging
import queue
import time
from collections import deque
from typing import Callable

from pathlib import Path
from typing import Optional

from automation.app_launcher import AppLauncher
from automation.file_manager import FileManager
from automation.media_controller import MediaController
from automation.system_controller import SHUTDOWN_DELAY_SECONDS, SystemController
from automation.system_controller import register as register_system_commands
from automation.web_controller import WebController
from automation.window_commands import WindowCommands
from automation.window_commands import register as register_window_commands
from core.command import Command, CommandType, Result
from core.confirmation import ConfirmationManager
from core.dispatcher import Dispatcher
from core.feedback import Feedback
from core.mode_manager import Mode, ModeManager
from gestures.engine import GestureEngine, GestureMessage
from gestures.face import FACE_GESTURES
from gestures.face_controller import FaceController
from gestures.gesture_controller import HELP_LINES, GestureController, GestureUI
from gestures.types import GestureStatus
from services.alias_manager import AliasManager
from services.application_registry import ApplicationRegistry
from services.config_store import ConfigStore
from services.file_indexer import FileIndexer, normalize_extensions
from services.usage_stats import UsageStats
from voice import recognizer
from voice.command_parser import CommandParser
from voice.engine import VoiceEngine
from voice.grammar import PhraseSources
from voice.hotkeys import HotkeyListener
from voice.listen_modes import VoiceEvent
from voice.text_normalizer import normalize

log = logging.getLogger(__name__)

MAX_USED_APPS_IN_GRAMMAR = 12

LISTEN_COMMANDS = {
    CommandType.LISTEN_WAKE_WORD: "WAKE_WORD",
    CommandType.LISTEN_ALWAYS: "ALWAYS",
    CommandType.LISTEN_PUSH_TO_TALK: "PUSH_TO_TALK",
}
MODE_TARGETS = {
    CommandType.MODE_VOICE_ONLY: Mode.VOICE_ONLY,
    CommandType.MODE_GESTURE_ONLY: Mode.GESTURE_ONLY,
    CommandType.MODE_HYBRID: Mode.HYBRID,
}


class AppController:
    def __init__(self, store: ConfigStore, system: Optional[SystemController] = None,
                 media: Optional[MediaController] = None, gesture_keyboard=None, gesture_wm=None):
        self.store = store
        s = store.settings
        recognizer.set_preferred(s["speech_model"])
        self.events: "queue.Queue[VoiceEvent]" = queue.Queue()
        self.feedback = Feedback()
        self.mode_manager = ModeManager(s["start_mode"])
        self.usage = UsageStats(store.data_dir / "usage.db")
        self.aliases = AliasManager(store)
        self.registry = ApplicationRegistry(self.aliases, self.usage, store)
        self.launcher = AppLauncher(self.registry, self.usage)
        self.indexer = FileIndexer(store.data_dir / "file_index.db", store)
        self.file_manager = FileManager(self.indexer)
        self.web = WebController(
            error_sink=lambda msg: self.events.put(VoiceEvent("error", msg)))
        self.voice_commands = store.load_json("voice_commands.json")
        self.parser = CommandParser(self.voice_commands, self.registry, s["wake_word"])
        self.parser.set_variants(store.load_json("voice_variants.json"))
        self.confirmation = ConfirmationManager(s["confirmation_timeout_seconds"])

        descriptions = {spec["type"]: spec.get("description", "") for spec in self.voice_commands.values()}
        executors: dict[CommandType, Callable[[Command], Result]] = {
            CommandType.LAUNCH_APP: lambda c: self.launcher.launch(c.target or ""),
            CommandType.MODE_PAUSE: self._exec_pause,
            CommandType.MODE_RESUME: self._exec_resume,
            CommandType.FILE_OPEN: self._exec_file,
            CommandType.WEB_OPEN: self._exec_web,
            CommandType.SEARCH: self._exec_search,
        }
        for ctype in MODE_TARGETS:
            executors[ctype] = self._exec_mode
        for ctype in LISTEN_COMMANDS:
            executors[ctype] = self._exec_listen
        self.window_commands = WindowCommands(voice_commands=self.voice_commands)
        register_window_commands(executors, self.window_commands)
        self.system = system or SystemController(
            notify=lambda kind, text: self.events.put(VoiceEvent(kind, text)))
        self.media = media or MediaController()
        register_system_commands(executors, self.system, self.media)
        # voice Next/Previous Window also move the selection while the gesture switcher is open
        executors[CommandType.NEXT_WINDOW] = lambda c: self._exec_step_window(c, +1)
        executors[CommandType.PREVIOUS_WINDOW] = lambda c: self._exec_step_window(c, -1)
        executors[CommandType.SLEEP] = self._exec_sleep
        executors[CommandType.SHUTDOWN] = self._exec_shutdown
        executors[CommandType.RESTART] = self._exec_restart
        preparers = {CommandType.CLOSE_WINDOW: self.window_commands.prepare_close,
                     CommandType.CLOSE_ALL_APPS: self.system.prepare_close_all}
        self.dispatcher = Dispatcher(self.parser, self.mode_manager, self.confirmation,
                                     self.feedback, executors, descriptions, preparers=preparers)

        self.engine = VoiceEngine(store, self.phrase_sources, self.events)
        # Gestures: recognized by the engine thread, turned into actions by the gesture controller
        self.gesture_events: "queue.Queue[GestureMessage]" = queue.Queue()
        self.gestures = GestureEngine(store, self.load_gestures_config, self.gesture_events)
        confirm_types = {CommandType(spec["type"]) for spec in self.voice_commands.values()
                         if spec.get("requires_confirmation")}
        self.gesture_ui = GestureUI()
        self.gesture_controller = GestureController(
            self.dispatcher, self.window_commands, self.media, self.feedback,
            self.load_gestures_config, lambda: self.store.settings, self.gestures.hold_overrides,
            confirm_types, keyboard=gesture_keyboard, wm=gesture_wm, ui=self.gesture_ui)
        # Face Only mode (manual): winks -> next / previous page. Voice and hand gestures are off then.
        self.face_controller = FaceController(self.dispatcher, self.feedback, lambda: self.store.settings,
                                              keyboard=gesture_keyboard, wm=gesture_wm)
        self.gesture_status = GestureStatus()
        self.camera_on = False
        self.recent_gesture_events: deque = deque(maxlen=12)
        self._gesture_listeners: list[Callable[[str, object], None]] = []
        self.hotkeys = HotkeyListener(
            s["push_to_talk_key"], s["resume_hotkey"],
            on_ptt_press=self.engine.ptt_press, on_ptt_release=self.engine.ptt_release,
            on_resume=lambda: self.events.put(VoiceEvent("hotkey", "resume")),
            ptt_enabled=lambda: self.store.get("listen_mode") == "PUSH_TO_TALK"
            and self.mode_manager.voice_enabled,
        )
        self.mic_state = "off"
        self._last_error: tuple = ("", 0.0)
        self.grammar_skipped = ""
        self.model_error = ""
        self._state_listeners: list[Callable[[str, str], None]] = []
        self.mode_manager.add_listener(self._on_mode_changed)

    # ---------- lifecycle ----------
    def start(self) -> None:
        self.registry.refresh_async(lambda: self.events.put(VoiceEvent("registry_ready")))
        self.indexer.start_periodic(lambda: self.events.put(VoiceEvent("index_ready")))
        self.engine.start()
        self.engine.set_enabled(self.mode_manager.voice_enabled)
        self.gestures.start()
        self._apply_gesture_state()
        try:
            self.hotkeys.start()
        except Exception:
            log.exception("Global hotkeys could not start")
            self.feedback.error("Global hotkeys unavailable", "Push-to-talk and Ctrl+Alt+R will not work")

    def shutdown(self) -> None:
        self.indexer.stop()
        try:
            self.gesture_controller.release_all()     # never leave Alt held down
        finally:
            try:
                self.hotkeys.stop()
            finally:
                self.gestures.request_stop()          # tell both threads first, then wait for them,
                self.engine.request_stop()            # so they finish in parallel (quit is faster)
                try:
                    self.gestures.shutdown()          # releases the camera
                finally:
                    self.engine.shutdown()

    # ---------- state listeners (UI) ----------
    def add_state_listener(self, fn: Callable[[str, str], None]) -> None:
        self._state_listeners.append(fn)

    def _notify(self, kind: str, value: str = "") -> None:
        for fn in list(self._state_listeners):
            try:
                fn(kind, value)
            except Exception:
                log.exception("State listener failed")

    # ---------- main loop hook ----------
    def poll(self) -> None:
        for _ in range(50):
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(ev)
        for _ in range(100):
            try:
                msg = self.gesture_events.get_nowait()
            except queue.Empty:
                break
            self._handle_gesture_message(msg)
        self.gesture_controller.tick()
        self.dispatcher.tick()

    def _handle_event(self, ev: VoiceEvent) -> None:
        if ev.kind == "mic_state":
            self.mic_state = ev.text
            self._notify("mic", ev.text)
        elif ev.kind == "command_text":
            self.feedback.heard(ev.text, ev.alt)
            self.dispatcher.handle_text(ev.text, "voice", alt=ev.alt)
        elif ev.kind == "error":
            self.model_error = ev.text
            now = time.monotonic()
            if (ev.text, ) != self._last_error[:1] or now - self._last_error[1] > 30:
                self._last_error = (ev.text, now)     # the same problem is reported once, not on every retry
                self.feedback.error(ev.text)
                self._notify("voice_error", ev.text)
        elif ev.kind == "info":
            self.feedback.info(ev.text)
        elif ev.kind == "window_timeout":
            self.feedback.info("No command understood", "Say the wake word again, then the command")
        elif ev.kind == "grammar_skipped":
            self.grammar_skipped = ev.text
            self.model_error = ""
            self._notify("grammar_skipped", ev.text)
        elif ev.kind == "hotkey" and ev.text == "resume":
            self.mode_manager.resume()
        elif ev.kind == "ptt_captured":
            self.apply_setting("push_to_talk_key", ev.text)
            self._notify("ptt_key", self.store.get("push_to_talk_key"))
        elif ev.kind == "registry_ready":
            self.engine.reload()           # rebuild grammar with the discovered apps
            self._notify("registry", str(len(self.registry)))
        elif ev.kind == "index_ready":
            self._notify("index", str(self.indexer.count()))

    # ---------- gestures ----------
    def add_gesture_listener(self, fn: Callable[[str, object], None]) -> None:
        self._gesture_listeners.append(fn)

    def load_gestures_config(self) -> dict:
        data = self.store.load_json("gestures.json")
        return data if isinstance(data, dict) else {}

    def _handle_gesture_message(self, msg: GestureMessage) -> None:
        if msg.kind == "camera":
            self.camera_on = bool(msg.payload)
            if not self.camera_on:
                self.gesture_controller.release_all()      # never leave Alt held when the camera stops
                self.gesture_status = GestureStatus()
            self._notify("camera", "on" if self.camera_on else "off")
            self._emit_gesture("status", self.gesture_status)
        elif msg.kind == "error":
            self.gesture_controller.release_all()
            self.feedback.error(str(msg.payload))
            self._notify("voice_error", str(msg.payload))
        elif msg.kind == "status":
            self.gesture_status = msg.payload
            self._emit_gesture("status", msg.payload)
        elif msg.kind == "event":
            if msg.payload.phase != "tick":                  # ticks (scroll, volume) would flood the list
                self.recent_gesture_events.appendleft(
                    (time.strftime("%H:%M:%S"), msg.payload.name, msg.payload.phase))
                self._emit_gesture("event", msg.payload)
            # Route by the CURRENT mode: a late event of the other pipeline is ignored, so a wink can
            # never reach the hand controller and a hand gesture never acts in Face Only mode.
            is_face_event = msg.payload.name in FACE_GESTURES
            if self.mode_manager.face_enabled and is_face_event:
                self.face_controller.handle(msg.payload)
            elif self.mode_manager.hands_enabled and not is_face_event:
                self.gesture_controller.handle(msg.payload)

    def _emit_gesture(self, kind: str, payload: object) -> None:
        for fn in list(self._gesture_listeners):
            try:
                fn(kind, payload)
            except Exception:
                log.exception("Gesture listener failed")

    def _apply_gesture_state(self) -> None:
        """The camera runs only while a mode needs it: hand gestures (Hybrid, Gesture Only, if enabled)
        or face expressions (Face Only, manual). Both use the same camera thread, one pipeline at a time."""
        face = self.mode_manager.face_enabled
        self.gestures.set_pipeline("face" if face else "hand")
        self.gestures.set_enabled(
            self.mode_manager.camera_enabled and (face or bool(self.store.get("gestures_enabled", True))))

    def set_face_setting(self, key: str, value) -> None:
        """Wink style, hold time, swap left/right eyes."""
        self.store.set(key, value)
        self.gestures.reload()

    def set_gestures_enabled(self, enabled: bool) -> None:
        self.store.set("gestures_enabled", bool(enabled))
        if not enabled:
            self.gesture_controller.release_all()
        self._apply_gesture_state()

    def overlay_info_lines(self) -> tuple[str, str]:
        """Title and body of the info panel that the open-palm gesture shows."""
        s = self.store.settings
        last = self.feedback.actions[0] if self.feedback.actions else None
        lines = [
            f"Mode: {self.mode_manager.mode.value.replace('_', ' ').title()}    "
            f"Listen: {s['listen_mode'].replace('_', ' ').title()}",
            f"Last command: {last.command if last else '-'}",
            f"Last result: {last.result if last else '-'}",
            "",
            *HELP_LINES,
        ]
        return "Hand&Voice", "\n".join(lines)

    def set_gesture_setting(self, key: str, value) -> None:
        self.store.set(key, value)
        self.gestures.reload()

    def set_gesture_hold(self, name: str, seconds: float) -> None:
        data = self.load_gestures_config()
        if name in data:
            data[name]["hold_seconds"] = max(0.1, min(10.0, float(seconds)))
            self.store.save_json("gestures.json", data)
            self.gestures.reload()

    def set_gesture_enabled(self, name: str, enabled: bool) -> None:
        data = self.load_gestures_config()
        if name in data:
            data[name]["enabled"] = bool(enabled)
            self.store.save_json("gestures.json", data)
            self.gestures.reload()

    # ---------- phrases for the grammar ----------
    def phrase_sources(self) -> PhraseSources:
        # Keep the grammar SMALL. With 150+ discovered app names in it, Vosk forces every
        # sound into some app name ("water", "tempo", "scale"). Only apps the user chose
        # (alias entries) or already uses are in it; every other app is found through the
        # full-vocabulary second reading plus fuzzy matching.
        apps: list[str] = []
        for entry in self.aliases.load_app_aliases():
            apps.append(entry["name"])
            apps.extend(entry["aliases"])
        for name, _count in self.usage.top(MAX_USED_APPS_IN_GRAMMAR):
            e = self.registry.get(name)
            if e is not None:
                apps.append(e.name)
        return PhraseSources(
            command_phrases=list(self.voice_commands.keys()),
            app_names=apps,
            web_aliases=list(self.aliases.load_web_aliases().keys()),
        )

    # ---------- actions used by UI and executors ----------
    def reload_registry(self) -> None:
        self.registry.refresh_async(lambda: self.events.put(VoiceEvent("registry_ready")))

    # ---------- voice variants ("how I pronounce it") ----------
    def load_variants(self) -> dict[str, str]:
        data = self.store.load_json("voice_variants.json")
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def add_variant(self, heard: str, command_phrase: str) -> str:
        """Teach that `heard` means `command_phrase`. Returns "" or an error message."""
        h, p = normalize(heard), normalize(command_phrase)
        if not h:
            return "Type or choose what the app heard"
        if h in self.parser.commands:
            return f"'{h}' is already a real command; it cannot be changed"
        if p not in self.parser.commands:
            return "Choose a command"
        variants = self.load_variants()
        variants[h] = p
        self.store.save_json("voice_variants.json", variants)
        self.parser.set_variants(variants)
        return ""

    def delete_variant(self, heard: str) -> None:
        variants = {k: v for k, v in self.load_variants().items() if normalize(k) != normalize(heard)}
        self.store.save_json("voice_variants.json", variants)
        self.parser.set_variants(variants)

    def rebuild_index(self) -> None:
        self.indexer.rebuild_async(lambda: self.events.put(VoiceEvent("index_ready")))

    def set_index_folders(self, folders: list[str]) -> None:
        self.store.set("index_folders", [str(f) for f in folders])
        self.rebuild_index()

    def set_index_extensions(self, extensions: list[str]) -> None:
        self.store.set("index_extensions", sorted(normalize_extensions(extensions)))
        self.rebuild_index()

    def retry_devices(self) -> None:
        """After plugging in a camera / microphone or fixing a model: try both again."""
        self.feedback.info("Trying the microphone and camera again...")
        self._last_error = ("", 0.0)                  # an explicit retry may report the problem again
        self.engine.reload()
        self.gestures.reload()

    def reload_grammar(self) -> None:
        """Call after web aliases change so the new names become speakable."""
        self.engine.reload()

    # ---------- settings page ----------
    VOICE_KEYS = {"command_window_seconds", "microphone_device", "speech_model"}
    GESTURE_KEYS = {"camera_index", "camera_fps", "camera_width", "camera_height", "hand",
                    "movement_tolerance", "min_hand_confidence", "stable_frames", "cooldown_seconds"}

    def apply_setting(self, key: str, value) -> None:
        """Save one setting (the store validates and clamps it) and apply it right away."""
        self.store.set(key, value)
        if key in self.VOICE_KEYS:
            self.engine.reload()
        elif key in self.GESTURE_KEYS:
            self.gestures.reload()
        elif key in ("push_to_talk_key", "resume_hotkey"):
            s = self.store.settings
            self.hotkeys.configure(s["push_to_talk_key"], s["resume_hotkey"])

    def set_wake_word(self, word: str) -> str:
        """Change the wake word. Returns "" or a message for the user."""
        from voice.grammar import validate_wake_word

        try:
            validate_wake_word(word, self.voice_commands.keys())
        except ValueError as exc:
            return str(exc)
        wake = word.strip().lower()
        vocab = recognizer.vocabulary_if_loaded()          # only if the model is already loaded
        if vocab is not None and wake not in vocab:
            return f"'{wake}' is not a word the speech model knows. Choose a common English word."
        self.store.set("wake_word", wake)
        self.parser.wake_word = wake
        self.engine.reload()
        return ""

    def list_microphones(self) -> list[tuple[int, str]]:
        try:
            from voice.microphone import list_input_devices

            return list_input_devices()
        except Exception:
            log.exception("Could not list microphones")
            return []

    def capture_ptt_key(self) -> None:
        """Settings > "Press a key": the next key press becomes the push-to-talk key."""
        self.hotkeys.capture_next(lambda kid: self.events.put(VoiceEvent("ptt_captured", kid)))

    def cancel_capture(self) -> None:
        self.hotkeys.cancel_capture()

    def apply_default_holds(self) -> None:
        """Write the default / sleep / scroll hold times from Settings into every gesture."""
        s = self.store.settings
        data = self.load_gestures_config()
        for name, cfg in data.items():
            if name == "FIST":
                cfg["hold_seconds"] = float(s["sleep_hold_seconds"])
            elif name in ("POINT_UP", "POINT_DOWN"):
                cfg["hold_seconds"] = float(s["scroll_hold_seconds"])
            else:
                cfg["hold_seconds"] = float(s["default_hold_seconds"])
        self.store.save_json("gestures.json", data)
        self.gestures.reload()

    def set_listen_mode(self, listen_mode: str) -> None:
        self.store.set("listen_mode", listen_mode)
        self.engine.reload()
        self._notify("listen_mode", self.store.get("listen_mode"))

    def _on_mode_changed(self, mode: Mode) -> None:
        self.engine.set_enabled(self.mode_manager.voice_enabled)
        if not self.mode_manager.hands_enabled:
            self.gesture_controller.release_all()          # also when switching to Face Only
        self._apply_gesture_state()
        if mode == Mode.FACE_ONLY:
            self.feedback.info(
                "Face Only mode",
                "Microphone and hand gestures are OFF. Close your RIGHT eye = next, LEFT eye = previous. "
                "Leave: tray menu, Dashboard or Ctrl+Alt+R")
        else:
            camera_on = self.mode_manager.camera_enabled and bool(self.store.get("gestures_enabled", True))
            self.feedback.info("Mode: " + mode.value.replace("_", " ").title(),
                               "Microphone " + ("on" if self.mode_manager.voice_enabled else "OFF")
                               + ", camera " + ("on" if camera_on else "OFF"))
        if mode == Mode.PAUSED:
            self.confirmation.cancel()
        self._notify("mode", mode.value)

    # ---------- executors ----------
    def _exec_step_window(self, cmd: Command, direction: int) -> Result:
        if self.gesture_controller.step_selection(direction):
            return Result.success("Next window" if direction > 0 else "Previous window")
        return self.window_commands.execute(cmd)

    def _exec_sleep(self, cmd: Command) -> Result:
        """Sleep never starts at once: a cancellable countdown runs first."""
        seconds = float(self.store.get("sleep_countdown_seconds", 3))
        self.dispatcher.start_countdown("Sleep in {s}s", seconds, action=self.system.sleep)
        return Result(True, "", show=False)

    def _exec_shutdown(self, cmd: Command) -> Result:
        return self._start_power_action(self.system.shutdown(cmd), "Shutting down in {s}s")

    def _exec_restart(self, cmd: Command) -> Result:
        return self._start_power_action(self.system.restart(cmd), "Restarting in {s}s")

    def _start_power_action(self, result: Result, label_fmt: str) -> Result:
        if result.ok:      # Windows now counts down 60 s; cancelling runs `shutdown /a`
            self.dispatcher.start_countdown(label_fmt, SHUTDOWN_DELAY_SECONDS,
                                            on_cancel=self.system.abort_shutdown,
                                            hint="Say 'Hand, cancel' to abort")
        return result

    def _exec_file(self, cmd: Command) -> Result:
        query = cmd.target or ""
        look = self.file_manager.lookup(query)
        if look.status == "none":
            return Result.error(f"No file found for '{query}'")
        if look.status == "open":
            return self.file_manager.open(look.hits[0].path)
        choices: dict[str, str] = {}
        for hit in look.hits:
            label = f"{hit.name}  ({Path(hit.path).parent.name})"
            if label in choices:
                label = f"{hit.name}  ({Path(hit.path).parent})"
            choices[label] = hit.path

        def on_pick(label: str) -> None:
            self.dispatcher.report(cmd.source, cmd.label(), self.file_manager.open(choices[label]))

        self.dispatcher.open_picker(f"Several files match '{query}'", list(choices), on_pick)
        return Result(True, "", show=False)

    def _exec_web(self, cmd: Command) -> Result:
        return self.web.open_alias(cmd.target or "", self.aliases.load_web_aliases())

    def _exec_search(self, cmd: Command) -> Result:
        return self.web.search(cmd.target or "")

    def _exec_pause(self, cmd: Command) -> Result:
        self.mode_manager.pause()
        return Result.success("Paused. Use the tray, the UI or Ctrl+Alt+R to resume")

    def _exec_resume(self, cmd: Command) -> Result:
        self.mode_manager.resume()
        return Result.success(f"Mode: {self.mode_manager.mode.value.replace('_', ' ').title()}")

    def _exec_mode(self, cmd: Command) -> Result:
        self.mode_manager.set_mode(MODE_TARGETS[cmd.type])
        extra = ""
        if cmd.type == CommandType.MODE_GESTURE_ONLY:
            extra = " (microphone closed; change mode from the tray or UI)"
        return Result.success(f"Mode: {self.mode_manager.mode.value.replace('_', ' ').title()}{extra}")

    def _exec_listen(self, cmd: Command) -> Result:
        self.set_listen_mode(LISTEN_COMMANDS[cmd.type])
        return Result.success(f"Listen mode: {LISTEN_COMMANDS[cmd.type].replace('_', ' ').title()}")
