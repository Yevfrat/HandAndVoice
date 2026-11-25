# Hand&Voice

Control your Windows PC with voice commands and hand gestures from a normal webcam.
Voice gives the commands, hands do the navigation. Everything runs offline, on the CPU.

> **Build status: Phase 10 of 10 (complete).** Voice, gestures, apps, files, websites, window and
> system commands, the four modes (Voice Only, Gesture Only, Hybrid, Paused), tray menu, Ctrl+Alt+R
> resume, privacy indicators, the complete Settings page, and the performance / stability pass.

## Requirements
- Windows 10 or 11
- Python **3.11, 64-bit**
- A microphone (voice) and a webcam (gestures, from Phase 7)

## Install (step by step)
Open **PowerShell** in the project folder:

```powershell
cd C:\HandAndVoice
```

> **Keep this folder OUT of OneDrive** (`C:\HandAndVoice` is not synced). The `venv` folder alone has about
> 16 000 files and 1.3 GB; OneDrive tries to sync every one of them and slows Explorer and other files down.

1. Create a virtual environment: `py -3.11 -m venv venv`
2. Install the packages: `.\venv\Scripts\python.exe -m pip install -r requirements.txt`
3. Download the offline models (about 50 MB, needs internet once):
   `.\venv\Scripts\python.exe scripts/download_models.py`
4. Run the app: `.\venv\Scripts\python.exe main.py`, or simply **double-click `start.bat`**
   (if something goes wrong the window stays open and shows the error).
   Closing the window only hides the app in the tray; to start a fresh copy, right-click the tray
   icon and choose **Quit** first.

(Using `.\venv\Scripts\python.exe` directly means you never need to activate the venv, so the
PowerShell "scripts are disabled" error cannot happen.)

Run the tests: `.\venv\Scripts\python.exe -m pytest`

## How voice works
The microphone listens only while a voice mode is on (Voice Only or Hybrid). Pause and Gesture
Only close the microphone completely.

| Listen mode | What you do |
|---|---|
| **Wake word** (default) | Say **"Hand"**, then the command within 5 seconds: *"Hand, Calculator"*. You can say it in one breath. |
| **Always** | Every command must start with "Hand". Speech without it is silently discarded. |
| **Push to talk** | Hold **Right Ctrl** and speak the command (no wake word). The microphone is open only while the key is held. |

Change the listen mode in Settings, from the tray, or by voice ("Hand, Push To Talk").
Wake-word commands need the wake word too for confirmation: *"Hand, confirm"*.

**Windows shows its microphone-in-use indicator while Hand&Voice listens. That is expected.**
Audio is processed in memory only and never saved.

### Commands available now
- `<app name or alias>`: opens the app, or switches to it if already running. Say *"Hand, Calculator"*.
  If two apps match closely you get a numbered list: say *"Hand, one"* / *"Hand, two"* or click it on the Dashboard.
- `File <name>` (also `F <name>`, `EF <name>`): opens the best matching indexed file, e.g.
  *"Hand, File Economics"*. If several match closely you get a numbered list (max 9).
- `Web <alias>` (also `W <alias>`, `Double-U <alias>`): opens a website alias, e.g. *"Hand, Web Alim"*.
  If a browser (Chrome, Edge, Firefox, Brave, Opera) is in front, it opens in a **new tab**
  (the app types the address with Ctrl+T, Ctrl+L, text, Enter); otherwise in your default browser.
  Unknown aliases are refused ("Website alias not found"). Only `https://` addresses are allowed.
- `Search <words>`: Google search of what you say, opened the same way.
- Window commands act on the window in focus: `Minimize`, `Maximize`, `Close Window` (asks for
  confirmation and shows which window; closes with WM_CLOSE so the app can ask "Save changes?"),
  `Fullscreen` (F11), `Show Desktop` (Win+D), `Task View` (Win+Tab), `Switch Window`
  (Alt+Tab: jumps to the previous window), `Next Window` (Alt+Tab), `Previous Window`
  (Alt+Shift+Tab).
- Browser commands (only when Chrome, Edge, Firefox, Brave or Opera is in focus; otherwise
  they are rejected with a message):

  | Say | Keys | Say | Keys |
  |---|---|---|---|
  | New Tab | Ctrl+T | Back | Alt+Left |
  | Close Tab | Ctrl+W | Forward | Alt+Right |
  | Next Tab | Ctrl+Tab | Page Up / Page Down | PageUp / PageDown |
  | Previous Tab | Ctrl+Shift+Tab | Go to Top / Go to Bottom | Home / End |
  | Refresh | F5 | Scroll Up / Scroll Down | mouse wheel, 5 notches |

  The keys can be changed with a `"keys"` field per command in `voice_commands.json`.
