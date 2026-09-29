"""A simple progress line, e.g. "uploading: 12/240 chunks, 6.0/120.0 MiB".

On a terminal the line updates in place; when output is redirected it prints a new
line every few seconds instead. With stream=None it does nothing.
"""

import time

MIB = 1024 * 1024


class Progress:
    def __init__(self, label: str, total_chunks: int, total_bytes: int, stream=None):
        self.label = label
        self.total_chunks = total_chunks
        self.total_bytes = total_bytes
        self.stream = stream
        self.tty = bool(stream is not None and getattr(stream, "isatty", lambda: False)())
        self.interval = 0.1 if self.tty else 2.0
        self.chunks = 0
        self.bytes = 0
        self._last_time = 0.0
        self._last_shown = -1

    def advance(self, nbytes: int) -> None:
        self.chunks += 1
        self.bytes += nbytes
        now = time.monotonic()
        if now - self._last_time >= self.interval:
            self._last_time = now
            self._show()

    def done(self) -> None:
        """Show the final count and end the line. Call once, even on errors."""
        if self.stream is None or self.total_chunks == 0:
            return
        if self.chunks != self._last_shown:
            self._show()
        if self.tty:
            self.stream.write("\n")
            self.stream.flush()

    def _show(self) -> None:
        if self.stream is None:
            return
        self._last_shown = self.chunks
        text = (
            f"{self.label}: {self.chunks}/{self.total_chunks} chunks, "
            f"{self.bytes / MIB:.1f}/{self.total_bytes / MIB:.1f} MiB"
        )
        self.stream.write(("\r" + text) if self.tty else (text + "\n"))
        self.stream.flush()
