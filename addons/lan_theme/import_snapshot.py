"""Repository entry for the same CLI included in the installed ZIP."""

from .backend.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