- Modes: `Pause`, `Resume`, `Voice Only`, `Gesture Only`, `Hybrid`
- Listen modes: `Wake Word Mode`, `Always Listen`, `Push To Talk`
- `Confirm`, `Cancel`, `Escape` work only while a confirmation or a list is pending.
- System commands:
  - `Volume Up` / `Volume Down` (2 % per command), `Mute` (toggle), `Play Media` / `Pause Media`
    (both send the same play/pause toggle key, Windows has no separate keys), `Screenshot`
    (Win+PrintScreen, saved by Windows in Pictures\Screenshots), `Lock Screen`.
  - `Sleep`: a **3-second countdown** appears first ("Sleep in 3s"); say "Hand, cancel" to stop it.
    After the countdown the PC sleeps. **The app stops with the PC: after waking up, start Hand&Voice
    again** (double-click `start.bat`). Say the cancel command quickly: wake word and command take
    about 2 s. The hand gesture 🤟 cancels it instantly.
  - `Shut down` / `Restart`: ask for confirmation first, then Windows counts down 60 s
    ("Shutting down in 60 s"). "Hand, cancel" runs `shutdown /a` and aborts it.
  - `Close All Apps`: the confirmation lists the number and names of the windows that will be
    asked to close (Hand&Voice itself, the desktop, taskbar and Windows helper windows are never
    included; administrator windows are skipped). Windows get WM_CLOSE, nothing is killed. After
    3 seconds you see which windows are still open (usually asking "Save changes?").
- All other commands from the Voice Commands page say "not available yet".

Resume from Pause with the tray menu, the UI, or **Ctrl+Alt+R**.

## Apps and aliases
Apps are discovered from the Start Menu, installed-program entries and Store apps (refreshed at
startup and with the Refresh button). On the **Applications** page you can add your own entries:
a spoken name, other spoken names, and the target. Leave the target empty to auto-detect it.
Each entry's alias list is independent (e.g. "ChatGPT" and "ChatGPT Classic" are separate).

**Important:** the speech model only knows ordinary English words. A name like "ChatGPT" or
"Telegram" may be missing from its vocabulary. Such names appear under the apps table as
"Not speakable". Add an alias made of common words (for example "chat bot"). Names with digits
or non-English letters cannot be spoken either.

## Files and websites
On the **Files & Websites** page you choose which folders are indexed (default: Documents,
Desktop, Downloads, and Projects if it exists), which extensions, and your website aliases.
Only file **names** are indexed, never contents. The index rebuilds at startup, every 30 minutes,
and with the Rebuild button. Hidden/system folders and `node_modules`, `.git`, `venv`,
`__pycache__` are skipped. Programs and scripts (`.exe .bat .cmd .ps1 .msi` and similar) are
never indexed or opened, even if you add them to the extension list.

All words of the file name you say must appear in the name. If one file clearly matches best
it opens; otherwise you get a numbered list.

Free text (file names, search words) is recognized in a second pass with the full English
vocabulary, using only the audio of that command, kept in memory. Accuracy on rare words is
lower than for the fixed commands: if a file is not found, say fewer or more common words.
Website aliases must be words the model knows ("Alim" may not be; add an alias like "alley m").

