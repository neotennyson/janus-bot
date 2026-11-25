from __future__ import annotations

import logging
import threading
import time
from typing import List

log = logging.getLogger("janus.monitor")


class Monitor:
    """The always-on loop: poll every source, feed the engine, and wake early
    whenever a push notification says a new trade is up."""

    def __init__(self, engine, sources: List, interval: int = 30):
        self.engine = engine
        self.sources = sources
        self.interval = interval
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def wake(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def poll_once(self) -> int:
        count = 0
        for src in self.sources:
            try:
                signals = src.poll() if hasattr(src, "poll") else []
            except Exception as e:
                log.error("source %s failed: %s", getattr(src, "name", src), e)
                continue
            for sig in signals:
                self.engine.process(sig)
                count += 1
        return count

    def run_forever(self):
        log.info("monitor started (interval %ss, %d source(s))", self.interval, len(self.sources))
        while not self._stop.is_set():
            n = self.poll_once()
            if n:
                log.info("processed %d signal(s)", n)
            self._wake.wait(timeout=self.interval)
            self._wake.clear()
        log.info("monitor stopped")

    def start_background(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run_forever, name="janus-monitor", daemon=True)
        self._thread.start()
