"""Bounded child ownership shared by Media operations."""

import subprocess
import time
from threading import Thread
from queue import Queue, Empty, Full

STOP_TIMEOUT_SECONDS = 2.0
POLL_SECONDS = 0.05


class ChildProcesses:
    def __init__(self, factory, error, logger):
        self._factory = factory
        self._error = error
        self._logger = logger
        self._unreaped_process = None

    def check_reaped(self):
        if self._unreaped_process is not None:
            if self._unreaped_process.poll() is None:
                raise self._error("lifecycle_failed")
            self._unreaped_process = None

    def run(self, command, control, timeout, validating=False, output=None, on_output=None, monitor=None,
            *, stdin=subprocess.DEVNULL):
        control.check()
        try:
            # The same gate covers cancellation acceptance and creation/ownership
            # for both FFmpeg and FFprobe. A preceding check alone is not enough.
            with control.starting():
                process = self._factory(command, stdin=stdin,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", errors="replace", shell=False,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if output is not None:
                    output.cleanup_guard = lambda: process.poll() is not None
        except OSError as error:
            raise self._error("start_failed") from error
        deadline = time.monotonic() + timeout
        next_sample = time.monotonic() + 1
        readers = []
        pending_output = Queue(maxsize=1)
        diagnostic = ['']
        if on_output is not None:
            # Stream telemetry without communicate()'s Windows EOF buffering or
            # retaining an unbounded per-frame log. Ownership stays in this manager.
            def read_output():
                for line in process.stdout:
                    try: pending_output.put_nowait(line)
                    except Full:
                        try: pending_output.get_nowait()
                        except Empty: pass
                        try: pending_output.put_nowait(line)
                        except Full: pass
            def read_error():
                while chunk := process.stderr.read(4096):
                    diagnostic[0]=(diagnostic[0]+chunk)[-8192:]
            readers=[Thread(target=read_output,daemon=True),Thread(target=read_error,daemon=True)]
            for reader in readers: reader.start()
        try:
            while True:
                control.check()
                now = time.monotonic()
                if monitor is not None and now >= next_sample:
                    monitor()
                    next_sample = now + 1
                remaining = deadline - now
                if remaining <= 0:
                    raise self._error("timeout")
                if on_output is not None:
                    try: on_output(pending_output.get_nowait())
                    except Empty: pass
                    if process.poll() is not None:
                        for reader in readers: reader.join(STOP_TIMEOUT_SECONDS)
                        try: on_output(pending_output.get_nowait())
                        except Empty: pass
                        stdout,stderr='',diagnostic[0]
                        break
                    time.sleep(min(POLL_SECONDS,remaining))
                    continue
                try:
                    stdout, stderr = process.communicate(timeout=min(POLL_SECONDS, remaining))
                    break
                except subprocess.TimeoutExpired as pending:
                    if on_output is not None and pending.output:
                        on_output(pending.output)
                    continue
            control.check()
            if process.returncode != 0:
                self._logger.error("Media process exit %s: %s", process.returncode, stderr)
                raise self._error("validation_failed" if validating else "process_failed")
            return stdout
        finally:
            # Always reap before the enclosing staging context can clean up.
            if process.poll() is None:
                try:
                    self._stop(process)
                except (OSError, subprocess.SubprocessError) as error:
                    self._unreaped_process = process
                    control.retain_unreaped_child()
                    raise self._error("lifecycle_failed") from error
            for reader in readers: reader.join(STOP_TIMEOUT_SECONDS)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def _stop(self, process):
        try:
            process.terminate()
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            if process.poll() is None:
                self._logger.warning("Media process did not terminate; forcing kill")
                process.kill()
                process.wait(timeout=STOP_TIMEOUT_SECONDS)
