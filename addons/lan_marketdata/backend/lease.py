"""One OS lease for the single extension-owned writer, released on crash."""

import os


class ProcessLease:
    def __init__(self, path):
        self.file = open(path, "a+b")
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError(
                "Another lan.marketdata writer owns this directory"
            ) from None

    def close(self):
        self.file.close()
