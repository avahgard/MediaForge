"""Lossless actual scene evidence. Unmapped readbacks survive assertions."""
import hashlib
import json
import os
from pathlib import Path
import time
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy


STORAGE_ENV = 'MEDIAFORGE_TEST_CAPTURE_STORAGE'


def storage_from_environment():
    mode = os.environ.get(STORAGE_ENV, 'raw+png')
    if mode not in ('raw+png', 'png-only'):
        raise ValueError(f'{STORAGE_ENV} must be exactly raw+png or png-only; got {mode!r}')
    return mode


def canonical_rgba(image):
    """Owned, tightly packed RGBA bytes; padding is not a picture channel."""
    if image.isNull():
        raise ValueError('Null capture/decoded image')
    image = image.convertToFormat(QImage.Format_RGBA8888)
    if image.bytesPerLine() != image.width() * 4:
        image = image.copy()  # Normalize externally padded scanlines.
    raw = bytes(image.constBits())
    if (image.isNull() or image.format() != QImage.Format_RGBA8888
            or image.bytesPerLine() != image.width() * 4
            or len(raw) != image.sizeInBytes()
            or len(raw) != image.bytesPerLine() * image.height()):
        raise ValueError('Invalid canonical RGBA8888 layout')
    return image, raw


def verify_png(path, image, raw):
    encoded = path.read_bytes()
    decoded = QImage.fromData(encoded, 'PNG')
    if decoded.isNull():
        raise ValueError(f'Cannot decode PNG: {path}')
    decoded, reconstructed = canonical_rgba(decoded)
    if (decoded.size() != image.size() or decoded.format() != image.format()
            or decoded.bytesPerLine() != image.bytesPerLine()
            or len(reconstructed) != len(raw) or reconstructed != raw
            or hashlib.sha256(reconstructed).digest() != hashlib.sha256(raw).digest()):
        raise ValueError(f'PNG canonical pixel/layout mismatch: {path}')
    return encoded


class CaptureStorageError(RuntimeError):
    """No digest/reference is accepted; persistence is best effort on failure."""


