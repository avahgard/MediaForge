"""Lossless bounded probe records, explicit portable child context and reaping."""

from queue import Empty, Full, Queue
import subprocess
from threading import Event, Thread
import time

from mediaforge.features.dub.contracts import DubError, Reason


class ProbeProcess:
    def __init__(self, factory, paths, control, timeout=300):
        self.factory, self.paths, self.control, self.timeout = factory, paths, control, timeout

    def lines(self, command, *, line_limit=8192):
        control = self.control
        control.check()
        abort = Event()
        queue = Queue(maxsize=32)
        diagnostic = bytearray()
        readers = []
        process = None

        def stdout():
            try:
                while not abort.is_set():
                    record = process.stdout.readline(line_limit + 1)
                    item = record if len(record) <= line_limit else DubError(Reason.PROBE_FAILED, 'Probe record exceeds bound.')
                    while not abort.is_set():
                        try:
                            queue.put(item or None, timeout=.05)
                            break
                        except Full:
                            pass
                    if not record or isinstance(item, Exception):
                        break
            except Exception as error:
                while not abort.is_set():
                    try:
                        queue.put(error, timeout=.05)
                        break
                    except Full:
                        pass

        def stderr():
            while block := process.stderr.read(4096):
                diagnostic.extend(block[:max(0, 8192 - len(diagnostic))])

        try:
            with control.starting():
                process = self.factory(command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                    cwd=self.paths.temporary, env=self.paths.child_environment(),
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            readers = [Thread(target=stdout, daemon=True), Thread(target=stderr, daemon=True)]
            for reader in readers:
                reader.start()
            deadline = time.monotonic() + self.timeout
            while True:
                control.check()
                if time.monotonic() >= deadline:
                    raise DubError(Reason.TIMEOUT, 'Source scan exceeded the inspection deadline.')
                try:
                    record = queue.get(timeout=.05)
                except Empty:
                    continue
                if isinstance(record, Exception):
                    raise DubError(Reason.PROBE_FAILED, 'Source probe record could not be read.') from record
                if record is None:
                    break
                yield record
            process.wait(timeout=2)
            for reader in readers:
                reader.join(timeout=2)
            if process.returncode or diagnostic:
                raise DubError(Reason.PROBE_FAILED, 'Source failed complete inspection/decoding; no output is qualified.')
        except OSError as error:
            raise DubError(Reason.PROBE_FAILED, 'Managed probe could not be launched.') from error
        except subprocess.TimeoutExpired as error:
            raise DubError(Reason.TIMEOUT, 'Managed probe did not finish.') from error
        finally:
            abort.set()
            if process is not None:
                try:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                    for reader in readers:
                        reader.join(timeout=2)
                    if any(reader.is_alive() for reader in readers):
                        raise RuntimeError('Probe reader remains owned.')
                    for pipe in (process.stdout, process.stderr):
                        pipe.close()
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    control.retain((process, readers))
                    raise DubError(Reason.LIFECYCLE_FAILED, 'Probe ownership is unresolved; disable processing.',
                                   retained_resources=(process, *readers)) from error
