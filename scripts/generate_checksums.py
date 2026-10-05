#!/usr/bin/env python3
from pathlib import Path
import hashlib,sys
root=Path(sys.argv[1]).resolve()
excluded={Path("checksums/SHA256SUMS")}
rows=[]

# Keep the distributable manifest independent of developer-local artefacts.
# These mirror repository ignore classes that may exist in a working tree but
# can never be present in a fresh GitHub installation.
ignored_names={".coverage","email_watchdog_config.json","email_watch_seen.json","email_watchdog_outbox.json","email_watchdog_status.json"}
ignored_suffixes={".pyc",".pyo",".sqlite",".sqlite3",".db",".log"}
ignored_dirs={".git","diagnostics","__pycache__",".pytest_cache",".mypy_cache","email_cache","email_learning"}

def portable_bytes(path):
    data=path.read_bytes()
    return data.replace(b"\r\n", b"\n") if b"\0" not in data else data

for p in sorted(root.rglob("*")):
    if not p.is_file(): continue
    rel=p.relative_to(root)
    if (
        rel in excluded
        or any(part in ignored_dirs for part in rel.parts)
        or p.name in ignored_names
        or p.suffix in ignored_suffixes
        or p.name.endswith(".tar.gz")
    ):
        continue
    rows.append(f"{hashlib.sha256(portable_bytes(p)).hexdigest()}  {rel.as_posix()}")
out=root/"checksums/SHA256SUMS"
out.parent.mkdir(parents=True,exist_ok=True)
out.write_text("\n".join(rows)+"\n",encoding="utf-8")
print(f"CHECKSUM_MANIFEST_OK files={len(rows)}")
