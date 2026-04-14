#!/usr/bin/env python
"""
audit_input_bounds.py — Tier 2.3: find Pydantic request-model fields
that accept untrusted input without an upper bound.

OWASP A03 (Injection) and A04 (Insecure Design) both call out
"unrestricted length" on user-controlled input as a baseline mistake.
A 10MB `shop_domain` string reaches the DB, the logs, the audit trail,
and the LLM prompt — any one of those is a DoS or cost vector.

This tool walks every `class X(BaseModel):` in app/api/ and app/models/
and flags fields of type `str` / `list[...]` / `dict[...]` that do
NOT declare an upper bound (`max_length` for strings, `max_items` /
`max_length` for lists, `max_length` on dict). Fields annotated with
`StrictStr`, `conint`, `constr(...)` or similar already have bounds
baked in — we don't flag those.

Fields we intentionally skip:
  * Optional fields defaulting to `None` with no Field(...) — common
    for GET query params where the bound is irrelevant
  * Fields whose annotation is a Literal[...] — bounded by definition
  * Fields whose annotation is an Enum subclass — bounded by definition
  * Fields named `shop_domain` — these go through stricter validation
    downstream but the bound should still be there (we flag them)

Usage:
    ./venv/bin/python scripts/audit_input_bounds.py
    ./venv/bin/python scripts/audit_input_bounds.py --strict
"""
from __future__ import annotations

import ast
import pathlib
import sys
from collections import Counter, defaultdict

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"
SCAN_DIRS = [APP_ROOT / "api", APP_ROOT / "models"]
SKIP_DIRS = {"__pycache__", ".pytest_cache"}

# Only these base types are subject to the rule. Everything else
# (int / float / bool / UUID / datetime / Enum) is either self-bounded
# or handled by a different hardening pass.
STRING_ANNOTATIONS = {"str", "Optional[str]", "str | None"}
LIST_PREFIXES = ("list[", "List[", "tuple[", "Tuple[", "set[", "Set[")
DICT_PREFIXES = ("dict[", "Dict[")


class Finding:
    __slots__ = ("file", "line", "class_name", "field_name", "annotation", "kind")

    def __init__(self, file, line, class_name, field_name, annotation, kind):
        self.file = file
        self.line = line
        self.class_name = class_name
        self.field_name = field_name
        self.annotation = annotation
        self.kind = kind  # str_unbounded | list_unbounded | dict_unbounded


def _ann_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _field_call_has_bound(call: ast.Call, kind: str) -> bool:
    """Does this `Field(...)` call declare the right upper bound?
    For strings, a `pattern=` regex is accepted as a partial bound
    because in practice our regex patterns are enum-shaped and cap
    the valid input space."""
    for kw in call.keywords:
        if kw.arg in ("max_length", "max_items", "max"):
            return True
        if kind == "str_unbounded" and kw.arg == "pattern":
            return True
    return False


def _field_default_is_bounded(default: ast.AST | None, kind: str) -> bool:
    """If the field's default is a `Field(...)` call, check the call.
    A Literal / Enum / None default is considered 'bounded' because
    the annotation itself constrains the shape."""
    if default is None:
        return False
    if isinstance(default, ast.Call):
        fn = default.func
        is_field_call = False
        if isinstance(fn, ast.Name) and fn.id == "Field":
            is_field_call = True
        elif isinstance(fn, ast.Attribute) and fn.attr == "Field":
            is_field_call = True
        if is_field_call:
            return _field_call_has_bound(default, kind)
    return False


def _annotation_is_bounded_shape(ann: ast.AST) -> bool:
    """Literal[...], constrained types like constr(...), Enum subclass
    references are self-bounded and don't need an explicit max_length."""
    txt = _ann_text(ann)
    if "Literal[" in txt:
        return True
    if "constr(" in txt or "conint(" in txt or "conlist(" in txt:
        return True
    if "StrictBool" in txt or "EmailStr" in txt or "HttpUrl" in txt:
        return True
    # UUID / datetime / IP addresses are self-bounded
    if any(t in txt for t in ("UUID", "datetime", "date", "IPv4", "IPv6", "bool", "int", "float")):
        return True
    return False


