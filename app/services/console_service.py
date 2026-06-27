from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings


@dataclass
class ConsoleSession:
    vm_name: str
    vnc_display: str
    vnc_port: int
    proxy_port: int
    url: str
    pid: int | None


class ConsoleService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.run_dir = Path('/run/atlasvm')
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def start_novnc(self, vm_name: str, vnc_display: str, request_host: str | None = None) -> ConsoleSession:
        vnc_port = self._display_to_port(vnc_display)
        proxy_port = self._choose_proxy_port(vm_name)
        pid_file = self.run_dir / f'console-{self._safe(vm_name)}.pid'

        if not self._pid_running(pid_file):
            self._kill_stale_pid(pid_file)
            command = self._novnc_command(proxy_port, vnc_port)
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            pid_file.write_text(str(process.pid))

        public_host = self.settings.console_public_host or request_host or 'localhost'
        url = f'http://{public_host}:{proxy_port}/vnc.html?autoconnect=1&resize=scale'
        return ConsoleSession(vm_name=vm_name, vnc_display=vnc_display, vnc_port=vnc_port, proxy_port=proxy_port, url=url, pid=self._read_pid(pid_file))

    def stop_novnc(self, vm_name: str) -> None:
        pid_file = self.run_dir / f'console-{self._safe(vm_name)}.pid'
        self._kill_stale_pid(pid_file)

    def _novnc_command(self, proxy_port: int, vnc_port: int) -> list[str]:
        candidates = [
            '/usr/share/novnc/utils/novnc_proxy',
            '/usr/share/novnc/utils/launch.sh',
        ]
        for path in candidates:
            if Path(path).exists():
                return [path, '--listen', f'{self.settings.console_bind_host}:{proxy_port}', '--vnc', f'127.0.0.1:{vnc_port}']
        return ['websockify', '--web', '/usr/share/novnc', f'{self.settings.console_bind_host}:{proxy_port}', f'127.0.0.1:{vnc_port}']

    def _choose_proxy_port(self, vm_name: str) -> int:
        base = self.settings.console_port_base
        maximum = self.settings.console_port_max
        span = max(1, maximum - base + 1)
        first = base + (sum(ord(c) for c in vm_name) % span)
        for port in list(range(first, maximum + 1)) + list(range(base, first)):
            if self._port_available(port) or self._port_owned_by_vm(vm_name):
                return port
        raise RuntimeError('No available noVNC proxy ports')

    def _port_available(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex(('127.0.0.1', port)) != 0

    def _port_owned_by_vm(self, vm_name: str) -> bool:
        pid_file = self.run_dir / f'console-{self._safe(vm_name)}.pid'
        return self._pid_running(pid_file)

    def _display_to_port(self, display: str) -> int:
        if display.startswith(':'):
            return 5900 + int(display[1:])
        return int(display)

    def _safe(self, name: str) -> str:
        return ''.join(c if c.isalnum() or c in '-_' else '_' for c in name)

    def _read_pid(self, pid_file: Path) -> int | None:
        try:
            return int(pid_file.read_text().strip())
        except Exception:
            return None

    def _pid_running(self, pid_file: Path) -> bool:
        pid = self._read_pid(pid_file)
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _kill_stale_pid(self, pid_file: Path) -> None:
        pid = self._read_pid(pid_file)
        if pid is not None:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass
