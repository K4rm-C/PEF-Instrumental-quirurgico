"""Run: python main.py  (from apps/EvidenceWorker)."""

from __future__ import annotations

import logging
import signal
import sys

from worker import EvidenceWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [evidence] %(message)s",
)


def main() -> int:
    worker = EvidenceWorker()

    def _stop(*_args) -> None:
        worker.stop()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
