#!/usr/bin/env python3
from app.services.doctor_service import run_doctor

for check in run_doctor():
    mark = 'OK' if check['ok'] else 'WARN'
    print(f"[{mark}] {check['name']}: {check['detail']}")
