#!/usr/bin/env python3
"""Clipboard history store (Win + V shows it, see winshell.py ClipboardPanel).

Run by `wl-paste --watch` (hyprland.lua autostart) with what was copied on
stdin:   clipstore.py text   |   clipstore.py image
Keeps the last MAX entries in ~/.cache/winshell/clipboard, newest first, no
duplicates. Things a password manager marks as sensitive are not kept.
"""
import fcntl
import hashlib
import json
import os
import sys
import time

MAX = 25
MAX_TEXT = 200_000          # bytes; bigger texts are not kept
DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "winshell", "clipboard")
INDEX = os.path.join(DIR, "index.json")


def main():
    # what you copied is only for you: folder 0700, files 0600
    os.umask(0o077)
    kind = sys.argv[1] if len(sys.argv) > 1 else "text"
    if os.environ.get("CLIPBOARD_STATE") in ("sensitive", "clear", "nil"):
        return
    data = sys.stdin.buffer.read()
    if not data or (kind == "text" and (len(data) > MAX_TEXT or not data.strip())):
        return
    os.makedirs(DIR, exist_ok=True)
    os.chmod(DIR, 0o700)                    # also when it was made before
    digest = hashlib.sha1(data).hexdigest()[:16]
    ext = "png" if kind == "image" else "txt"
    name = f"{digest}.{ext}"
    with open(os.path.join(DIR, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(INDEX) as f:
                entries = json.load(f)
        except (OSError, ValueError):
            entries = []
        entries = [e for e in entries if e.get("file") != name]      # copied again: to the top
        path = os.path.join(DIR, name)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(data)
        entries.insert(0, {"file": name, "kind": kind, "time": time.time()})
        for old in entries[MAX:]:
            try:
                os.unlink(os.path.join(DIR, old["file"]))
            except OSError:
                pass
        entries = entries[:MAX]
        tmp = INDEX + ".tmp"
        with open(tmp, "w") as f:
            json.dump(entries, f)
        os.replace(tmp, INDEX)


if __name__ == "__main__":
    main()
