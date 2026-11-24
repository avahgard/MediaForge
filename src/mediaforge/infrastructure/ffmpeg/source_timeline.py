"""One streaming first-video frame scan using the injected authenticated runtime."""
from array import array
from fractions import Fraction
from queue import Queue, Empty, Full
from threading import Event, Thread
import subprocess
import time

from mediaforge.application.contracts.source_timeline import (
    SourceSignature, SourcePresentationTimeline, TimelineResult, TIMELINE_BYTES_LIMIT,
)

INDEX_TIMEOUT_SECONDS = 300.0


class FfprobeTimelineIndexer:
    def __init__(self, executable, *, process_factory=subprocess.Popen, decoder_threads=None,maximum_bytes=TIMELINE_BYTES_LIMIT):
        self._executable, self._factory = executable, process_factory
        self._decoder_threads=decoder_threads
        self._maximum_bytes=min(TIMELINE_BYTES_LIMIT,maximum_bytes)

    def build(self, request, control):
        process = None
        abort = Event()
        lines = Queue(maxsize=32)
        diagnostic = bytearray()
        readers = []
        def consume_stdout(pipe):
            try:
                while not abort.is_set():
                    line = pipe.readline(1024)
                    if len(line)==1024 and not line.endswith(b'\n'):
                        line = b'INVALID oversized timing record\n'
                    while not abort.is_set():
                        try:
                            lines.put(line or None, timeout=.05)
                            break
                        except Full: pass
                    if not line: break
            finally:
                pipe.close()
        def consume_stderr(pipe):
            try:
                while not abort.is_set():
                    data = pipe.read(4096)
                    if not data: break
                    diagnostic.extend(data[:max(0,4096-len(diagnostic))])
            finally: pipe.close()
        try:
            if self._executable is None or SourceSignature.capture(request.source.canonical_path)!=request.source:
                raise ValueError('Source/runtime unavailable')
            command=[str(self._executable),'-v','error','-select_streams','v:0',
                '-show_frames','-show_streams','-show_entries',
                'frame=best_effort_timestamp,duration:frame_side_data=:stream=time_base',
                '-of','compact=p=1:nk=0',request.source.canonical_path]
            if self._decoder_threads is not None:
                command[1:1]=['-threads',str(self._decoder_threads)]
            process=control.spawn(self._factory,command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False)
            readers = [Thread(target=consume_stdout,args=(process.stdout,),daemon=True),
                       Thread(target=consume_stderr,args=(process.stderr,),daemon=True)]
            for reader in readers:
                reader.start()
            deadline=time.monotonic()+INDEX_TIMEOUT_SECONDS
            timestamps=array('q')
            time_base=None
            last_duration=None
            while True:
                if control.cancelled.is_set() or time.monotonic()>=deadline:
                    raise ValueError('Frame indexing cancelled or timed out')
                try: record=lines.get(timeout=.05)
                except Empty: continue
                if record is None: break
                # Class E.1: remove physical line endings only, never whitespace.
                payload = record.removesuffix(b'\n').removesuffix(b'\r')
                if payload == b'':
                    continue
                # Class E: nested stream-group facts have no timing authority.
                if record.split(b'|', 1)[0].strip() == b'stream_group':
                    continue
                tokens=record.decode('ascii',errors='strict').strip().split('|')
                fields=dict(token.split('=',1) for token in tokens[1:] if '=' in token)
                if tokens[0]=='frame':
                    tick=int(fields['best_effort_timestamp'])
                    if timestamps and tick<=timestamps[-1]:
                        raise ValueError('Duplicate/unordered frame timing')
                    if (len(timestamps)+1)*8>self._maximum_bytes:
                        raise ValueError('Frame timeline exceeds 64 MiB')
                    timestamps.append(tick)
                    last_duration=int(fields.get('duration','0'))
                elif tokens[0]=='stream':
                    if time_base is not None: raise ValueError('Ambiguous video stream')
                    time_base=Fraction(fields['time_base'])
                else: raise ValueError('Unexpected frame timing record')
            process.wait(timeout=2)
            if process.returncode!=0 or not time_base or time_base<=0 or control.cancelled.is_set():
                raise ValueError('Frame scan failed: '+diagnostic.decode('utf-8',errors='replace'))
            numerator, denominator = time_base.numerator * 1_000_000, time_base.denominator
            for index,tick in enumerate(timestamps):
                if index % 1024 == 0 and (control.cancelled.is_set() or time.monotonic() >= deadline):
                    raise ValueError('Frame indexing cancelled or timed out')
                quotient, remainder = divmod(tick * numerator, denominator)
                if 2 * remainder > denominator or (2 * remainder == denominator and quotient % 2):
                    quotient += 1
                timestamps[index] = quotient
            timeline=SourcePresentationTimeline.from_timestamps(timestamps,round(last_duration*time_base*1_000_000))
            if SourceSignature.capture(request.source.canonical_path)!=request.source:
                raise ValueError('Source changed during indexing')
            return TimelineResult(request,timeline)
        except Exception as error:
            return TimelineResult(request,None,str(error))
        finally:
            abort.set()
            if process is not None:
                if process.poll() is None: control.stop(process)
                for reader in readers:
                    reader.join(timeout=2)
                if any(reader.is_alive() for reader in readers):
                    raise RuntimeError('Frame timing reader failed to stop')
                control.release(process)
