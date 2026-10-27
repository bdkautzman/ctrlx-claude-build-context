"""The Python package lives in src/ (snap part "app" builds only that folder, gotchas #47):
make `import app` work for `python3 -m unittest discover -s tests -t .` from the project root."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
