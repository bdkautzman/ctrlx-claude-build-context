#!/usr/bin/env python3
# Entry point of the snap daemon (snapcraft.yaml: apps.service.command = bin/run.sh).
# Yes, this ".sh" file is a Python script: it is the pattern used by the ctrlX SDK 4.6
# Python samples. "python3" resolves to the interpreter of the snap's Python venv.
import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
