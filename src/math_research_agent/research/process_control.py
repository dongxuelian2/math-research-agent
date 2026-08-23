"""Cross-platform subprocess-group creation and deterministic termination."""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any


class ProcessTerminationBackend:
    """Terminate a provider process tree without hard-coding POSIX APIs."""

    def creation_kwargs(self, *, hidden: bool = True) -> dict[str, Any]:
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if hidden:
                flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            return {"creationflags": flags, "start_new_session": False}
        return {"creationflags": 0, "start_new_session": True}

    def terminate_tree(self, process: Any, *, force: bool = True, timeout: float = 5.0) -> dict:
        if process.poll() is not None:
            return {
                "requested": False,
                "platform": os.name,
                "method": "already-exited",
                "returncode": process.returncode,
            }
        method = "process"
        if os.name == "nt" and getattr(process, "pid", None):
            try:
                completed = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", *(["/F"] if force else [])],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=max(1.0, timeout),
                    check=False,
                    shell=False,
                )
                method = "taskkill-tree"
                if completed.returncode == 0:
                    process.wait(timeout=timeout)
                    return {
                        "requested": True,
                        "platform": os.name,
                        "method": method,
                        "returncode": process.returncode,
                    }
            except (OSError, subprocess.SubprocessError):
                method = "taskkill-fallback"
        elif getattr(process, "pid", None) and hasattr(os, "killpg"):
            try:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                process.wait(timeout=timeout)
                return {
                    "requested": True,
                    "platform": os.name,
                    "method": "posix-process-group",
                    "returncode": process.returncode,
                }
            except (OSError, ProcessLookupError, subprocess.SubprocessError):
                method = "posix-process-fallback"
        try:
            process.terminate()
            process.wait(timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
                process.wait(timeout=timeout)
                method += "-kill"
            except (OSError, subprocess.SubprocessError):
                method += "-failed"
        return {
            "requested": True,
            "platform": os.name,
            "method": method,
            "returncode": process.returncode,
        }

    @staticmethod
    def termination_succeeded(process: Any) -> bool:
        """Windows exit codes need not be negative; non-running is the invariant."""

        return process.poll() is not None