def _classify(ann: ast.AST) -> str | None:
    txt = _ann_text(ann).strip()
    if _annotation_is_bounded_shape(ann):
        return None
    if txt in STRING_ANNOTATIONS or txt.startswith("str"):
        return "str_unbounded"
    if any(txt.startswith(p) for p in LIST_PREFIXES):
        return "list_unbounded"
    if any(txt.startswith(p) for p in DICT_PREFIXES):
        return "dict_unbounded"
    return None


_RESPONSE_SUFFIXES = (
    "Response", "Out", "Row", "Summary", "Result", "Reply",
    "Item", "Entry", "Metric", "Record", "Card", "Tile",
)
_REQUEST_SUFFIXES = (
    "Request", "Input", "Create", "Update", "Patch", "Body",
    "Payload", "Params", "Event",
)


def _is_request_model(class_name: str) -> bool:
    """Heuristic: only flag models whose name looks like a request
    body. Response models exit our process — their fields come from
    trusted DB data, not untrusted user input, so unbounded strings
    there are a client-side concern, not a server-side injection risk."""
    if any(class_name.endswith(s) for s in _RESPONSE_SUFFIXES):
        return False
    if any(class_name.endswith(s) for s in _REQUEST_SUFFIXES):
        return True
    # Neither suffix matched — be conservative and do NOT flag.
    # These ambiguous classes can be hand-audited via --all.
    return False


def scan_class(cls: ast.ClassDef, rel: str, scan_all: bool = False) -> list[Finding]:
    # Is it a BaseModel subclass? Cheap heuristic: at least one base
    # named BaseModel (or ends in .BaseModel).
    is_basemodel = False
    for base in cls.bases:
        name = _ann_text(base)
        if name == "BaseModel" or name.endswith(".BaseModel"):
            is_basemodel = True
            break
    if not is_basemodel:
        return []

    if not scan_all and not _is_request_model(cls.name):
        return []

    findings: list[Finding] = []
    for stmt in cls.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        if not isinstance(stmt.target, ast.Name):
            continue
        field_name = stmt.target.id
        kind = _classify(stmt.annotation)
        if kind is None:
            continue
        if _field_default_is_bounded(stmt.value, kind):
            continue
        findings.append(Finding(
            rel, stmt.lineno, cls.name, field_name,
            _ann_text(stmt.annotation), kind,
        ))
    return findings


def scan_file(path: pathlib.Path, scan_all: bool = False) -> list[Finding]:
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return []
    rel = path.relative_to(APP_ROOT.parent).as_posix()
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            findings.extend(scan_class(node, rel, scan_all=scan_all))
    return findings


def walk(scan_all: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    for root in SCAN_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            findings.extend(scan_file(path, scan_all=scan_all))
    return findings


def main() -> int:
    scan_all = "--all" in sys.argv
    findings = walk(scan_all=scan_all)
    by_kind = Counter(f.kind for f in findings)
    by_file = defaultdict(list)
    for f in findings:
        by_file[f.file].append(f)

    print(f"audit_input_bounds: scanned {[str(d) for d in SCAN_DIRS]}")
    print(f"  total unbounded fields: {len(findings)}")
    print(f"    str_unbounded : {by_kind.get('str_unbounded', 0)}")
    print(f"    list_unbounded: {by_kind.get('list_unbounded', 0)}")
    print(f"    dict_unbounded: {by_kind.get('dict_unbounded', 0)}")
    print()

    if findings:
        print("Top files by unbounded-field count:")
        ranked = sorted(by_file.items(), key=lambda kv: len(kv[1]), reverse=True)
        for file, items in ranked[:20]:
            print(f"  {len(items):3d}  {file}")
        print()

    if "--detail" in sys.argv:
        print("All sites:")
        for f in sorted(findings, key=lambda x: (x.file, x.line)):
            print(f"  {f.file}:{f.line}  {f.class_name}.{f.field_name}: {f.annotation}  [{f.kind}]")

    strict = "--strict" in sys.argv
    if strict and findings:
        print(f"FAIL: {len(findings)} unbounded fields remain (target: 0)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
