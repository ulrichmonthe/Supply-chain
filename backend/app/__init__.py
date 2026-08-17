"""Health Supply Chain Network Design."""

import sys

# Python 3.9 is supported deliberately. It is what macOS ships as the system
# `python3`, and a tool whose whole claim is that it survives without its authors
# should not open by telling a ministry analyst to install a new interpreter. The
# cost is writing `Optional[X]` instead of `X | None`, which is not much of a cost.
if sys.version_info < (3, 9):  # pragma: no cover - version guard
    raise RuntimeError(
        f"This application needs Python 3.9 or newer, but is running on "
        f"{sys.version.split()[0]} at {sys.executable}."
    )

__version__ = "0.1.0"
