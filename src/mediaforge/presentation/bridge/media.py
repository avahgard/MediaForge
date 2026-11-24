"""QObject bridge for the Open Media metadata lifecycle."""

import logging
import os
import time
from threading import Event
from mediaforge.application.contracts.sequence import friendly_rate

from PySide6.QtCore import QObject, Property, QThread, QUrl, Signal, Slot

from mediaforge.application.contracts.media import (
    OpenMediaErrorCode,
    OpenMediaResult,
    OpenMediaUseCase,
)
from mediaforge.presentation.models.media import MediaMetadataView
from mediaforge.application.contracts.trim import TrimUseCase
from mediaforge.presentation.bridge.trim import TrimViewModel
from mediaforge.application.contracts.crop import CropUseCase
from mediaforge.presentation.bridge.crop import CropViewModel
from mediaforge.application.contracts.resize import ResizeUseCase
from mediaforge.presentation.bridge.resize import ResizeViewModel
from mediaforge.presentation.bridge.export import ExportViewModel
from mediaforge.application.contracts.export import ExportUseCase, output_aspect
from mediaforge.presentation.bridge.playback import PlaybackController
from mediaforge.presentation.bridge.viewer_window import ViewerWindowController
from mediaforge.presentation.bridge.crop_geometry import Rect, drag_rect
from mediaforge.application.state.workspace import MediaWorkspaceSession, TrimPictureBoundary, canonical_path
from mediaforge.application.contracts.source_location import SourceLocationRevealer
from mediaforge.application.contracts.image_source import StillSource, VideoSource, SequenceSource, IMAGE_SUFFIXES
from mediaforge.application.contracts.sequence import sequence_rate
from mediaforge.application.contracts.media import MediaMetadataDto
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from mediaforge.presentation.models.media_info import info_rows
from mediaforge.presentation.bridge.image_preview import ImagePreviewController


class _ProbeWorker(QObject):
    finished = Signal(object)

    def __init__(self, use_case: OpenMediaUseCase, source_path: str, exported=None) -> None:
        super().__init__()
        self._use_case = use_case
        self._source_path = source_path
        self._exported = exported
        self.cancelled=Event()

    @Slot()
    def run(self) -> None:
        try:
            result = (self._use_case.open_exported_result(self._exported,self.cancelled) if self._exported is not None
                      else self._use_case.open_media(self._source_path))
        except Exception:
            logging.getLogger("mediaforge").exception(
                "Unexpected metadata worker failure"
            )
            result = OpenMediaResult(
                error_code=OpenMediaErrorCode.PROBE_FAILED,
                error_message="Media metadata could not be read. Try another file.",
            )
        self.finished.emit(result)


