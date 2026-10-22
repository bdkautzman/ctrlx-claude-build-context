"""Logging to journald (visible in the ctrlX Logbook / `snap logs`) without extra packages.

snapd runs the daemon as a systemd service, so stdout/stderr already go to journald.
systemd interprets a "<N>" prefix (syslog priority) at the start of each line, so we map
Python levels to syslog priorities and get correct ERROR/WARNING levels in the Logbook
without bundling cysystemd.

Rules (flash wear, ctrlX App Guide 6.2.2): never write log FILES to the internal
storage, never log something on every cycle of a fast loop. Use DEBUG for chatty output.
"""

import logging
import sys

from app import config

_SYSLOG_PRIORITY = {
    logging.CRITICAL: 2,
    logging.ERROR: 3,
    logging.WARNING: 4,
    logging.INFO: 6,
    logging.DEBUG: 7,
}


class _JournalFormatter(logging.Formatter):
    def format(self, record):
        text = super().format(record)
        prio = _SYSLOG_PRIORITY.get(record.levelno, 6)
        # prefix every line, so multi-line tracebacks keep their priority
        return "\n".join("<{}>{}".format(prio, line) for line in text.splitlines())


def setup(level="INFO"):
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    if config.IN_SNAP:
        # journald adds its own timestamp
        handler.setFormatter(_JournalFormatter("%(name)s: %(message)s"))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    set_level(level)


def set_level(level):
    logging.getLogger().setLevel(getattr(logging, str(level).upper(), logging.INFO))
