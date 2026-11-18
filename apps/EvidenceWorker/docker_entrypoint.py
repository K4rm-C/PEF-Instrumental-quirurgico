"""Fix named-volume ownership for /data/clips, then drop privileges to `evidence`."""
from __future__ import annotations

import grp
import os
import pwd
import sys
from pathlib import Path


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, gid)
    for child in path.rglob("*"):
        try:
            os.chown(child, uid, gid)
        except OSError:
            pass


def main() -> None:
    user = pwd.getpwnam("evidence")
    group = grp.getgrnam("evidence")
    clips = Path(os.environ.get("EVIDENCE_CLIPS_DIR", "/data/clips"))
    _chown_tree(clips, user.pw_uid, group.gr_gid)
    os.setgid(group.gr_gid)
    os.setuid(user.pw_uid)
    os.environ["HOME"] = user.pw_dir
    argv = sys.argv[1:] or ["python", "main.py"]
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
