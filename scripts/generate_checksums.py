#!/usr/bin/env python3
from pathlib import Path
import hashlib,sys
root=Path(sys.argv[1]).resolve()
excluded={Path("checksums/SHA256SUMS")}
rows=[]

def portable_bytes(path):
    data=path.read_bytes()
    return data.replace(b"\r\n", b"\n") if b"\0" not in data else data

for p in sorted(root.rglob("*")):
    if not p.is_file(): continue
    rel=p.relative_to(root)
    if rel in excluded or ".git" in rel.parts or "diagnostics" in rel.parts or "__pycache__" in rel.parts or p.suffix in {".pyc", ".pyo"}:
        continue
    rows.append(f"{hashlib.sha256(portable_bytes(p)).hexdigest()}  {rel.as_posix()}")
out=root/"checksums/SHA256SUMS"
out.parent.mkdir(parents=True,exist_ok=True)
out.write_text("\n".join(rows)+"\n",encoding="utf-8")
print(f"CHECKSUM_MANIFEST_OK files={len(rows)}")
