import shutil
import sys
from pathlib import Path

from app import security
from app.main import SHARE, unique_path


def send(src: Path):
    if not src.is_file():
        print(f"skipped (not a file): {src}")
        return
    dest = unique_path(src.name)
    tmp = SHARE / f".{dest.name}.sending"     # leading dot = hidden from the file list
    print(f"copying {src.name} ...")
    shutil.copyfile(src, tmp)
    tmp.replace(dest)                         # appears atomically when complete
    security.add_event("sent", dest.name, dest.stat().st_size)
    print(f"sent {dest.name}")


if len(sys.argv) < 2:
    sys.exit("usage: send.py FILE [FILE ...]")
for arg in sys.argv[1:]:
    send(Path(arg).expanduser())