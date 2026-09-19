"""Small cross-platform advisory file-lock helpers.

The caller owns the file descriptor. Locks cover the first byte of the lock
file on Windows and the whole file on POSIX.
"""

from __future__ import annotations

import os


if os.name == "nt":
    import msvcrt

    def acquire_file_lock(fd: int) -> None:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def release_file_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def acquire_file_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def release_file_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
