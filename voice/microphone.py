"""Microphone capture. Audio lives only in memory (a bounded queue) - never on disk."""
from __future__ import annotations

import logging
import queue
from typing import Optional

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BLOCK_SIZE = 4000  # 250 ms at 16 kHz


class MicrophoneError(RuntimeError):
    pass


def list_input_devices() -> list[tuple[int, str]]:
    import sounddevice as sd

    out = []
    for i, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            out.append((i, dev["name"]))
    return out


class Microphone:
    """Raw 16 kHz mono 16-bit stream. stop() fully closes the device."""

    def __init__(self, device=None, sample_rate: int = SAMPLE_RATE,
                 blocksize: int = BLOCK_SIZE, max_blocks: int = 40):
        self.device = device
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self._q: queue.Queue[bytes] = queue.Queue(maxsize=max_blocks)
        self._stream = None

    @property
    def running(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("Audio status: %s", status)
        block = bytes(indata)
        try:
            self._q.put_nowait(block)
        except queue.Full:
            try:
                self._q.get_nowait()  # drop the oldest block
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(block)
            except queue.Full:
                pass

    def start(self) -> None:
        if self._stream is not None:
            return
        try:
            import sounddevice as sd

            stream = sd.RawInputStream(
                samplerate=self.sample_rate, blocksize=self.blocksize, device=self.device,
                channels=1, dtype="int16", callback=self._callback,
            )
            stream.start()
        except Exception as exc:  # PortAudioError, no device, device busy...
            log.error("Microphone could not be opened: %s", exc)
            if self.device is not None:
                hint = (f"The selected microphone (#{self.device}) was not found or is in use. "
                        "Choose another one, or 'System default', in Settings > Microphone.")
            else:
                hint = ("No microphone was found or it is in use. Plug one in, check Windows Settings > "
                        "Privacy > Microphone, then use 'Retry microphone and camera' on the Dashboard.")
            raise MicrophoneError("Microphone not available. " + hint) from exc
        self._stream = stream
        log.info("Microphone opened")

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("Error closing microphone")
            log.info("Microphone closed")
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def read(self, timeout: float = 0.25) -> Optional[bytes]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def read_nowait(self) -> Optional[bytes]:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None
