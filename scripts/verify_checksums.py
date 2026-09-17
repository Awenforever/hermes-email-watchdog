#!/usr/bin/env python3
from pathlib import Path
import hashlib, sys
root=Path(sys.argv[1]).resolve()
manifest=Path(sys.argv[2])
errors=[]
for raw in manifest.read_text(encoding="utf-8").splitlines():
    if not raw.strip(): continue
    expected,rel=raw.split("  ",1)
    p=(root/rel).resolve()
    if root not in p.parents and p!=root:
        errors.append(f"path escape: {rel}"); continue
    if not p.is_file():
        errors.append(f"missing: {rel}"); continue
    actual=hashlib.sha256(p.read_bytes()).hexdigest()
    if actual!=expected:
        errors.append(f"hash mismatch: {rel}")
if errors:
    raise SystemExit("\n".join(errors))
print("CHECKSUMS_OK")