## Where your data lives
`%APPDATA%\HandAndVoice\` (Settings > "Open data folder"): `settings.json`, `voice_commands.json`,
`gestures.json`, `app_aliases.json`, `web_aliases.json`, `usage.db` (app launch counts),
`file_index.db` (file names) and `logs\handandvoice.log`. Defaults are copied on first run. To reset everything, close the app and
delete that folder.

## Privacy and safety
No network calls at runtime (only `scripts/download_models.py` uses the internet). Audio is never
written to disk. Logs contain no audio, video or file contents. Only a fixed command registry runs;
nothing from speech is executed as a shell command. Apps will be closed with WM_CLOSE (so they can
ask "save changes?"), never killed. Admin rights are never requested. The global key listener
reacts only to the push-to-talk key and Ctrl+Alt+R; key presses are not stored.

## Known limitations
English only (Uzbek and Russian are not supported). Always-on listening uses CPU and microphone
access continuously. **Apps running as administrator block keyboard input from Hand&Voice**
(Windows rule); you will see "Target app runs as administrator; keyboard input is blocked by
Windows" and nothing is retried. Scrolling uses the mouse wheel at the **current cursor position**
(it does not move the cursor), so the mouse pointer must be over the page. Fullscreen sends F11,
which not every app supports. **The app stops when the PC sleeps and must be started again** (double-click
`start.bat`). Recognition quality depends on your voice and microphone: the small offline model is tuned
for native English, so teach it your pronunciation (Voice Commands page). Free text (`File ...`,
`Search ...`) is read with the full vocabulary and is less accurate than the fixed commands. The gesture
rules use the fixed numbers from the specification; hands differ, tell me which gestures fail. The
camera turns on automatically in Hybrid and Gesture Only mode (the light shows it). Only a keyboard and
mouse-wheel are used, never the mouse cursor, so the pointer must already be where scrolling should happen.

## Manual test checklist (Phase 5)
Open Notepad and a browser first. Each line: say it, expect the result.
1. Notepad in focus: "Hand, Minimize" -> Notepad minimizes. Restore it. "Hand, Maximize" -> fills the screen.
2. "Hand, Show Desktop" -> desktop appears; again (or click a window) to return.
3. "Hand, Task View" -> Windows Task View opens (Esc closes it).
4. Two windows open: "Hand, Next Window" -> focus jumps to the other window; "Hand, Previous Window" cycles back.
5. Browser in focus: "Hand, New Tab", "Hand, Close Tab", "Hand, Next Tab", "Hand, Previous Tab",
   "Hand, Refresh", "Hand, Back", "Hand, Forward" -> each does what its name says.
6. Browser on a long page, mouse pointer over the page: "Hand, Scroll Down", "Hand, Scroll Up",
   "Hand, Page Down", "Hand, Page Up", "Hand, Go to Bottom", "Hand, Go to Top".
7. "Hand, Fullscreen" in the browser -> F11 fullscreen; again to leave.
8. Notepad in focus: "Hand, Next Tab" -> rejected: "works only when a browser is in focus"; nothing happens.
9. Notepad with unsaved text: "Hand, Close Window" -> prompt names the window; nothing closes yet.
   "Hand, Confirm" -> Notepad asks "Save changes?" (it is never killed). Try again and say "Hand, Cancel" -> nothing happens.
10. Say "Hand, Close Window" with the Hand&Voice window in focus -> refused.
11. (Optional) Start any app "as administrator", focus it, say "Hand, Refresh" -> the administrator message appears.

## Performance (measured)
Measured on a development PC (8 cores), per thread, in a real run. **Run the benchmark on your i3
laptop** to check your own numbers: `.\venv\Scripts\python.exe scripts/benchmark.py --camera`
(hold a hand in front of the camera for the camera part). It prints OK / SLOW against the budgets:
gesture pipeline at least 10 FPS, wake-word listener under 25 % of one core.

| State | CPU (percent of ONE core) |
|---|---|
| Paused | about 1 % |
| Voice Only (microphone listening, idle) | about 7 % |
| Gesture Only, no hand in view | about 28 % |
| Hybrid, nobody speaking, no hand in view | about 31 % |
| Camera pipeline with the camera running at 12 FPS | about 50 %, 11.9 FPS reached |

What keeps it light: with no hand in view for 1.5 s the camera only scans 4 times per second and
speeds up the moment a hand appears (the Hand Gestures page says "idle"); a hidden window does no
gesture drawing; the camera is read on a timer, not in a busy loop (an early version spun a core at
100 % because DirectShow's `grab()` does not wait for frames); the microphone and camera are really
closed in Pause, Voice Only and Gesture Only. If your PC is slow: use Voice Only when you do not need
gestures, lower the camera FPS in Settings, close other heavy programs.

The window never blocks: every slow job (microphone, camera, MediaPipe, speech, file index, app scan,
web tab typing, downloads) runs in its own thread; the UI thread only reads queues every 30 ms. In a
real run the UI thread never paused for more than 150 ms while the app was in use.

## Modes
| Mode | Microphone | Camera / gestures |
|---|---|---|
| **Hybrid** (default at launch) | on | on |
| **Voice Only** | on | off (camera released) |
| **Gesture Only** | **closed** (the stream is really closed, not ignored) | on |
| **Paused** | **closed** | **released** |
| **Face Only** (manual only) | **closed** | camera on, **hand gestures OFF**, only your eyes are read |

Switch with the buttons on the Dashboard, the tray menu (the active mode is checked; hovering shows it),
or by voice ("Hand, Voice Only", "Hand, Gesture Only", "Hand, Hybrid", "Hand, Pause"). In Gesture Only
and Paused the microphone is closed, so voice cannot bring you back: use the Dashboard, the tray, or
press **Ctrl+Alt+R**, which resumes from Pause (back to the mode you had before). Each change shows
"Mode: ... / Microphone ... / camera ..." in the overlay. The header always shows the real state of the
microphone and the camera (it is driven by the devices themselves, not by the mode name).

## Face Only mode (face expressions)
A separate mode that you start **by hand only**: it is never the start mode and there is no voice command or
gesture that enters it (the microphone is closed in it anyway). In Face Only the **microphone and the hand
gestures are OFF**; the camera reads only your face:

- close your **RIGHT eye** (hold about half a second) = **next page**;
- close your **LEFT eye** = **previous page**.

Normal blinks and both eyes closed never count; only one eye closed while the other stays clearly open.

**Start:** Face Expressions page > "Start Face Only", or tray menu > Mode > Face Only, or Dashboard > Face Only.
A question reminds you that the microphone and hands go off. **Leave:** the same button ("Leave"), the tray
menu, the Dashboard buttons, or **Ctrl+Alt+R** (back to the mode you had before; Pause never returns to Face Only).

"Page" means (Face Expressions page > Action style, saved immediately):
| Style | Right eye | Left eye |
|---|---|---|
| **Tabs / windows** (default) | next browser tab (Ctrl+Tab); in other apps next window | previous tab / window |
| **Page Down / Page Up** | Page Down | Page Up |
| **History** | Alt+Right (forward) | Alt+Left (back) |

The page also has the **hold time**, the live **eye bars** (how open each eye is) and a **swap** checkbox. If
the "right eye" bar reacts when you close your LEFT eye, tick "swap". The app learns how open your eyes are
during the first second ("Learning your eyes": keep both eyes open and look at the screen). Administrator
windows cannot receive keys from the app (Windows rule): you get a message instead.

The face model (about 4 MB, `face_landmarker.task`) is a separate download:
`.\venv\Scripts\python.exe scripts/download_models.py --face`, or Settings > Download missing models. Frames
are processed in memory and never saved.

### Manual test checklist (Face Only)
1. Face Expressions page: the Start button is disabled with "model MISSING" until the model is downloaded.
2. After downloading: Start > confirm. Header: Microphone OFF, Camera ON; the overlay explains the mode.
3. Keep both eyes open for a second ("Learning your eyes" disappears), then both bars show about 100 %.
4. Close only your right eye for half a second: the bar drops and the next tab/page happens once. Keep it
   closed: it does not repeat. Open it, close it again: next again.
5. Same with the left eye: previous. Blink normally for 20 seconds, talk, smile: nothing happens.
6. Show a peace sign or open palm to the camera: nothing happens (hands are off). Say "Hand, ...": nothing.
7. If left/right are reversed: tick "swap". Try each action style in Notepad/browser.
8. Leave with Ctrl+Alt+R: microphone and hand gestures work again. Quit while in Face Only: camera light goes off.

## Settings page
Everything is saved and applied immediately unless a note says to restart:
start mode (used at the next launch), privacy indicators (overlay message when the microphone or camera
turns on/off), listen mode, **wake word** (one plain word, never a command word), command window seconds,
**push-to-talk key** ("Press a key..." then press it), **microphone** picker, **camera** picker,
camera FPS (5-30), default / fist / scroll hold times (button "Apply to all gestures"), speech model
(Small or Better) with download buttons, model status, and the data folder. The hand to use and the
movement tolerance are on the Hand Gestures page. The download buttons run `scripts/download_models.py`
after you confirm: they are the only place that uses the internet.

### Manual test checklist (Phase 9)
1. Start the app: Dashboard shows Hybrid, the header says microphone "Listening for 'Hand'" and `Camera: ON`.
2. Click **Voice Only**: camera light off, microphone still listening. Click **Gesture Only**: the header
   says "Microphone OFF" while the camera is on. Click **Pause**: both off. Click **Resume** (same button).
3. Pause again, then press **Ctrl+Alt+R**: it resumes to the previous mode.
4. Tray: right-click the icon, Mode menu: the current mode has a check mark; choose another one.
5. Say "Hand, Pause": everything goes off. Resume with Ctrl+Alt+R. Try saying a command while paused: nothing happens.
6. Settings: change the **wake word** to another plain English word (for example "computer"), say it, then
   change it back. Try "minimize": it must be refused.
7. Settings > Push-to-talk key > "Press a key...", press F9, choose Listen mode "Push to talk": commands work
   only while F9 is held.
8. Settings > Microphone: pick your microphone (or "System default"); it must keep working.
9. Settings > "Show a message when microphone or camera turns on/off": untick it, switch modes: no overlay
   message about the devices (the header still updates).

## Hand gestures
The camera recognizes your gestures, shows them (Hand Gestures page and a small overlay with a
hold-progress bar) and runs their actions. Gestures never move the mouse cursor; they send keys and
mouse-wheel events (scrolling uses the wheel at the **current cursor position**, so keep the pointer
over the page).

- **The camera turns on by itself in Hybrid mode (the default) and Gesture Only mode**, and the camera
  light will be on. In Voice Only and Paused mode, or with "Gestures enabled" unticked, the camera is
  released (really switched off). The header shows `Camera: ON/OFF`.
- Frames are processed in memory only, never saved or sent anywhere. The preview shows names, not video.
- The image is mirrored like a selfie camera, so "Right" means your real right hand and pointing to your
  right is "POINT_RIGHT". Settings on the page: hand (Any/Right/Left), movement tolerance, camera FPS
  (5-30, default 12) and camera number.

How a gesture is accepted: the same pose for 5 frames in a row, held still (the wrist may drift less
than 0.15 x hand size) for its hold time, counted from the first frame. After it fires, the same gesture
is ignored for 1 s (scroll and volume gestures are continuous and not subject to that). An unknown pose
shows "Hand: unknown pose" and never does anything.

| Gesture | Name | Hold | Action |
|---|---|---|---|
| 👍 | THUMBS_UP | 1 s | Close All Apps: asks to confirm (list of windows shown) |
| 👎 | THUMBS_DOWN | 1 s | Close Window: asks to confirm (names the window) |
| ✊ | FIST | 2 s | Sleep with a 3 s countdown (cancel with 🤟) |
| ✋ | OPEN_PALM | 1 s | Show / hide the info panel (mode, last command, last result, help) |
| ✌️ | PEACE | 1 s | Browser mode (only when a browser is in focus) |
| 👆 | INDEX_THUMB | 1 s | Switch Window mode |
| ☝️ / 👇 | POINT_UP / POINT_DOWN | 0.3 s | Scroll while held (1 wheel notch / 80 ms) |
| 👉 / 👈 | POINT_RIGHT / POINT_LEFT | 1 s | Next / previous window (tab if a browser is in focus), once per hold |
| 👌 | OK | 1 s | Confirm when a confirmation is pending, else Enter |
| 🤘 | ROCK | 1 s | Play / pause |
| 🤙 | SHAKA | 1 s | Volume mode |
| 🤟 | ILY | 1 s | Escape: cancels a confirmation / countdown / list, leaves a mode, closes the panel |

Hold times can be changed on the Hand Gestures page (saved in `gestures.json`).

**Switch Window mode (👆):** Windows' Alt+Tab switcher opens and **Alt stays held**. Point right / left
(hold about 0.4 s, repeats every 0.4 s while you keep pointing) to move the selection, 👌 selects the
highlighted window, 🤟 cancels without changing focus. 8 seconds without any gesture cancels too. Saying
"Next Window" / "Previous Window" also moves the selection while it is open.

**Browser mode (✌️):** 👉 next tab (Ctrl+Tab), 👈 previous tab (Ctrl+Shift+Tab), every 0.4 s. 🤟 leaves;
10 seconds without a gesture leaves too.

**Volume mode (🤙):** hold 🤙 still for 1 s, then move the hand up / down: every 10 % of the hand's size of
travel changes the volume by one step (2 %), at most every 0.15 s. Stop by lowering the hand out of
view, or with 🤟.

**Safety:** the camera stopping, a camera error, switching to Voice Only / Paused, unticking "Gestures
enabled", quitting, and a crash all release the Alt key, so it can never stay stuck down. Nothing
destructive runs from a gesture without the confirmation (👌) or the countdown (🤟 cancels).

### Manual test checklist (Phase 8: actions)
Save your work and open Notepad, a browser with 3+ tabs, and a second window first.
1. Browser with a long page, mouse pointer over the page: hold ☝️ -> scrolls up, 👇 -> down; stops when you lower the hand.
2. Hold 👉 (1 s) in the browser -> next tab; 👈 -> previous tab. In Notepad the same gesture switches the window.
3. Hold 👆 (1 s): the Windows switcher opens. Point 👉 / 👈 to move the selection, 👌 to select. Try again and
   cancel with 🤟: focus must not change. Try again and do nothing for 8 s: it cancels.
4. In the browser hold ✌️ -> "Browser mode"; 👉 / 👈 change tabs every 0.4 s; 🤟 leaves. In Notepad ✌️ must say "needs a browser".
5. Play some music: hold 🤘 -> pauses, again -> resumes.
6. Hold 🤙 for 1 s (still), then move the hand slowly up -> volume rises; down -> falls; 🤟 stops.
7. Hold 👍 -> the prompt lists the windows; **nothing closes**. Make 🤟 -> cancelled. Repeat and make 👌 -> windows get a close request.
   (Only do the 👌 part if you are ready to close your apps.)
8. Notepad with unsaved text: hold 👎 -> the prompt names Notepad; 👌 -> Notepad asks "Save changes?".
9. Hold ✊ for 2 s -> "Sleep in 3s". Make 🤟 within 3 s -> the PC stays awake. (Do not let it run unless you want to sleep.)
10. Hold ✋ -> info panel appears; ✋ again hides it. 🤟 also closes it.
11. Hold 👌 with a text field in focus and nothing pending -> Enter is typed.
12. Quit from the tray while in Switch Window mode: afterwards press a letter key; the Alt key must not be stuck.

### Manual test checklist (Phase 7)
Sit about an arm's length from the camera, hand clearly visible, plain background if possible.
1. Open the **Hand Gestures** page. With no hand it says "No hand"; the header says `Camera: ON`.
2. Show each of the 14 gestures one by one. The name should appear, confidence is shown, and the bar
   fills while you hold still. Write down which ones are NOT recognized or are mixed up (for example
   ✋ shown as 👌, or 👍 not recognized). Tell me those: the thresholds can be tuned.
3. FPS should be around 10-13. Make a strange pose (three fingers, middle finger only): it must say
   "Hand: unknown pose".
4. Move your hand around while holding a gesture: the hold must restart ("Keep holding still...").
5. Say "Hand, Gesture Only", then "Hand, Pause" via the tray: the camera light goes off in Pause;
   Voice Only turns it off too. Tick/untick "Gestures enabled" to check the same.
6. Quit from the tray: the camera light must go off.

## If the app hears you wrongly
Speech recognition is offline and the small model is tuned for native English, so a strong accent
can be misheard. Things that help, in this order:
1. **Look at what it heard.** The Dashboard shows the last recognized texts, with a second reading in
   brackets ("also heard: ..."). The header shows "No command understood" if nothing was understood.
2. **Teach it your pronunciation** (Voice Commands page, bottom): choose what the app heard (for
   example "mini meter") and the command it means ("Minimize"), click Add. It works immediately.
3. **Try the bigger speech model** (128 MB, once):
   `.\venv\Scripts\python.exe scripts/download_models.py --better`, then Settings > Speech model >
   "Better", then Quit and restart the app. It is NOT used automatically: in my synthetic-voice
   test it was not more accurate for the short command grammar (and about 0.4 s slower), but real
   voices differ, so compare both with your own voice. Switch back to "Small" in the same place.
4. Speak the wake word and command in one calm phrase, close to the microphone, and pause after it
   (the command is taken about 1 s after you stop). Quiet room.

How recognition works: a small grammar (all commands plus your own aliases and recently used apps)
is tried first; if that does not give a valid command, the same audio is read again with the full
English vocabulary and matched against all discovered apps. A big grammar made things worse, so
the list of discovered apps is deliberately not in it.

## Manual test checklist (Phase 6)
Save your work first. Do **not** say Shut down / Restart / Sleep until you want to test them.
1. "Hand, Volume Up" / "Hand, Volume Down" -> the volume bubble appears. "Hand, Mute" twice -> muted, then unmuted.
2. Play some music or a video: "Hand, Pause Media" pauses, "Hand, Play Media" resumes.
3. "Hand, Screenshot" -> a new image appears in Pictures\Screenshots.
4. "Hand, Lock Screen" -> Windows locks (sign in again).
5. "Hand, Close All Apps" -> the prompt lists the windows. Say "Hand, Cancel": nothing closes. Repeat and
   wait 5 s: "Confirmation timed out", nothing closes.
6. Open Notepad with unsaved text and Calculator, say "Hand, Close All Apps", then "Hand, Confirm" -> Calculator
   closes, Notepad asks "Save changes?"; about 3 s later the overlay says what is still open.
7. "Hand, Sleep", then quickly "Hand, Cancel" -> the countdown stops and the PC stays awake.
   (Only if you want the real test: let it run, the PC sleeps; move the mouse, then start the app again.)
8. "Hand, Shut down" -> prompt. "Hand, Confirm" -> "Shutting down in 60s". "Hand, Cancel" -> "Shutdown cancelled".
   Check with PowerShell: `shutdown /a` says no shutdown is in progress.

## When something is missing
The app keeps running and tells you what to do; the rest still works.
- **No camera / camera busy:** "Camera N could not be opened ..." (voice keeps working).
- **No microphone, or the chosen one is gone:** "Microphone not available ..." (gestures keep working).
- **Speech model missing / wake word not in the model's vocabulary / hand model missing:** a clear message
  with the command to fix it (`scripts/download_models.py`, or choose another wake word in Settings).
- The same problem is reported once, not on every retry. After plugging a device in or fixing a model, press
  **Retry microphone and camera** on the Dashboard (or change the mode and back).
- Windows shutdown / restart / logoff is never blocked by the app. The window's X button hides it to the
  tray; use the tray menu > Quit to close it.

### Manual test checklist (Phase 10)
1. `.\venv\Scripts\python.exe scripts/benchmark.py --camera` on the i3 laptop: all lines should say OK.
2. Task Manager > Details > python.exe while the app idles in Hybrid: about 30 % of one core (on a 4-thread
   i3 that is under 10 % of the whole PC). Pause: nearly 0 %.
3. Unplug the webcam (or cover nothing, just disable it in Device Manager) and start the app: a camera message,
   voice still works. Plug it back, click Retry on the Dashboard: the camera starts.
4. Settings > Microphone: choose a device, then unplug it: a microphone message, gestures keep working.
5. Right-click the tray icon > Quit: the camera light goes off within about a second and no python.exe remains.
6. Click the window's X: it hides, the tray icon stays and the app keeps listening.
7. With the window open, restart Windows: the app must not appear in "this app is preventing shutdown".

## Troubleshooting
- **Camera 0 could not be opened:** close apps that use the camera (Zoom, Teams, browser tabs), check
  Windows Settings > Privacy & security > Camera (allow desktop apps), or change the camera number on
  the Hand Gestures page (laptops with several cameras).
- **"The hand model is missing":** run `.\venv\Scripts\python.exe scripts/download_models.py --hand`.
- **"The face model is missing":** run `.\venv\Scripts\python.exe scripts/download_models.py --face`.
- **Face Only: wrong eye, or winks not detected:** watch the eye bars on the Face Expressions page; tick "swap"
  if they are reversed, raise the hold time if it triggers too easily, lower it if it feels slow. Face the
  light, keep your face in the frame, glasses glare can hide the eyes.
- **Gestures are not recognized well:** more light, hand fully in the frame, palm toward the camera, not
  too close. The rules use fixed numbers from the specification: tell me which gestures fail.
- **"Speech model is missing":** run `scripts/download_models.py` (step 3), then restart the app.
- **Microphone not found / busy:** close other apps using the microphone, check Windows Settings >
  Privacy > Microphone, then switch listen mode once in Settings to retry.
- **Wake word not recognized:** speak clearly, one word, a little slower. Check the header shows
  "Listening for 'Hand'". Quiet room first.
- **An app is not opened:** look at the Applications page: is the target set? Is the name listed as
  "Not speakable"?
- **"Hand, File ..." finds nothing:** check the Files page shows a non-zero file count and that the
  extension is in the list. Wait for the first index to finish after startup.
- **Web alias opens in a new window instead of a tab (or nothing happens):** the browser must be the
  window in focus when you speak. If the browser runs as administrator, Windows blocks typing.
- **Tray icon not visible:** click the `^` arrow in the taskbar corner.
- **`pip` fails on mediapipe/numpy:** make sure `python --version` shows 3.11 64-bit.
