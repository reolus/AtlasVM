#!/usr/bin/env python3
from app.services.doctor_service import run_doctor

for check in run_doctor():
    severity = (check.get('severity') or check.get('status') or 'warning').upper()
    category = check.get('category') or 'General'
    name = check.get('name') or 'check'
    detail = check.get('detail') or ''
    print(f"[{severity}] {category} - {name}: {detail}")
