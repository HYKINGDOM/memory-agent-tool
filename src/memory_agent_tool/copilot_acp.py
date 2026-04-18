from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


def _resolve_command() -> str:
    return os.getenv("MEMORY_AGENT_TOOL_COPILOT_COMMAND", "").strip() or "copilot"


def _resolve_args() -> list[str]:
    raw = os.getenv("MEMORY_AGENT_TOOL_COPILOT_ARGS", "").strip()
    if raw:
        return shlex.split(raw)
    return ["--acp", "--stdio", "--allow-all-tools", "--allow-all-paths", "--allow-all-urls", "--no-ask-user"]


class CopilotACPError(RuntimeError):
    pass


class CopilotACPClient:
    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | None = None,
    ):
        self.command = command or _resolve_command()
        self.args = list(args or _resolve_args())
        self.cwd = str(Path(cwd or os.getcwd()).resolve())
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._started_readers = False

    def close(self) -> None:
        with self._lock:
            proc = self._process
            self._process = None
            self._started_readers = False
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def start(self) -> None:
        try:
            proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self.cwd,
            )
        except FileNotFoundError as exc:
            raise CopilotACPError(
                f"Could not start Copilot ACP command '{self.command}'."
            ) from exc
        if proc.stdin is None or proc.stdout is None:
            proc.kill()
            raise CopilotACPError("Copilot ACP process did not expose stdin/stdout pipes.")
        with self._lock:
            self._process = proc
        self._start_readers(proc)

    def _start_readers(self, proc: subprocess.Popen[str]) -> None:
        if self._started_readers:
            return

        def _stdout_reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    self._inbox.put(json.loads(line))
                except Exception:
                    self._inbox.put({"raw": line.rstrip("\n")})

        def _stderr_reader() -> None:
            if proc.stderr is None:
                return
            for line in proc.stderr:
                self._stderr_tail.append(line.rstrip("\n"))

        threading.Thread(target=_stdout_reader, daemon=True).start()
        threading.Thread(target=_stderr_reader, daemon=True).start()
        self._started_readers = True

    def request(
        self,
        request_id: int,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
        text_parts: list[str] | None = None,
        extra_handlers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proc = self._process
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise CopilotACPError("Copilot ACP process is not running.")

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                msg = self._inbox.get(timeout=0.1)
            except queue.Empty:
                continue
            if self._handle_server_message(msg, process=proc, text_parts=text_parts, extra_handlers=extra_handlers):
                continue
            if msg.get("id") != request_id:
                continue
            if "error" in msg:
                err = msg.get("error") or {}
                raise CopilotACPError(f"Copilot ACP {method} failed: {err.get('message') or err}")
            return msg

        stderr_text = "\n".join(self._stderr_tail).strip()
        if proc.poll() is not None and stderr_text:
            raise CopilotACPError(f"Copilot ACP process exited early: {stderr_text}")
        raise TimeoutError(f"Timed out waiting for Copilot ACP response to {method}.")

    def _handle_server_message(
        self,
        msg: dict[str, Any],
        *,
        process: subprocess.Popen[str],
        text_parts: list[str] | None,
        extra_handlers: dict[str, Any] | None,
    ) -> bool:
        method = msg.get("method")
        if not isinstance(method, str):
            return False

        if method == "session/update":
            params = msg.get("params") or {}
            update = params.get("update") or {}
            kind = str(update.get("sessionUpdate") or "").strip()
            content = update.get("content") or {}
            if kind == "agent_message_chunk" and isinstance(content, dict) and text_parts is not None:
                text = str(content.get("text") or "")
                if text:
                    text_parts.append(text)
            return True

        if extra_handlers and method in extra_handlers:
            return bool(extra_handlers[method](msg, process))

        if process.stdin is None:
            return True

        response = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {
                "outcome": {
                    "outcome": "allow_once",
                }
            },
        }
        if method != "session/request_permission":
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {
                    "code": -32601,
                    "message": f"ACP client method '{method}' is not supported.",
                },
            }
        process.stdin.write(json.dumps(response) + "\n")
        process.stdin.flush()
        return True
