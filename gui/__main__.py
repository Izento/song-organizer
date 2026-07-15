from __future__ import annotations

import sys
from pathlib import Path


if __package__:
    from .app import run
else:
    # PyInstaller executes this entry point as ``__main__`` rather than as
    # ``gui.__main__``. Add the project/package parent for direct execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from gui.app import run


if __name__ == "__main__":
    run()
