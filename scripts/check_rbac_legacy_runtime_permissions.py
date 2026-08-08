from __future__ import annotations
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
LEGACY = (r"customer:read\b", r"order:write\b", r"risk:read\b", r"risk:write\b")
RUNTIME_ROOTS = (ROOT / "app", ROOT / "frontend" / "src")
EXCLUDED = {ROOT / "app" / "services" / "auth_seed.py"}


def main() -> int:
    findings = []
    for base in RUNTIME_ROOTS:
        for path in base.rglob("*"):
            if not path.is_file() or path in EXCLUDED or path.suffix not in {".py", ".ts", ".vue"}:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in LEGACY:
                if re.search(pattern, text):
                    findings.append({"path": str(path.relative_to(ROOT)), "permission": pattern})
    result = {"legacy_runtime_references": findings, "pass": not findings}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