class MediaViewModel(QObject):
    sequencePublished = Signal(int,object)
    @Slot(str, int, int, int, int, str, result="QVariantMap")
    def scrubNumber(self, text, cursor, start, end, direction, place):
        from mediaforge.presentation.bridge.numeric import scrub
        return scrub(text, cursor, start, end, direction, place)

    stateChanged = Signal()
    contentChanged = Signal()
    canOpenChanged = Signal()
    sourceSelectionCancelled = Signal()
    workspaceChanged = Signal()
    previewChanged = Signal()
    infoChanged = Signal()

    def __init__(
        self,
        use_case: OpenMediaUseCase,
        logger: logging.Logger | None = None,
        trim_use_case: TrimUseCase | None = None,
        crop_use_case: CropUseCase | None = None,
        resize_use_case: ResizeUseCase | None = None,
        playback_controller: PlaybackController | None = None,
        source_revealer: SourceLocationRevealer | None = None,
        export_use_case: ExportUseCase | None = None,
        image_service=None,
        processing_gate=None,
    ) -> None:
        super().__init__()
        self._use_case = use_case
        self._image_service = image_service
        self._image_region_dirty = False
        self._pending_sequence = None
        self._pending_sequence_rate = None
        self._pending_sequence_result = None
        self._sequence_shared = False
        self._sequence_leases = {}
        self._sequence_error = ''
        self._image_preview = (ImagePreviewController(image_service,self._image_prepared,self)
                               if image_service is not None else None)
        if self._image_preview is not None:
            self._image_preview.changed.connect(self._image_status_changed)
            self._image_preview.discovered.connect(self._sequence_discovered)
            self._image_preview.exportedDiscovered.connect(self._export_sequence_discovered)
            self._image_preview.failed.connect(self._sequence_failed)
            self._image_preview.viewChanged.connect(self._refresh_image_region)
        self._logger = logger or logging.getLogger("mediaforge")
        self._state = "empty"
        self._error_message = ""
        self._metadata: MediaMetadataView | None = None
        self._workspace = MediaWorkspaceSession()
        self._video_session_owner = None
        self._retired_video_item = None
        self._retirement_restore_blocked = False
        self._selected_operation = "trim"
        self.workspaceChanged.connect(self.previewChanged)
        from mediaforge.presentation.bridge.viewer_viewport import ViewerViewport
        self._viewer_viewport = ViewerViewport(self)
        self.workspaceChanged.connect(self._sync_trim_loop)
        self._thread: QThread | None = None
        self._worker: _ProbeWorker | None = None
        self._shutdown = False
        self._external_page = False
        self._processing_gate = processing_gate
        self._shutdown_error: Exception | None = None
        self._playback = playback_controller
        if self._image_preview is not None:
            self._image_preview.retire_video = self._retire_video_for_image
            self._image_preview.retirement_pending = lambda:self.imageTransition and self._playback._source is None
        self._source_revealer = source_revealer
        self._reveal_message = ''
        self._last_info_frames = None
        self.contentChanged.connect(self.infoChanged)
        self.workspaceChanged.connect(self.infoChanged)
        if self._playback is not None and hasattr(self._playback, 'changed'):
            self._playback.changed.connect(self._refresh_info_frames)
        self._window_presentation = ViewerWindowController(playback_controller, self)
        if self._image_preview is not None:
            self._window_presentation.image_admission = self._image_preview.admit_fullscreen
        self._trim = TrimViewModel(trim_use_case, self)
        self._trim.changed.connect(self.canOpenChanged)
        self._crop = CropViewModel(crop_use_case, self)
        self._crop.changed.connect(self.canOpenChanged)
        self._resize = ResizeViewModel(resize_use_case, self)
        self._resize.changed.connect(self.canOpenChanged)
        self._output = ExportViewModel(export_use_case, self._workspace, source_revealer, self)
        self._output.changed.connect(self.canOpenChanged)
        self.stateChanged.connect(self._output.changed)
        self._trim.changed.connect(self._output.changed)
        self._crop.changed.connect(self._output.changed)
        self._resize.changed.connect(self._output.changed)
        self.canOpenChanged.connect(self._resume_image_region)

    @Slot()
    def _resume_image_region(self):
        if self._image_region_dirty and self.canOpen:
            self._image_region_dirty=False
            self._refresh_image_region()

    @Property(QObject, constant=True)
    def imagePreview(self):
        return self._image_preview

    @Slot()
    def _image_status_changed(self):
        if self._shutdown: return
        self._release_unused_manifests()
        preview = self._image_preview
        if self._metadata is None:
            if preview.busy: self._set_state('loading')
            elif preview.status:
                self._error_message = preview.status
                self._set_state('error')
        if (self.imageTransition and not getattr(self,'_retirement_restore_blocked',False) and not preview.busy and preview.statusKind!='progress'
                and self._pending_sequence is None and self._thread is None):
            self._restore_retired_video()
        self.contentChanged.emit()
        self._output.changed.emit()

    @Slot(QObject)
    def attachImageWindow(self,window):
        if self._image_preview is not None:
            self._image_preview.attach(window)
            if self._playback is not None:
                self._playback.attachImageWindow(window)

    @Slot()
    def _refresh_image_region(self):
        if self._playback is None or not self._playback.imageMode:return
        prepared=self._playback._image_prepared
        if prepared is None or not getattr(prepared.raster.plan,'full_source',False):return
        if not self.canOpen:
            self._image_region_dirty=True;return
        self._image_region_dirty=False
        item=self._workspace.active
        if item is None:return
        # Layout callbacks describe the displayed source, which can still be
        # old while a new import/Tray selection is preparing. Never let that
        # subordinate region intent cancel a newer source intent.
        intent=self._image_preview._pending or self._image_preview._active
        if intent is not None and (intent.discover or intent.exported is not None or intent.item_id!=item.id):return
        if isinstance(item.metadata.source,SequenceSource):
            self._request_sequence(item.metadata.source,self._playback.position,item.id)
        else:
            self._image_preview.request(item.metadata.source_path,item.metadata.source,item.id)

    @Slot(str,result=bool)
    def setImageChannel(self,channel):
        if channel not in ('RGBA','RGB','Alpha') or not self._playback.imageMode or not self.canOpen:return False
        item = self._workspace.active
        if item is None or not isinstance(item.metadata.source,(StillSource,SequenceSource)):
            return False
        self._image_service.channel=channel
        if isinstance(item.metadata.source,SequenceSource):
            self._pause_sequence()
            return self._request_sequence(item.metadata.source,item.sequence.current_slot,item.id)
        return self._image_preview.request(item.metadata.source_path,item.metadata.source,item.id)

    def _image_prepared(self,request,prepared):
        publication_started = time.perf_counter()
        if self._shutdown:
            raise ValueError('Image publisher is closed.')
        if not (self.canImport if getattr(request,'shared_source',False) else self.canOpen):
            if getattr(prepared.raster.plan,'full_source',False):self._image_region_dirty=True
            raise ValueError('Media processing is busy. The current source and picture are preserved.')
        if self._playback is None:
            raise ValueError('The image Viewer is unavailable.')
        if request.exported is not None:
            prepared.source_guard.check()
        if request.exported is not None and not request.discover:
            previous=self._workspace.find_path(prepared.metadata.source_path)
            if previous is not None and previous.metadata.source!=prepared.metadata.source:
                raise ValueError('Exported result changed since its Tray import. Remove that old item before reopening.')
        item = next((i for i in self._workspace.items if i.id == request.item_id),None) if request.item_id else None
        if request.item_id and item is None:
            raise ValueError('The requested Tray item no longer exists.')
        frame = self._playback.prepare_image(prepared) if self._playback else None
        if request.context is not None:
            source, slot, rate = request.context
            if prepared.metadata.source != source.manifest.members[slot]:
                raise ValueError('Sequence picture identity mismatch.')
            if item is not None and item.metadata.source != source:
                raise ValueError('Sequence manifest no longer matches the Tray item.')
            same = item is not None and self._playback.sequence.item is item and item.id == self._workspace.active_id
            frame.setStartTime(int(source.manifest.time(slot,item.sequence.rate if item else rate)*1_000_000))
            if same:
                self._playback.update_image_preview(prepared,frame,slot)
                self._playback.sequence.admitted(slot)
            else:
                if item is None:
                    existing = next((i for i in self._workspace.items if i.canonical_path == canonical_path(source.path)
                                     and isinstance(i.metadata.source,SequenceSource)),None)
                    if existing is not None and existing.metadata.source != source:
                        raise ValueError('Sequence changed; remove its old Tray item before reimporting.')
                    facts = source.manifest.members[0].facts
                    metadata = replace(prepared.metadata,source=source,source_path=source.path,
                        source_name=source.manifest.pattern,format_name=facts.format+' sequence')
                    item,_ = self._workspace.add(metadata)
                    # Reimport must not silently reuse a changed manifest.
                    if item.metadata.source != source: raise ValueError('Sequence changed; remove its old Tray item before reimporting.')
                    item.sequence.rate, item.sequence.confirmed = rate, True
                else: self._workspace.select(item.id)
                self._activate_image(item,prepared,frame,slot)
                self._playback.sequence.bind(item,lambda target:self._request_sequence(source,target,item.id,shared=True),slot,
                                             self._image_preview.cancel)
                self._sync_trim_loop()
            if not prepared.raster.plan.full_source:prepared.close_source()
            self._image_preview.live_prepared=__import__("weakref").ref(prepared)
            self.workspaceChanged.emit()
            self.sequencePublished.emit(slot,dict(request_ms=(time.perf_counter()-request.requested)*1000,
                cache_hit=getattr(prepared.raster,'cache_hit',False),
                decode_ms=prepared.raster.decode_ms,normalize_ms=prepared.raster.normalize_ms,
                publication_ms=(time.perf_counter()-publication_started)*1000,source=source.manifest_id,
                source_number=source.manifest.numbers[slot]))
            if request.exported is None:
                self._image_preview.prefetch(source,(slot+1)%source.manifest.count,item.id)
            return
        if item is not None and item.id == self._workspace.active_id and self._playback and self._playback.imageMode:
            self._playback.update_image_preview(prepared,frame)
        else:
            if item is None:
                item,_ = self._workspace.add(prepared.metadata)
            else:
                self._workspace.select(item.id)
            self._activate_image(item,prepared,frame)
            self.workspaceChanged.emit()
        if not prepared.raster.plan.full_source:prepared.close_source()
        self._image_preview.live_prepared=__import__("weakref").ref(prepared)

    @Property('QVariantMap',notify=workspaceChanged)
    def sequenceConfirmation(self):
        if self._pending_sequence is None: return {}
        m = self._pending_sequence.manifest
        f = m.members[0].facts
        return dict(error=self._sequence_error,pattern=m.pattern,first=m.numbers[0],last=m.numbers[-1],count=m.count,
                    directory=str(Path(m.members[0].path).parent),
                    pixelClass=f'{f.variant or ("RGBA" if f.alpha else "RGB")} · {f.depth}-bit · {f.color_class}',
                    resolution=f'{f.width}×{f.height}',fps=friendly_rate(self._pending_sequence_rate or 24),
                    exactFps=str(self._pending_sequence_rate or 24),
                    authority='Confirmed export FPS — not embedded image metadata' if self._pending_sequence_rate else 'ASSUMED — no authoritative sequence FPS in ordinary image files',gap='Error · no gaps')

    @Property('QVariantMap',notify=workspaceChanged)
    def sequenceInfo(self):
        item = self._workspace.active
        if item is None or not isinstance(item.metadata.source,SequenceSource): return {}
        return dict(error=self._sequence_error,fps=friendly_rate(item.sequence.rate),exactFps=str(item.sequence.rate),confirmed=item.sequence.confirmed,
                    authority='User confirmed',count=item.metadata.source.manifest.count)

    @Slot(QUrl,result=bool)
    def importSequence(self,url):
        if not self.canOpen or self._image_preview is None or not url.isLocalFile(): return False
        self._pause_sequence()
        self._auto_sequence = False
        self.cancelSequenceConfirmation()
        self.workspaceChanged.emit()
        return self._image_preview.request(url.toLocalFile(),discover=True)

    def _sequence_discovered(self,manifest,rate=None,result=None):
        if getattr(self,'_auto_sequence',False) and manifest.manifest.count==1:
            self._auto_sequence = False
            source=manifest.manifest.members[0]
            shared,self._sequence_shared=self._sequence_shared,False
            self._image_preview.request(source.path,source,shared_source=shared)
            manifest.release()
            return
        self._auto_sequence = False
        self._sequence_error = ''
        if self._pending_sequence is not None: self._pending_sequence.release()
        self._pending_sequence = manifest
        self._pending_sequence_rate = rate
        self._pending_sequence_result = result
        self.workspaceChanged.emit()

    def _export_sequence_discovered(self,result,manifest):
        self._auto_sequence = False
        self._sequence_discovered(manifest,result.rate,result)

    @Slot(str,result=bool)
    def confirmSequence(self,fps):
        shared = self._sequence_shared
        if self._pending_sequence is None or not (self.canImport if shared else self.canOpen):
            return False
        try: rate = sequence_rate(fps)
        except ValueError as error:
            self._sequence_error = str(error)
            self.workspaceChanged.emit()
            return False
        self._sequence_error = ''
        owner = self._pending_sequence
        m = owner.manifest
        source = SequenceSource(m.members[0].path,m.identity,m)
        if not self._request_sequence(source,0,None,rate=rate,
                                      exported=self._pending_sequence_result,shared=shared):
            return False
        self._pending_sequence = None
        self._pending_sequence_result = None
        self._pending_sequence_rate = None
        self._sequence_shared = False
        old = self._sequence_leases.get(m.identity)
        if old is not None: owner.release()
        else: self._sequence_leases[m.identity] = owner
        self._release_unused_manifests()
        self.workspaceChanged.emit()
        return True

    @Slot()
    def cancelSequenceConfirmation(self):
        had_pending=self._pending_sequence is not None
        if self._pending_sequence is not None: self._pending_sequence.release()
        self._pending_sequence = None
        self._pending_sequence_rate = None
        self._pending_sequence_result = None
        self._sequence_shared = False
        if had_pending:
            self.sourceSelectionCancelled.emit()
            self._restore_retired_video()
        if self._metadata is None and self._image_preview and not self._image_preview.busy:
            self._set_state('empty')
        self.workspaceChanged.emit()

    @Slot(str,result=bool)
    def setSequenceFps(self,fps):
        item = self._workspace.active
        if not self.canOpen or item is None or not isinstance(item.metadata.source,SequenceSource): return False
        try: rate = sequence_rate(fps)
        except ValueError as error:
            self._sequence_error = str(error)
            self.workspaceChanged.emit()
            return False
        self._sequence_error = ''
        self._pause_sequence()
        self._image_preview.cancel()
        self._playback.sequence.awaiting = False
        item.sequence.rate, item.sequence.confirmed = rate, True
        self.workspaceChanged.emit()
        self._playback.changed.emit()
        return True

    def _pause_sequence(self):
        if self._playback is not None and self._playback.sequence.item is not None:
            self._playback.sequence.pause()
            self._playback.sequence.awaiting = False

    def _request_sequence(self,source,slot,item_id,rate=None,exported=None,*,shared=False):
        if not (self.canImport if shared else self.canOpen) or not 0 <= slot < source.manifest.count:
            return False
        member = source.manifest.members[slot]
        return self._image_preview.request(member.path,member,item_id,
            context=(source,slot,rate),exported=exported,validate_manifest=(self._playback.sequence.item is None
                or self._playback.sequence.item.id != item_id),shared_source=shared)

    def _sequence_failed(self,request,message):
        import_failed = request.discover or request.item_id != self._workspace.active_id
        if import_failed:
            self._error_message = message
        if request.context is not None and self._playback is not None:
            self._playback.sequence.failed()
        self._release_unused_manifests()
        if request.serial==self._image_preview.requestGeneration:
            self._restore_retired_video()
        if import_failed:
            self.contentChanged.emit()

    @Property(bool,notify=contentChanged)
    def imageTransition(self):return self._retired_video_item is not None

    def _retire_video_for_image(self,request):
        pc=self._playback;item=self._workspace.active
        admitted = self.canImport if getattr(request,'shared_source',False) else self.canOpen
        if (not admitted or pc is None or pc.imageMode
                or pc._source is None or item is None or item.metadata.audio_only
                or not isinstance(item.metadata.source,VideoSource)):
            return False
        self._remember_video()
        if request.serial!=self._image_preview.requestGeneration or self._workspace.active is not item:return False
        self._retired_video_item=item
        self._retirement_restore_blocked=False
        try:pc.retire_for_image()
        except Exception:
            if self._processing_gate is not None:
                self._processing_gate.quarantine(pc,'Video decoder retirement ownership is unresolved.')
            raise
        if request.serial!=self._image_preview.requestGeneration or self._workspace.active is not item:return False
        self.contentChanged.emit()
        return True

    def _restore_retired_video(self):
        item=self._retired_video_item
        if item is None:return True
        if self._shutdown or self._workspace.active is not item:
            self._retired_video_item=None
            return False
        if not self.canImport or self._retirement_restore_blocked:return False
        try:self._image_service.admit_video_resume()
        except ValueError as error:
            self._retirement_restore_blocked=True
            message = 'Video remains unloaded: ' + str(error)
            self._image_preview._set_status(message,'error')
            playback = getattr(self, '_playback', None)
            if playback is not None:
                playback._set_error(message)
            self.contentChanged.emit();self._output.changed.emit()
            return False
        self._retired_video_item=None;self._retirement_restore_blocked=False
        self._activate_item(item,source_changed=True)
        self.contentChanged.emit()
        return True

    def _release_unused_manifests(self):
        live = {i.metadata.source.manifest_id for i in self._workspace.items if isinstance(i.metadata.source,SequenceSource)}
        if self._playback and self._playback.sequence.item is not None:
            live.add(self._playback.sequence.item.metadata.source.manifest_id)
        if self._image_preview:
            for request in (self._image_preview._active,self._image_preview._pending):
                if request is not None and request.context is not None: live.add(request.context[0].manifest_id)
        for identity in set(self._sequence_leases)-live:
            self._sequence_leases.pop(identity).release()

    @Property(QObject, constant=True)
    def output(self):
        return self._output

    @Property(QObject, constant=True)
    def playback(self):
        return self._playback

    @Property(QObject, constant=True)
    def viewerViewport(self):
        return self._viewer_viewport

    @Property(QObject, constant=True)
    def windowPresentation(self):
        return self._window_presentation

    @Property(QObject, constant=True)
    def resize(self):
        return self._resize

    @Property(QObject, constant=True)
    def crop(self):
        return self._crop

    @Property(QObject, constant=True)
    def trim(self):
        return self._trim

    @Property("QVariantList", notify=workspaceChanged)
    def mediaItems(self) -> list[dict]:
        return [
            {"id": item.id, "name": item.metadata.source_name,
             "duration": MediaMetadataView.from_dto(item.metadata).duration,
             "resolution": 'Audio' if item.metadata.audio_only else f"{item.metadata.width}×{item.metadata.height}",
             "active": item.id == self._workspace.active_id}
            for item in self._workspace.items
        ]

    @Property(str, notify=workspaceChanged)
    def activeMediaId(self) -> str:
        return self._workspace.active_id or ""

    @Property(str, notify=workspaceChanged)
    def selectedOperation(self) -> str:
        return self._selected_operation

    @Property(str, notify=workspaceChanged)
    def sourceKind(self):
        item=self._workspace.active
        if item is not None and item.metadata.audio_only:return 'audio'
        if item is not None and isinstance(item.metadata.source,SequenceSource): return 'sequence'
        return 'still' if item is not None and isinstance(item.metadata.source,StillSource) else 'video'

    @Property("QVariantMap", notify=workspaceChanged)
    def editState(self) -> dict:
        item = self._workspace.active
        if item is None:
            return {}
        from mediaforge.application.contracts.reformat import GeometryError, scale_raster, sample_aspect
        from mediaforge.application.contracts.export import ratio_text
        from fractions import Fraction
        geometry = None
        quantized = False
        reason = ""
        standalone_reason = ""
        try:
            geometry = self._workspace.reformat_plan()
            geometry.encoding_geometry()
            if item.resize.mode == "scale":
                from mediaforge.application.contracts.image_geometry import scale_raster as image_scale
                raster=scale_raster if isinstance(item.metadata.source,VideoSource) else image_scale
                quantized = raster(*self._workspace.effective_input(), item.resize.scale).quantized
        except GeometryError as error:
            if geometry is None:
                reason = str(error)
            else:
                standalone_reason = str(error)
        return {
            "trimEnabled": item.trim.enabled, "trimIn": item.sequence.in_slot if isinstance(item.metadata.source,SequenceSource) else item.trim.in_ms,
            "trimOut": item.sequence.out_slot if isinstance(item.metadata.source,SequenceSource) else item.trim.out_ms, "cropEnabled": item.crop.enabled,
            "cropX": item.crop.x, "cropY": item.crop.y,
            "cropWidth": item.crop.width, "cropHeight": item.crop.height,
            "cropPreview": item.crop.preview, "resizeEnabled": item.resize.enabled,
            "resizeWidth": item.resize.width, "resizeHeight": item.resize.height,
            "reformatMode": item.resize.mode, "reformatScale": item.resize.scale,
            "reformatPlacement": item.resize.placement, "reformatPreset": item.resize.preset,
            "reformatLocked": item.resize.aspect_locked,
            "pixelAspect": item.resize.pixel_aspect,
            "pixelAspectText": ("1.0" if item.resize.pixel_aspect == "square" else
                str(output_aspect(item.resize.pixel_aspect, geometry.output.width, geometry.output.height,
                                  item.metadata.sample_aspect_ratio)) if geometry else item.resize.pixel_aspect),
            "reformatSar": ratio_text(output_aspect(item.resize.pixel_aspect,geometry.output.width,geometry.output.height,
                                    item.metadata.sample_aspect_ratio)) if geometry else '',
            "reformatDar": ratio_text(Fraction(geometry.output.width,geometry.output.height)*output_aspect(
                item.resize.pixel_aspect,geometry.output.width,geometry.output.height,item.metadata.sample_aspect_ratio)) if geometry else '',
            "reformatError": item.resize.error or reason,
            "reformatValid": geometry is not None,
            "reformatScaleQuantized": quantized,
            "reformatOutputWidth": geometry.output.width if geometry else 0,
            "reformatOutputHeight": geometry.output.height if geometry else 0,
            "standaloneReason": ("Use Output to export enabled Trim/Crop and Pixel Aspect together."
                                 if item.trim.enabled or item.crop.enabled or (geometry and output_aspect(item.resize.pixel_aspect,
                                     geometry.output.width, geometry.output.height, item.metadata.sample_aspect_ratio) != 1)
                                 else item.resize.error or reason or standalone_reason),
            "sourceWidth": item.metadata.width, "sourceHeight": item.metadata.height,
            "sourceSar": float(sample_aspect(item.metadata.sample_aspect_ratio)),
            "duration": item.metadata.source.manifest.count if isinstance(item.metadata.source,SequenceSource) else item.duration_ms,
            "activeCount": sum((item.trim.enabled, item.crop.enabled, item.resize.enabled)),
        }

    @Property('QVariantMap', notify=infoChanged)
    def trimReadout(self):
        item = self._workspace.active
        if item is None:
            return {}
        source = item.metadata.source
        if isinstance(source, SequenceSource):
            start, end = item.sequence.in_slot, item.sequence.out_slot
            return dict(first=start + 1, last=end, count=end-start,
                        startMs=round(start*1000/item.sequence.rate), endMs=round(end*1000/item.sequence.rate),
                        durationMs=round((end-start)*1000/item.sequence.rate))
        first_us, last_us = item.trim.player_bounds_us
        result = dict(durationMs=max(0, round((last_us-first_us)/1000)))
        if not isinstance(source, VideoSource):
            return result
        if not getattr(self._playback, 'timelineAvailable', False):
            result['pending'] = getattr(self._playback, 'frameStepStatus', 'Exact frame index unavailable')
            return result
        from mediaforge.application.services.image_export import video_trim_span
        snapshot = self._playback.export_picture_snapshot()
        try:
            start, end = video_trim_span(item, snapshot)
        except RuntimeError as error:
            result['pending'] = str(error)
            return result
        if start < end:
            result.update(first=start+1, last=end, count=end-start)
        else:
            result['count'] = 0
        return result

    @Property(bool, notify=previewChanged)
    def cropEditAllowed(self):
        item = self._workspace.active
        return bool(item and not self._external_page and self._selected_operation == 'crop' and not item.crop.preview
                    and (item.crop.enabled or not item.resize.enabled))

    @Property(bool, notify=previewChanged)
    def previewSource(self):
        return self.cropEditAllowed  # upstream Crop Edit only; Owner removed comparison/W

    @Property("QVariantMap", notify=previewChanged)
    def previewPlan(self):
        from mediaforge.application.contracts.reformat import GeometryError
        item = self._workspace.active
        if item is None:
            return {}
        source = self.previewSource
        editing = self.cropEditAllowed
        cropped = item.crop.enabled and not source and not self._external_page
        x, y = (item.crop.x, item.crop.y) if cropped else (0, 0)
        w, h = (item.crop.width, item.crop.height) if cropped else (item.metadata.width, item.metadata.height)
        result = {"cropX": x, "cropY": y, "inputWidth": w, "inputHeight": h,
                  "width": w, "height": h, "pictureWidth": w, "pictureHeight": h,
                  "x": 0., "y": 0., "reformatted": False, "editing": editing, "valid": True}
        if item.resize.enabled and not source and not self._external_page:
            try:
                p = self._workspace.reformat_plan()
                result.update(width=p.output.width, height=p.output.height,
                              pictureWidth=float(p.picture_width), pictureHeight=float(p.picture_height),
                              x=float(p.x), y=float(p.y), reformatted=True,
                              displaySar=float(output_aspect(item.resize.pixel_aspect, p.output.width, p.output.height,
                                                             item.metadata.sample_aspect_ratio)))
            except GeometryError as error:
                result.update(valid=False, error=str(error))
        return result

    @Slot(str, str, result=bool)
    def setReformat(self, field, value):
        valid = self._workspace.set_reformat(field, value)
        self.workspaceChanged.emit()
        return valid

    @Slot(result=bool)
    def startStandaloneReformat(self):
        item = self._workspace.active
        if item is None or not item.resize.enabled or self.editState["standaloneReason"]:
            return False
        p = self._workspace.reformat_plan()
        return self._resize.start_reformat(p.output.width, p.output.height, p.placement)

    def _sync_trim_loop(self):
        item = self._workspace.active
        if item is not None and isinstance(item.metadata.source,SequenceSource):
            seq = self._playback.sequence
            if seq.loop and (not item.trim.enabled or not item.sequence.in_slot <= seq.slot < item.sequence.out_slot): seq.loop = False
            self._playback.changed.emit()
            return
        if self._playback is not None and item is not None:
            exact = tuple(picture.player_us if picture is not None else None
                          for picture in (item.trim.in_picture, item.trim.out_picture))
            args = (item.trim.enabled and not self._external_page, item.trim.in_ms, item.trim.out_ms)
            if any(bound is not None for bound in exact):
                self._playback.configure_trim_loop(*args, picture_bounds_us=exact)
            else:
                self._playback.configure_trim_loop(*args)

    @Slot(str)
    def selectOperation(self, operation: str) -> None:
        if operation in ("trim", "crop", "resize", "output") and operation != self._selected_operation:
            self._selected_operation = operation
            self.workspaceChanged.emit()

    @Slot(str, bool)
    def setOperationEnabled(self, operation: str, enabled: bool) -> None:
        if self._workspace.set_enabled(operation, enabled):
            self.workspaceChanged.emit()

    @Slot(int, int, result=bool)
    def setTrimRange(self, in_ms: int, out_ms: int) -> bool:
        valid = (self._workspace.set_sequence_trim(in_ms,out_ms) if self.sourceKind == 'sequence'
                 else self._workspace.set_trim(in_ms, out_ms))
        if valid:
            if self.sourceKind == 'sequence':
                self._pause_sequence()
                self._image_preview.cancel()
            self.workspaceChanged.emit()
        return valid

    @Slot(bool, result=bool)
    def setTrimInAtPlayhead(self, reset: bool = False) -> bool:
        return self._set_trim_from_playhead(False, reset)

    @Slot(bool, result=bool)
    def setTrimOutAtPlayhead(self, reset: bool = False) -> bool:
        return self._set_trim_from_playhead(True, reset)

    @Slot(bool, int, result=bool)
    def setTrimBoundary(self, is_out, value):
        item = self._workspace.active
        if item is None:
            return False
        if self.sourceKind == 'sequence':
            return (self.setTrimRange(item.sequence.in_slot, value) if is_out
                    else self.setTrimRange(value, item.sequence.out_slot))
        return self._apply_trim_boundary(is_out, value)

    def _apply_trim_boundary(self, is_out, value, picture=None):
        valid = self._workspace.set_trim_boundary(is_out, value, picture)
        if valid:
            self.workspaceChanged.emit()
        return valid

    def _set_trim_from_playhead(self, is_out, reset):
        item, playback = self._workspace.active, self._playback
        if item is None or (playback is None and not reset):
            return False
        if reset:
            end = item.metadata.source.manifest.count if self.sourceKind == 'sequence' else item.duration_ms
            return self.setTrimBoundary(is_out, end if is_out else 0)
        if self.sourceKind == 'sequence':
            return self.setTrimBoundary(is_out, playback.position + int(is_out))
        if not isinstance(item.metadata.source, VideoSource) or not getattr(playback, 'framesReliable', False):
            return self.setTrimBoundary(is_out, playback.position)
        snapshot = playback.export_picture_snapshot()
        index = snapshot.ordinal + int(is_out)
        timestamps = snapshot.timeline.timestamps
        pts = timestamps[index] if index < len(timestamps) else timestamps[-1] + snapshot.timeline.last_duration_us
        player_us = pts + snapshot.offset_us
        display_ms = (player_us + (999 if is_out else 0)) // 1000
        picture = TrimPictureBoundary(snapshot.signature, pts, player_us)
        return self._apply_trim_boundary(is_out, max(0, min(item.duration_ms, display_ms)), picture)

    @Slot(int, int, int, int, result=bool)
    def setCropRect(self, x: int, y: int, width: int, height: int) -> bool:
        valid = self._workspace.set_crop(x, y, width, height)
        if valid:
            self.workspaceChanged.emit()
        return valid

    cropReset = Signal()

    @Slot(result=bool)
    def resetCrop(self) -> bool:
        if not self._workspace.reset_crop():
            return False
        self.workspaceChanged.emit()
        self.cropReset.emit()
        return True

    @Slot(str, int, int, int, int, float, float, result=bool)
    def dragCrop(self, mode: str, x: int, y: int, width: int, height: int,
                 delta_x: float, delta_y: float) -> bool:
        item = self._workspace.active
        if not self.cropEditAllowed or item is None or mode not in ("move", "left", "right", "top", "bottom",
                                         "left-top", "right-top", "left-bottom", "right-bottom"):
            return False
        if isinstance(item.metadata.source,VideoSource):
            rect = drag_rect(Rect(x, y, width, height), mode, delta_x, delta_y,item.metadata.width,item.metadata.height)
        else:
            from mediaforge.application.contracts.image_geometry import ImageRect,drag
            try:rect=drag(ImageRect(x,y,width,height),mode,delta_x,delta_y,item.metadata.width,item.metadata.height)
            except GeometryError:return False
        return self.setCropRect(rect.x, rect.y, rect.width, rect.height)

    @Slot(bool)
    def setCropPreview(self, preview: bool) -> None:
        item = self._workspace.active
        if item is not None and item.crop.preview != preview:
            item.crop.preview = preview
            self.workspaceChanged.emit()

    @Slot(int, int, result=bool)
    def setResizeSize(self, width: int, height: int) -> bool:
        valid = self._workspace.set_resize(width, height)
        if valid:
            self.workspaceChanged.emit()
        return valid

    def _admit_video_source(self,path):
        if self._playback is None or getattr(self._playback,'posters',None) is None:return True
        try:
            if self.imageTransition:self._image_service.admit_video_resume()
            self._playback.posters.activate(path);return True
        except (OSError,ValueError,RuntimeError) as error:
            if getattr(error,'retained_resources',()) and self._processing_gate is not None:
                self._processing_gate.quarantine(error,'Video source lock ownership unresolved.')
            self._error_message=str(error)
            if self._image_preview is not None:
                self._image_preview._set_status(str(error),'error');self._image_preview.changed.emit()
            self.contentChanged.emit()
            return False

    @Slot(str, result=bool)
    def selectMedia(self, item_id: str) -> bool:
        return self._select_media(item_id)

    def _select_media(self, item_id, *, shared=False):
        if not (self.canImport if shared else self.canOpen):
            return False
        before = self._workspace.active
        candidate = next((i for i in self._workspace.items if i.id == item_id), None)
        if candidate is None:
            return False
        self._pause_sequence()
        self.cancelSequenceConfirmation()
        had_error = bool(self._error_message)
        self._error_message = ""
        if isinstance(candidate.metadata.source,SequenceSource):
            return self._request_sequence(candidate.metadata.source,candidate.sequence.current_slot,candidate.id,shared=shared)
        if isinstance(candidate.metadata.source, StillSource):
            return self._image_preview.request(candidate.metadata.source_path,candidate.metadata.source,
                                               candidate.id,shared_source=shared)
        if not self._admit_video_source(candidate.metadata.source_path):return False
        if self._image_preview is not None:
            self._image_preview.cancel(stop_monitor=True)
        item = self._workspace.select(item_id)
        if item is None:
            return False
        source_changed_on_disk = (self._playback is not None and
                                 self._playback.timeline_source_changed(item.id,item.metadata.source_path))
        detached = self._playback is not None and self._playback._source is None
        self._retired_video_item=None
        if before is None or before.id != item.id or self._state != "loaded" or source_changed_on_disk or detached:
            self._activate_item(item, source_changed=(self._state != "loaded" or before is None
                                                      or before.canonical_path != item.canonical_path
                                                      or source_changed_on_disk or detached))
        self.workspaceChanged.emit()
        if had_error:
            self.contentChanged.emit()
        return True

    @Slot(str, result=bool)
    def duplicateMedia(self, item_id: str) -> bool:
        if not self.canOpen:
            return False
        self._save_video_session()
        item = self._workspace.duplicate(item_id)
        if item is None:
            return False
        self.workspaceChanged.emit()
        return True

    @Slot(str, result=bool)
    def removeMedia(self, item_id: str) -> bool:
        return self._remove_media(item_id)

    def clear_shared_source(self):
        item=self._workspace.active
        return bool(item and self._remove_media(item.id,shared=True))

    def _remove_media(self,item_id,*,shared=False):
        if not (self.canImport if shared else self.canOpen):
            return False
        before = self._workspace.active
        removed=next((item for item in self._workspace.items if item.id==item_id),None)
        if removed is None:
            return False
        if self._image_preview is not None and before is not None and before.id == item_id:
            self._image_preview.cancel(stop_monitor=True)
            self._retired_video_item=None
        item = self._workspace.remove(item_id)
        self._release_unused_manifests()
        if self._playback is not None:
            self._playback.forget_timeline(item_id)
            if (getattr(self._playback,'posters',None) is not None and
                    not any(i.canonical_path==removed.canonical_path for i in self._workspace.items)):
                self._playback.posters.forget(removed.metadata.source_path)
        if before is not None and before.id != self._workspace.active_id:
            if item is None:
                self._remember_video()
                self._metadata = None
                if self._playback is not None:
                    self._playback.clear_source()
                self._trim.set_source(None)
                self._crop.set_source(None)
                self._resize.set_source(None)
                self._set_state("empty")
                self.contentChanged.emit()
            else:
                if isinstance(item.metadata.source, (StillSource,SequenceSource)):
                    # Removal explicitly ends the old source's ownership. Do not
                    # label its old picture with an asynchronously selected item.
                    self._metadata = None
                    if self._playback is not None: self._playback.clear_source()
                    self._trim.set_source(None)
                    self._crop.set_source(None)
                    self._resize.set_source(None)
                    self._set_state('loading')
                    if isinstance(item.metadata.source,SequenceSource):
                        self._request_sequence(item.metadata.source,item.sequence.current_slot,item.id,shared=shared)
                    else: self._image_preview.request(item.metadata.source_path,item.metadata.source,item.id)
                else:
                    if self._admit_video_source(item.metadata.source_path):
                        self._activate_item(item, source_changed=before.canonical_path != item.canonical_path)
                    else:
                        self._remember_video();self._metadata=None
                        self._playback.clear_source();self._set_state('error')
        self._release_unused_manifests()
        self.workspaceChanged.emit()
        return True

    @Slot(str, int, result=bool)
    def moveMedia(self, item_id: str, direction: int) -> bool:
        if not self.canOpen:
            return False
        moved = self._workspace.move(item_id, direction)
        if moved:
            self.workspaceChanged.emit()
        return moved

    def _save_video_session(self):
        owner=self._video_session_owner
        if owner is not None and self._playback is not None and not self._playback.imageMode:
            owner.video.position_ms=self._playback.position
            owner.video.signature=self._playback._timeline_signature
            pc=self._playback
            if pc._origin_established:
                owner.video.offset_us=pc._timeline_offset
            if pc.posters is not None and pc.framesReliable:
                pc.posters.remember(pc._timeline_signature,pc._presentation_sink.videoFrame(),pc._current_index,pc._timeline,pc._timeline_offset,capture_gpu=True)

    def _remember_video(self):
        self._save_video_session()
        self._video_session_owner=None

    def _activate_item(self, item, *, source_changed: bool = True) -> None:
        self._retired_video_item=None
        self._remember_video()
        if self._image_service and not item.metadata.audio_only:self._image_service.video_texture(item.metadata.width,item.metadata.height,
            getattr(item.metadata.info_facts,'bit_depth',None) or 8)
        self._viewer_viewport.fit()
        self._reveal_message = ''
        self._metadata = MediaMetadataView.from_dto(item.metadata)
        if source_changed and self._playback is not None:
            posters=getattr(self._playback,'posters',None)
            if item.metadata.audio_only:
                self._playback.set_source(item.metadata.source_path,audio_only=True)
            elif posters is not None:
                poster=posters.picture(item.metadata.source_path,item.video.position_ms)
                self._playback.set_source(item.metadata.source_path,poster=poster)
            else:self._playback.set_source(item.metadata.source_path)
        if self._playback is not None:
            self._playback.configure_timeline(item.id,item.metadata.source_path)
            self._sync_trim_loop()
        self._trim.set_source(item.metadata)
        self._crop.set_source(None if item.metadata.audio_only else item.metadata)
        self._resize.set_source(None if item.metadata.audio_only else item.metadata)
        self._error_message = ""
        self._set_state("loaded")
        self._video_session_owner=item
        if self._playback is not None:
            from mediaforge.application.contracts.source_timeline import SourceSignature
            try:unchanged=item.video.signature==SourceSignature.capture(item.metadata.source_path)
            except (OSError,ValueError):unchanged=False
            if not source_changed:
                # Duplicate rows share an already owned physical source/decoder,
                # but each row restores its independent compact transport intent.
                if self._playback.position!=item.video.position_ms:self._playback.seek(item.video.position_ms)
            elif unchanged or item.metadata.audio_only:
                posters=getattr(self._playback,'posters',None)
                if posters is not None and not item.metadata.audio_only:
                    offset=posters.request_offset(item.metadata.source_path,item.video.position_ms)
                    if offset is None:offset=item.video.offset_us
                    self._playback.restore_session(item.video.position_ms,request_offset=offset)
                else:self._playback.restore_session(item.video.position_ms)
        self.contentChanged.emit()

    def _activate_image(self, item, prepared, frame=None, slot=0):
        self._remember_video()
        self._retired_video_item=None
        self._sequence_error = ''
        self._viewer_viewport.fit()
        self._metadata = MediaMetadataView.from_dto(item.metadata)
        if self._playback is not None:
            start = int(item.metadata.source.manifest.time(slot,item.sequence.rate)*1_000_000) if isinstance(item.metadata.source,SequenceSource) else 0
            self._playback.set_image(prepared, frame,start_us=start)
        else:
            prepared.release()
        self._trim.set_source(None)
        self._crop.set_source(None)
        self._resize.set_source(None)
        self._error_message = ''
        self._set_state('loaded')
        self.contentChanged.emit()

    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self._state

    @Property(bool, notify=canOpenChanged)
    def canOpen(self) -> bool:
        return not self._external_page and self.canImport

    @Property(bool, notify=canOpenChanged)
    def canImport(self) -> bool:
        gate = self._processing_gate
        return (not self._shutdown and self._thread is None and not self._trim.busy
                and not self._crop.busy and not self._resize.busy and not self._output.busy
                and not (gate and (gate.busy or gate.failure)))

    def original_source(self):
        return self._workspace.original_reference() if self._state == 'loaded' else None

    def active_matches_source(self, path):
        item = self._workspace.active
        if item is None:
            return False
        key = canonical_path(path)
        source = item.metadata.source
        return item.canonical_path == key or (
            isinstance(source, SequenceSource)
            and any(os.path.normcase(member.path) == key for member in source.manifest.members))

    @Property(bool, notify=workspaceChanged)
    def hasSelectedSource(self):
        return self._workspace.active is not None

    def open_shared_source(self, url):
        return self._open_media(url, shared=True)

    def setExternalPage(self, active):
        self._external_page=bool(active)
        self._sync_trim_loop()
        self.previewChanged.emit()
        self.canOpenChanged.emit()

    @Property(str, notify=contentChanged)
    def errorMessage(self) -> str:
        return self._error_message

    @Property(str, notify=contentChanged)
    def fileName(self) -> str:
        return self._field("file_name")

    @Property('QVariantList', notify=infoChanged)
    def mediaInfoRows(self):
        item = self._workspace.active if self._state == 'loaded' else None
        rows=info_rows(item.metadata if item else None,
                         getattr(self._playback, 'exactFramesText', 'Unavailable'))
        if item and self._image_preview:
            if self.sourceKind=='video':
                detail='Source resolution · Qt Multimedia'
            elif self.sourceKind=='audio':detail='Audio transport; no video raster'
            else:
                detail = (f'Display raster · {self._playback.imageWidth}×{self._playback.imageHeight}'
                          if self._playback and self._playback.imageMode else '')
            if detail:
                rows.append(dict(label='Viewer only',value=detail,section=False))
        return rows

    @Slot()
    def _refresh_info_frames(self):
        value = (getattr(self._playback, 'exactFramesText', 'Unavailable'),
                 getattr(self._playback, 'timelineAvailable', False))
        if value != self._last_info_frames:
            self._last_info_frames = value
            self.infoChanged.emit()

    @Property(str, notify=infoChanged)
    def revealMessage(self):
        return self._reveal_message

    @Slot(result=bool)
    def revealCurrentSource(self):
        item = self._workspace.active if self._state == 'loaded' and not self._shutdown else None
        if item is None or self._source_revealer is None:
            self._reveal_message = 'Source location is unavailable.'
            self.infoChanged.emit()
            return False
        try:
            result = self._source_revealer.reveal(item.metadata.source_path)
            self._reveal_message = result.message
            success = result.succeeded
        except Exception:
            self._reveal_message = 'Source location could not be opened.'
            success = False
        self.infoChanged.emit()
        return success

    @Property(str, notify=contentChanged)
    def localPath(self) -> str:
        return self._field("local_path")

    @Property(str, notify=contentChanged)
    def formatName(self) -> str:
        return self._field("format_name")

    @Property(str, notify=contentChanged)
    def duration(self) -> str:
        return self._field("duration")

    @Property(str, notify=contentChanged)
    def codec(self) -> str:
        return self._field("codec")

    @Property(str, notify=contentChanged)
    def resolution(self) -> str:
        return self._field("resolution")

    @Property(str, notify=contentChanged)
    def frameRate(self) -> str:
        return self._field("frame_rate")

    @Slot(QUrl, result=bool)
    def openMedia(self, source_url: QUrl) -> bool:
        return self._open_media(source_url)

    def openExportedResult(self,result):
        """Explicit second phase. Never activates from a failed Export callback."""
        if not self.canOpen:return False
        if result.kind in ('video','audio'):return self._open_media(QUrl.fromLocalFile(result.path),exported=result)
        if result.kind not in ('still','sequence') or self._image_preview is None:return False
        self._auto_sequence=False
        self._pause_sequence()
        self.cancelSequenceConfirmation()
        return self._image_preview.request(result.path,discover=result.kind=='sequence',exported=result)

    def _open_media(self,source_url,exported=None,*,shared=False):
        if not (self.canImport if shared else self.canOpen):
            return False
        self._auto_sequence = False
        self._pause_sequence()
        self.cancelSequenceConfirmation()
        if source_url.isEmpty() or not source_url.isValid():
            return False
        if not source_url.isLocalFile():
            self._set_error("Choose an existing local media file.")
            return False
        source_path = source_url.toLocalFile()
        if not source_path:
            return False

        existing = self._workspace.find_path(source_path)
        if existing is not None and exported is None:
            return self._select_media(existing.id, shared=shared)

        if exported is None and (Path(source_path).suffix.lower() in IMAGE_SUFFIXES
                or self._image_service is not None and self._image_service.is_image(source_path)):
            self._error_message = ""
            if self._image_service is None:
                self._error_message = 'Image import is unavailable.'
                self.contentChanged.emit()
                return False
            if Path(source_path).suffix.lower() in ('.png','.jpg','.jpeg','.tif','.tiff'):
                from mediaforge.application.contracts.sequence import numbered_name
                try: numbered_name(Path(source_path).name)
                except ValueError: pass
                else:
                    self._auto_sequence = True
                    self._sequence_shared = shared
                    return self._image_preview.request(source_path,discover=True,shared_source=shared,automatic_sequence=True)
            return self._image_preview.request(source_path,shared_source=shared)

        if self._image_preview is not None:
            self._image_preview.cancel(stop_monitor=True)
        self._error_message = ""
        if self._metadata is None: self._set_state("loading")
        self.contentChanged.emit()

        thread = QThread(self)
        self._opening_exported = exported
        worker = _ProbeWorker(self._use_case, source_path,exported)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_result)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._release_thread)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        self.canOpenChanged.emit()
        thread.start()
        return True

    @Slot('QVariantList', result=bool)
    def canDropMedia(self, urls: list) -> bool:
        # One file per drop/browse is intentional; repeated Add builds the tray.
        # Validate the payload before highlighting or changing the current source.
        if not self.canOpen or len(urls) != 1:
            return False
        from pathlib import Path
        url = QUrl(urls[0])
        return (url.isValid() and url.isLocalFile()
                and Path(url.toLocalFile()).is_absolute()
                and Path(url.toLocalFile()).is_file())

    @Slot('QVariantList', result=bool)
    def dropMedia(self, urls: list) -> bool:
        return self.canDropMedia(urls) and self.openMedia(QUrl(urls[0]))

    @Slot()
    def cancelSelection(self) -> None:
        """A cancelled picker intentionally preserves the current state."""

    @Slot(object)
    def _handle_result(self, result: OpenMediaResult) -> None:
        if result.guard is None:return self._handle_open_result(result)
        if self._shutdown:return result.guard.close()
        try:
            with result.guard.activating():self._handle_open_result(result)
        except Exception as error:
            try:result.guard.close()
            finally:
                self._error_message=str(error);self.contentChanged.emit()

    def _handle_open_result(self,result):
        if self._shutdown:
            if result.guard is not None:result.guard.close()
            return
        try:
            if result.guard is not None:result.guard.check()
            if result.succeeded and result.metadata is not None:
                previous=self._workspace.find_path(result.metadata.source_path)
                if (getattr(self,'_opening_exported',None) is not None and previous is not None
                        and previous.metadata!=result.metadata):
                    raise ValueError('Exported video metadata changed. Remove its old Tray item before reopening.')
                if not self._admit_video_source(result.metadata.source_path):return
                item, _ = self._workspace.add(result.metadata)
                self._activate_item(item)
                self.workspaceChanged.emit()
            else:
                self._error_message = result.error_message
                if self._metadata is None: self._set_state("error")
                elif self._playback is not None and self._playback.imageMode and self._image_preview is not None:
                    self._image_preview.resume_current_monitor()
        except Exception:
            self._logger.exception("Could not format metadata result for presentation")
            self._error_message = "Media metadata could not be displayed. Try another file."
            if self._metadata is None: self._set_state("error")
            elif self._playback is not None and self._playback.imageMode and self._image_preview is not None:
                self._image_preview.resume_current_monitor()
        finally:
            if result.guard is not None:
                try:result.guard.close()
                except Exception as error:self._error_message=str(error)
        self.contentChanged.emit()

    @Slot()
    def _release_thread(self) -> None:
        self._thread = None
        self._worker = None
        if self.imageTransition and not self._image_preview.busy and self._pending_sequence is None:
            self._restore_retired_video()
        self.canOpenChanged.emit()
        self._output.changed.emit()

    @Slot()
    def shutdown(self) -> None:
        if self._shutdown:
            if self._shutdown_error is not None:
                raise self._shutdown_error
            return
        self._shutdown = True
        self._retired_video_item=None
        self.canOpenChanged.emit()
        # Attempt each existing teardown once even if another operation failed.
        # Keep the first failure durable across aboutToQuit and bootstrap finally.
        teardowns = [self._shutdown_metadata] + ([self._image_preview.shutdown] if self._image_preview else []) + [self._trim.shutdown, self._crop.shutdown, self._resize.shutdown, self._output.shutdown,
                     self._window_presentation.shutdown]
        if self._playback is not None:
            teardowns.append(self._playback.shutdown)
        for teardown in teardowns:
            try:
                teardown()
            except Exception as error:
                if self._shutdown_error is None:
                    self._shutdown_error = error
                self._logger.exception("Media shutdown cleanup failed")
        if self._shutdown_error is not None:
            raise self._shutdown_error
        self.cancelSequenceConfirmation()
        for owner in self._sequence_leases.values(): owner.release()
        self._sequence_leases.clear()

    @property
    def shutdown_error(self) -> Exception | None:
        return self._shutdown_error

    def _shutdown_metadata(self) -> None:
        thread = self._thread
        if thread is not None and thread.isRunning():
            if self._worker is not None:self._worker.cancelled.set()
            try:
                self._use_case.cancel()
            except Exception:
                self._logger.exception("Could not request metadata probe cancellation")
            thread.quit()
            stopped = thread.wait()
            if not stopped or thread.isRunning():
                raise RuntimeError("Metadata worker did not stop during shutdown")
        if thread is not None:
            self._thread = None
            self._worker = None
            self.canOpenChanged.emit()
        if self._state == "loading":
            self._metadata = None
            self._error_message = "Reading media metadata was cancelled."
            self._set_state("error")
            self.contentChanged.emit()

    def _field(self, name: str) -> str:
        if self._metadata is None:
            return ""
        return str(getattr(self._metadata, name))

    def _set_error(self, message: str) -> None:
        if self._metadata is not None:
            self._error_message = message
            self.contentChanged.emit()
            return
        self._metadata = None
        if self._playback is not None:
            self._playback.clear_source()
        self._trim.set_source(None)
        self._crop.set_source(None)
        self._resize.set_source(None)
        self._error_message = message
        self._set_state("error")
        self.contentChanged.emit()

    def _set_state(self, state: str) -> None:
        if self._state != state:
            self._state = state
            self.stateChanged.emit()

    @Slot(QObject, QObject)
    def registerInspectorWheel(self, scroll, window):
        from mediaforge.presentation.bridge.inspector_wheel import InspectorWheelRouting
        self._inspector_wheel = InspectorWheelRouting(scroll, window, self)
