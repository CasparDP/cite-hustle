"""Portable path helpers.

Paths stored in the DB use the $HOME/... convention so the same database works
across machines (see CLAUDE.md). Convert with to_portable() before writing a
path to the DB and expand() after reading one.
"""

import os
from pathlib import Path


def to_portable(path: str | Path) -> str:
    """Convert an absolute path under the user's home to $HOME/... form."""
    path_str = str(path)
    home = str(Path.home())
    if path_str.startswith(home):
        return "$HOME" + path_str[len(home) :]
    return path_str


def expand(path_str: str) -> Path:
    """Expand a stored $HOME/... (or ~/...) path to a local absolute Path."""
    return Path(os.path.expandvars(os.path.expanduser(path_str)))
