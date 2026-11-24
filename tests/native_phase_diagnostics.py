"""Test-only phase journal and fail-fast watchdog for native ownership probes.

Qt is inspected only on its owning thread. The watchdog retains the latest Qt
snapshot and inspects Python/child ownership without acquiring product locks.
The native faulthandler deadline also works when a C extension holds the GIL.
Neither deadline can produce a successful test result.
"""
from contextlib import contextmanager
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


class NativePhaseDiagnostics:
    def __init__(self, output, *, timeout=60, native_timeout=90):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.timeout, self.native_timeout = timeout, native_timeout
        self.case, self.phase = "startup", "diagnostic-start"
        self.entered = time.monotonic()
        self.qt_snapshot = {}
        self.bindings = {}
        self.processes = []
        self._factories = []
        self._stop = threading.Event()
        self._journal = (self.output / "phases.jsonl").open("a", encoding="utf-8", buffering=1)
        self._stacks = (self.output / "thread-stacks.log").open("a", encoding="utf-8", buffering=1)
        self._original_popen = subprocess.Popen
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(line_buffering=True, write_through=True)

        def owned_spawn(*args, **kwargs):
            # Persist intent before launch: a blocked CreateProcess also leaves
            # an actionable phase, command and Python stack.
            self._write("subprocess-enter", command=repr(args[0] if args else kwargs.get("args")))
            child = self._original_popen(*args, **kwargs)
            self._track(child)
            self._write("subprocess-exit", pid=child.pid)
            return child

        subprocess.Popen = owned_spawn
        self._write("watchdog-armed", timeout_s=timeout, native_timeout_s=native_timeout)
        faulthandler.dump_traceback_later(native_timeout, file=self._stacks, exit=True)
        self._watchdog = threading.Thread(target=self._watch, name="native-phase-watchdog", daemon=True)
        self._watchdog.start()

    def bind(self, **objects):
        self.bindings.update(objects)

    def _track(self, child):
        if child not in self.processes:
            self.processes.append(child)

    def track_factory(self, owner, attribute):
        """Cover injected factories whose default was captured before import."""
        original = getattr(owner, attribute)

        def spawned(*args, **kwargs):
            self._write("owned-factory-enter", command=repr(args[0] if args else kwargs.get("args")),
                        factory=attribute)
            child = original(*args, **kwargs)
            self._track(child)
            self._write("owned-factory-exit", pid=child.pid, factory=attribute)
            return child

        setattr(owner, attribute, spawned)
        self._factories.append((owner, attribute, original))

    def _ownership(self):
        p, service, observer = (self.bindings.get(name) for name in ("playback", "service", "observer"))
        control = getattr(service, "_current", None)
        executor = getattr(service, "_executor", None)
        timeline = getattr(p, "_timeline", None)
        model = self.bindings.get("model")
        metadata = getattr(getattr(model, "_use_case", None), "_probe", None)
        return {
            "playback_python": {name: repr(getattr(p, name, None)) for name in (
                "_state", "_closed", "_source_generation", "_timeline_generation", "_origin_generation",
                "_presented_start", "_first_presented_start", "_origin_established", "_timeline_offset",
                "_indexing", "_current_index", "_step_serial", "_step_pending", "_step_target_index",
                "_step_expected_start", "_queued_step", "_seek_waiting", "_shutdown_pending", "_shutdown_error")},
            "retained_timeline": {"count": timeline.count, "storage_bytes": timeline.storage_bytes} if timeline else None,
            "callbacks": {"player_handlers": list(getattr(p, "_player_signal_handlers", {})),
                          "frame_handler": getattr(p, "_frame_handler", None) is not None},
            "metadata": {"child_pid": getattr(getattr(metadata, "_process", None), "pid", None),
                         "lock_held": metadata._process_lock.locked() if metadata else None},
            "timeline": {"service_id": id(service), "closed": getattr(service, "_closed", None),
                "generation": getattr(service, "_generation", None),
                "cleanup_error": getattr(service, "_cleanup_error", None),
                "active_request": repr(getattr(service, "_active_request", None)),
                "current_request": repr(getattr(service, "_current_request", None)),
                "pending": repr(getattr(service, "_pending", None)),
                "control_id": id(control), "child_pid": getattr(getattr(control, "_process", None), "pid", None),
                "cancelled": control.cancelled.is_set() if control else None,
                "service_lock_held": service._lock.locked() if service else None,
                "control_lock_held": control._lock.locked() if control else None,
                "workers": [{"name": t.name, "ident": t.ident, "alive": t.is_alive()}
                            for t in tuple(getattr(executor, "_threads", ()))],
                "queued_work": executor._work_queue.qsize() if executor else None},
            "settlement": {name: repr(getattr(observer, name, None)) for name in (
                "revision", "_failure", "_delivered", "_observed_start", "_after_revision", "_loop", "_closed")},
            "owned_subprocesses": [{"pid": child.pid, "command": repr(child.args),
                                    "returncode": child.poll()} for child in tuple(self.processes)],
        }

    def _qt(self):
        # Called by phase enter/exit on the GUI thread, never by the watchdog.
        import shiboken6
        p, app, window = (self.bindings.get(name) for name in ("playback", "app", "window"))
        result = {}
        if p is not None and shiboken6.isValid(p):
            player = p._player
            result.update(public_state=p.state, public_position=p.position, label=p.frameText,
                          frames_reliable=p.framesReliable, exact_step=p.exactStepAvailable,
                          backend_python_id=id(player), backend_disposed=not shiboken6.isValid(player))
            if shiboken6.isValid(player):
                result['backend_cpp_id']=shiboken6.getCppPointer(player)[0]
                result["player"] = {"state": player.playbackState().name, "status": player.mediaStatus().name,
                "position": player.position(), "source": player.source().toString(),
                "error": player.error().name, "error_string": player.errorString(),
                # QMediaPlayer.videoSink() has PySide keep-reference side effects
                # on native replacement. Reading QObject properties does not
                # acquire that wrapper ownership; inspect the retained QML sink.
                "audio_attached": player.property("audioOutput") is not None,
                "video_attached": player.property("videoOutput") is not None}
            sink = self.bindings.get("sink") or p._presentation_sink
            result["frame"] = ({"valid": sink.videoFrame().isValid(), "start": sink.videoFrame().startTime(),
                               "end": sink.videoFrame().endTime(), "sink_cpp_id": shiboken6.getCppPointer(sink)[0]}
                               if sink is not None and shiboken6.isValid(sink) else None)
            result["step_timer"] = {"active": p._step_timer.isActive(), "remaining_ms": p._step_timer.remainingTime()}
        if window is not None and shiboken6.isValid(window):
            focus = window.activeFocusItem()
            result["window"] = {"visible": window.isVisible(), "active": window.isActive(),
                "visibility": window.visibility().name, "width": window.width(), "height": window.height(),
                "focus": focus.objectName() if focus else None, "dpr": window.devicePixelRatio()}
        if app is not None and shiboken6.isValid(app):
            result["application"] = {"state": app.applicationState().name,
                                     "focus_window_id": id(app.focusWindow()), "closing_down": app.closingDown()}
        self.qt_snapshot = result

    def _write(self, event, **extra):
        record = {"case": self.case, "phase": self.phase, "event": event,
                  "monotonic": time.monotonic(), "qt_cached_at_phase": self.qt_snapshot,
                  **self._ownership(), **extra}
        line = json.dumps(record, default=repr)
        self._journal.write(line + "\n")
        self._journal.flush()
        print(json.dumps({key: record[key] for key in ("case", "phase", "event", "monotonic")}), flush=True)
        return record

    @contextmanager
    def phase_scope(self, name):
        previous = self.phase
        self.phase, self.entered = name, time.monotonic()
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(self.native_timeout, file=self._stacks, exit=True)
        self._write("phase-enter")  # Written before any potentially blocked Qt inspection.
        self._stacks.write(f"\ncase={self.case} phase={name} monotonic={self.entered}\n")
        faulthandler.dump_traceback(file=self._stacks, all_threads=True)
        self._stacks.flush()
        self._qt()
        self._write("phase-snapshot")
        try:
            yield
        except BaseException as error:
            self._write("phase-failure", error=repr(error))
            raise
        else:
            self._write("phase-exit")
            self._qt()
            self._write("phase-exit-snapshot")
        finally:
            self.phase, self.entered = previous, time.monotonic()

    def call(self, name, operation):
        with self.phase_scope(name):
            return operation()

    def _watch(self):
        while not self._stop.wait(min(self.timeout / 4, 1)):
            if time.monotonic() - self.entered < self.timeout:
                continue
            record = self._write("WATCHDOG-FAILURE", gui_snapshot_is_cached=True)
            (self.output / "watchdog.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            faulthandler.dump_traceback(file=self._stacks, all_threads=True)
            self._stacks.flush()
            # Own only children launched by this harness. A blocked Qt path may
            # otherwise orphan its actual scan when the diagnostic exits.
            for child in tuple(self.processes):
                if child.poll() is None:
                    try:
                        child.terminate()
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=2)
            self._write("watchdog-owned-child-cleanup")
            os._exit(124)

    def close(self):
        app = self.bindings.get("app")
        if app is not None:
            import shiboken6
            if shiboken6.isValid(app):
                # Binding the app for snapshots retains it past run(). Make
                # the otherwise implicit, harness-owned destructor explicit
                # and watchdog-covered before disarming diagnostics.
                self.call("application-delete", lambda: shiboken6.delete(app))
            self.bind(app=None)
        self._stop.set()
        self._watchdog.join(timeout=2)
        faulthandler.cancel_dump_traceback_later()
        subprocess.Popen = self._original_popen
        for owner, attribute, original in self._factories:
            setattr(owner, attribute, original)
        self._write("diagnostic-close")
        self._journal.close()
        self._stacks.close()
