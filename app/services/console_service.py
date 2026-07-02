from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
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
    """
    Manages per-VM noVNC/websockify proxy processes.

    Console proxies are intentionally treated as disposable. A VM reboot can
    invalidate the VNC backend while leaving websockify alive, so each console
    launch tears down the old per-VM proxy and starts a fresh one against the
    VM's current VNC display.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.run_dir = Path('/run/atlasvm')
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def start_novnc(self, vm_name: str, vnc_display: str, request_host: str | None = None) -> ConsoleSession:
        """
        Start a fresh noVNC proxy for the VM and return browser connection info.

        Important behavior:
        - Always removes the existing proxy for this VM first.
        - Recalculates the VNC port from the current libvirt display.
        - Writes a PID file only after the new proxy starts.
        """
        pid_file = self._pid_file(vm_name)

        # A previous VM restart can leave websockify running but disconnected
        # from the current VNC backend. Kill the old per-VM proxy every time.
        self._kill_console_pid(pid_file)

        vnc_port = self._display_to_port(vnc_display)
        proxy_port = self._choose_proxy_port(vm_name)

        command = self._novnc_command(proxy_port, vnc_port)
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        pid_file.write_text(str(process.pid))

        public_host = self.settings.console_public_host or request_host or 'localhost'
        url = f'http://{public_host}:{proxy_port}/vnc.html?host={public_host}&port={proxy_port}&autoconnect=1&resize=scale'

        return ConsoleSession(
            vm_name=vm_name,
            vnc_display=vnc_display,
            vnc_port=vnc_port,
            proxy_port=proxy_port,
            url=url,
            pid=process.pid,
        )

    def stop_novnc(self, vm_name: str) -> None:
        """Stop the noVNC proxy associated with a VM, if one exists."""
        self._kill_console_pid(self._pid_file(vm_name))

    def cleanup_all(self) -> None:
        """
        Remove dead PID files and stop live AtlasVM console proxies.

        This is safe to call during service startup/shutdown. It only kills
        processes whose command line looks like websockify/noVNC.
        """
        for pid_file in self.run_dir.glob('console-*.pid'):
            self._kill_console_pid(pid_file)

    def cleanup_dead_pid_files(self) -> None:
        """Remove stale PID files without killing live console proxies."""
        for pid_file in self.run_dir.glob('console-*.pid'):
            pid = self._read_pid(pid_file)
            if pid is None or not self._pid_exists(pid):
                self._unlink_pid_file(pid_file)

    def _novnc_command(self, proxy_port: int, vnc_port: int) -> list[str]:
        return [
            'websockify',
            '--web',
            '/usr/share/novnc',
            f'{self.settings.console_bind_host}:{proxy_port}',
            f'127.0.0.1:{vnc_port}',
        ]

    def _choose_proxy_port(self, vm_name: str) -> int:
        base = self.settings.console_port_base
        maximum = self.settings.console_port_max
        span = max(1, maximum - base + 1)
        first = base + (sum(ord(c) for c in vm_name) % span)

        for port in list(range(first, maximum + 1)) + list(range(base, first)):
            if self._port_available(port):
                return port

        raise RuntimeError('No available noVNC proxy ports')

    def _port_available(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex(('127.0.0.1', port)) != 0

    def _display_to_port(self, display: str) -> int:
        display = str(display).strip()
        if display.startswith(':'):
            return 5900 + int(display[1:])
        value = int(display)
        if value < 100:
            return 5900 + value
        return value

    def _safe(self, name: str) -> str:
        return ''.join(c if c.isalnum() or c in '-_' else '_' for c in name)

    def _pid_file(self, vm_name: str) -> Path:
        return self.run_dir / f'console-{self._safe(vm_name)}.pid'

    def _read_pid(self, pid_file: Path) -> int | None:
        try:
            return int(pid_file.read_text().strip())
        except Exception:
            return None

    def _pid_exists(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _pid_is_console_process(self, pid: int) -> bool:
        """
        Make sure we only kill the console proxy we meant to kill.

        PID files can become stale and Linux can reuse PIDs. This prevents
        AtlasVM from terminating an unrelated process just because the PID file
        is old.
        """
        try:
            cmdline = Path(f'/proc/{pid}/cmdline').read_text(errors='ignore')
        except Exception:
            return False

        return 'websockify' in cmdline or 'novnc' in cmdline

    def _kill_console_pid(self, pid_file: Path) -> None:
        pid = self._read_pid(pid_file)

        if pid is not None and self._pid_exists(pid):
            if self._pid_is_console_process(pid):
                self._terminate_process_group(pid)

        self._unlink_pid_file(pid_file)

    def _terminate_process_group(self, pid: int) -> None:
        """
        Terminate the proxy process group.

        start_new_session=True creates a new process group/session, so killing
        the group is cleaner than only killing the parent process.
        """
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return

        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not self._pid_exists(pid):
                return
            time.sleep(0.05)

        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def _unlink_pid_file(self, pid_file: Path) -> None:
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass
