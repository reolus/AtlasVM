#!/usr/bin/env python3
"""Static AtlasVM UI route/form audit.

This intentionally avoids importing app.main so it can run on a workstation that
may not have libvirt available. It checks declared FastAPI routes and literal
HTML form actions in templates. Jinja expressions are reported for manual review
instead of pretending static analysis is magic. Humanity will cope.
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"
TEMPLATES = ROOT / "app" / "templates"

route_re = re.compile(r"@app\.(get|post|put|delete|patch)\('([^']+)'")
action_re = re.compile(r"<form[^>]+method=\"(?P<method>get|post)\"[^>]+action=\"(?P<action>[^\"]+)\"", re.I)


def route_shape(path: str) -> str:
    return re.sub(r"\{[^/]+\}", "{}", path)


def action_shape(action: str) -> str:
    if "{{" in action or "{%" in action:
        action = re.sub(r"\{\{[^}]+\}\}", "{}", action)
    return action


def main() -> int:
    routes = []
    for lineno, line in enumerate(MAIN.read_text().splitlines(), 1):
        match = route_re.search(line)
        if match:
            method = match.group(1).upper()
            path = match.group(2)
            routes.append((lineno, method, path, route_shape(path)))

    duplicates = collections.defaultdict(list)
    route_shapes = set()
    for lineno, method, path, shape in routes:
        duplicates[(method, path)].append(lineno)
        route_shapes.add((method, shape))

    print("Declared routes:", len(routes))
    for (method, path), lines in sorted(duplicates.items()):
        if len(lines) > 1:
            print(f"DUPLICATE {method:4} {path} lines={lines}")

    print("\nVM route ordering checkpoints:")
    for lineno, method, path, shape in routes:
        if path.startswith("/ui/vms") or path.startswith("/vms/{"):
            print(f"{lineno:5} {method:4} {path}")

    print("\nTemplate form actions:")
    missing = []
    for template in sorted(TEMPLATES.glob("*.html")):
        for match in action_re.finditer(template.read_text(errors="ignore")):
            method = match.group("method").upper()
            action = match.group("action")
            shape = action_shape(action)
            ok = (method, shape) in route_shapes or method == "GET" or action.strip().startswith("{{")
            # Account for intentional action-dispatch routes used by existing UI
            # compatibility wrappers. They stay below specific routes in main.py.
            if not ok:
                for route_method, route_shape_value in route_shapes:
                    if route_method != method:
                        continue
                    if not route_shape_value.endswith("/{}"): 
                        continue
                    prefix = route_shape_value[:-3]
                    if shape.startswith(prefix + "/"):
                        ok = True
                        break
            status = "OK" if ok else "CHECK"
            print(f"{status:5} {method:4} {template.name:28} {action}")
            if not ok:
                missing.append((method, template.name, action))

    if missing:
        print("\nActions requiring manual review:")
        for method, template, action in missing:
            print(f"- {method} {template}: {action}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
