from __future__ import annotations
import signal, threading
from .config import Settings
from .db import Database
from .pipeline import Pipeline

def run_loop(pipeline: Pipeline, stopped: threading.Event):
    import logging
    while not stopped.is_set():
        try:
            worked = pipeline.tick()
        except Exception as exc:
            logging.error("Worker scheduler failure: %s", type(exc).__name__)
            worked = False
        if not worked:
            stopped.wait(1)

def main():
    settings = Settings()
    settings.validate()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    run_loop(Pipeline(Database(settings.data_dir), settings), stopped)

if __name__ == "__main__":
    main()
