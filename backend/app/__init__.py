"""Health Supply Chain Network Design."""

import sys

# macOS ships Python 3.9 as the system `python3`. Every dependency here installs
# cleanly on it and then the app dies at import on `@dataclass(slots=True)` with a
# TypeError that says nothing about the real problem. Fail with the fix instead.
if sys.version_info < (3, 10):  # pragma: no cover - version guard
    raise RuntimeError(
        f"This application needs Python 3.10 or newer, but is running on "
        f"{sys.version.split()[0]} at {sys.executable}.\n"
        f"On macOS:  brew install python@3.12\n"
        f"Then:      rm -rf .venv && make install PYTHON=$(brew --prefix)/bin/python3.12"
    )

__version__ = "0.1.0"