class ScenePaintOracle:
    def __init__(self, window, video, playback, directory):
        self.storage_mode = storage_from_environment()  # Before allocation/readback.
        self.window,self.video,self.playback=window,video,playback
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.revision=0
        self.spies={name:QSignalSpy(getattr(window,name)) for name in
                    ('beforeSynchronizing','beforeRendering','afterRendering','frameSwapped')}
        self.references={}
        self.storage_error = None

    def revisions(self):
        return {name:spy.count() for name,spy in self.spies.items()}

    def capture(self, kind, timestamp=None):
        if self.storage_error is not None:
            raise CaptureStorageError(self.storage_error)
        self.revision+=1
        p=self.playback;video=self.video;window=self.window
        before=self.revisions()
        scene=window.grabWindow()
        content=video.property('contentRect');dpr=window.devicePixelRatio()
        regions={'full':scene}
        geometry={}
        for name,fraction,extent in [('video',0,1),('proof',.25,.5)]:
            point=video.mapToScene(QPointF(content.x()+content.width()*fraction,
                                         content.y()+content.height()*fraction))
            end=video.mapToScene(QPointF(content.x()+content.width()*(fraction+extent),
                                       content.y()+content.height()*(fraction+extent)))
            rect=[round(point.x()*dpr),round(point.y()*dpr),
                  round((end.x()-point.x())*dpr),round((end.y()-point.y())*dpr)]
            geometry[name]=rect;regions[name]=scene.copy(*rect)
        facts={'kind':kind,'readback_revision':self.revision,'reference_timestamp_us':timestamp,
               'source_generation':p._source_generation,'player_generation':p._player_generation,
               'player_identity':id(p._player),'staging_identity':id(p._staging_sink),
               'viewer_identity':id(p._presentation_sink),'presentation_us':p._presented_start,
               'frame_label':p.frameText,'range_ms':[p._trim_in,p._trim_out],
               'armed':p.trimLoopActive,'loop_seeking':p._loop_seeking,'dpr':dpr,
               'before':before,'after':self.revisions(),'geometry':geometry,'images':{}}
        started = time.perf_counter_ns()
        facts['storage'] = {'version':1, 'mode':self.storage_mode, 'status':'incomplete',
                            'errors':[]}
        captured = {}
        unconverted = {}
        # Snapshot ALL originals before saving. One region's storage failure must
        # not prevent attempting the remaining full/video/proof evidence.
        for name, original in regions.items():
            try:
                image, raw = canonical_rgba(original)
                captured[name] = (image, raw)
                stem = f'{kind}-{self.revision:04d}-{name}'
                facts['images'][name] = {'png':stem+'.png',
                    'size':[image.width(),image.height()], 'format':'RGBA8888',
                    'bytes_per_line':image.bytesPerLine(), 'byte_count':len(raw),
                    'raw_present':False, 'roundtrip_verified':False}
                facts['images'][name]['raw_sha256'] = hashlib.sha256(raw).hexdigest()
            except Exception as error:
                facts['storage']['errors'].append(f'{name} snapshot: {error!r}')
                if name not in captured:
                    # A conversion failure can still leave obtainable original
                    # framebuffer bytes. Their original layout is diagnostic only.
                    try:
                        unconverted[name] = bytes(original.constBits())
                        facts['images'][name] = {
                            'png':f'{kind}-{self.revision:04d}-{name}.png',
                            'size':[original.width(),original.height()],
                            'format':original.format().name,
                            'bytes_per_line':original.bytesPerLine(),
                            'byte_count':len(unconverted[name]), 'raw_present':False,
                            'roundtrip_verified':False}
                    except Exception as raw_error:
                        facts['storage']['errors'].append(f'{name} original bytes: {raw_error!r}')
        for name, (image, raw) in captured.items():
            info = facts['images'][name]
            path = self.directory / info['png']
            try:
                save_started = time.perf_counter_ns()
                if not image.save(str(path), 'PNG'):
                    raise OSError(f'PNG save failed: {path}')
                if self.storage_mode == 'raw+png':
                    if path.with_suffix('.rgba').write_bytes(raw) != len(raw):
                        raise OSError('Short raw write')
                    info['raw_present'] = True
                info['serialization_ns'] = time.perf_counter_ns() - save_started
                read_started = time.perf_counter_ns()
                if self.storage_mode == 'png-only':
                    encoded = verify_png(path, image, raw)
                    info['roundtrip_verified'] = True
                else:
                    encoded = path.read_bytes()
                info['png_sha256'] = hashlib.sha256(encoded).hexdigest()
                info['png_byte_count'] = len(encoded)
                info['roundtrip_ns'] = time.perf_counter_ns() - read_started
            except Exception as error:
                facts['storage']['errors'].append(f'{name} storage: {error!r}')
        facts['storage']['serialization_roundtrip_ns'] = time.perf_counter_ns() - started
        if not facts['storage']['errors']:
            facts['storage']['status'] = 'complete'
            try:
                # Close before returning/classifying, including every unmapped
                # scene. No power-loss/fsync guarantee is added here.
                serialized = json.dumps(facts, allow_nan=False) + '\n'
                with (self.directory/'readbacks.jsonl').open('a',encoding='utf8') as log:
                    if log.write(serialized) != len(serialized):
                        raise OSError('Short journal write')
            except Exception as error:
                facts['storage']['errors'].append(f'journal: {error!r}')
        if facts['storage']['errors']:
            self._retain_incomplete(facts, captured, unconverted, regions)
        digest=facts['images']['proof']['raw_sha256']
        if timestamp is not None:self.references[digest]=timestamp
        return digest

    def _retain_incomplete(self, facts, captured, unconverted, regions):
        facts['storage']['status'] = 'incomplete'
        errors = facts['storage']['errors']
        for name, raw in unconverted.items():
            info = facts['images'][name]
            try:
                path = (self.directory / info['png']).with_suffix('.rgba')
                if path.write_bytes(raw) != len(raw):
                    raise OSError('Short original diagnostic write')
                info['raw_present'] = True
            except Exception as error:
                errors.append(f'{name} original raw recovery: {error!r}')
            try:
                recovery = (self.directory / info['png']).with_suffix('.recovery.png')
                if not regions[name].save(str(recovery), 'PNG'):
                    raise OSError('Original PNG recovery save failed')
                info['recovery_png'] = recovery.name
            except Exception as error:
                errors.append(f'{name} original PNG recovery: {error!r}')
        for name, (image, raw) in captured.items():
            info = facts['images'][name]
            path = self.directory / info['png']
            try:
                info['raw_present'] = False
                if path.with_suffix('.rgba').write_bytes(raw) != len(raw):
                    raise OSError('Short recovery raw write')
                info['raw_present'] = True
            except Exception as error:
                errors.append(f'{name} raw recovery: {error!r}')
            # Keep the suspect original PNG too. A separate recovery PNG retains
            # obtainable pictures even after corruption or a failed first save.
            try:
                recovery = path.with_suffix('.recovery.png')
                if not image.save(str(recovery), 'PNG'):
                    raise OSError(f'PNG recovery save failed: {recovery}')
                info['recovery_png'] = recovery.name
                encoded = recovery.read_bytes()
                info['recovery_png_sha256'] = hashlib.sha256(encoded).hexdigest()
                info['recovery_png_byte_count'] = len(encoded)
            except Exception as error:
                errors.append(f'{name} PNG recovery: {error!r}')
        try:
            serialized = json.dumps(facts, allow_nan=False) + '\n'
            with (self.directory/'readbacks.jsonl').open('a',encoding='utf8') as log:
                if log.write(serialized) != len(serialized):
                    raise OSError('Short incomplete journal write')
        except Exception as error:
            errors.append(f'incomplete journal: {error!r}')
        # Independent sidecar serialization can survive a broken journal/dumps.
        try:
            with (self.directory/f'capture-{self.revision:04d}-incomplete.json').open(
                    'w',encoding='utf8') as log:
                json.dump(facts, log, default=repr, indent=2)
        except Exception as error:
            errors.append(f'incomplete sidecar: {error!r}')
        self.storage_error = f'Capture evidence incomplete in {self.directory}: ' + '; '.join(errors)
        raise CaptureStorageError(self.storage_error)
