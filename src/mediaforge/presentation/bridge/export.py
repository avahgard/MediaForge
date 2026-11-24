"""Job snapshot presentation and explicit commands; no FFmpeg implementation knowledge."""
from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from dataclasses import asdict
from pathlib import Path

from mediaforge.application.contracts.export import ExportJob, ExportUseCase


class ExportViewModel(QObject):
    changed = Signal()
    eligibilityChanged = Signal()

    def __init__(self, use_case: ExportUseCase | None, workspace, revealer, parent):
        super().__init__(parent)
        self._use_case = use_case
        self._workspace = workspace
        self._owner = parent
        self._revealer = revealer
        self._job = ExportJob()
        self._job_id = None
        self._source_item = workspace.active if workspace is not None else None
        self._closed = False
        self._shutdown_error = None
        self._reveal_message = ""
        self._picture_state = None
        parent.workspaceChanged.connect(self._workspace_changed)
        self.changed.connect(self.eligibilityChanged)
        if getattr(parent, 'playback', None) is not None:
            parent.playback.changed.connect(self._picture_changed)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self.refresh)

    busy = Property(bool, lambda self: self._use_case is not None and self._use_case.busy, notify=changed)
    canExport = Property(bool, lambda self: self._use_case is not None and not self._closed
                         and self._owner.state == "loaded" and self._owner.canOpen
                         and not getattr(self._owner,'imageTransition',False)
                         and not (self._owner.sourceKind=='sequence' and self._workspace.active is not None
                                  and self._workspace.active.output.kind=='frame'
                                  and self._owner.playback.sequence.pending_slot is not None)
                         and bool(self.summary.get("valid")), notify=eligibilityChanged)
    canCancel = Property(bool, lambda self: self.busy and self._job.state == "Running" and not self._closed, notify=changed)
    state = Property(str, lambda self: self._job.state, notify=changed)
    stage = Property(str, lambda self: self._job.stage, notify=changed)
    message = Property(str, lambda self: self._job.message, notify=changed)
    outputPath = Property(str, lambda self: self._job.result, notify=changed)
    progress = Property(float, lambda self: self._job.progress, notify=changed)
    elapsed = Property(float, lambda self: self._job.elapsed, notify=changed)
    speed = Property(str, lambda self: self._job.speed, notify=changed)
    fps = Property(str, lambda self: self._job.fps, notify=changed)
    actionError = Property(str, lambda self: self._reveal_message, notify=changed)
    canOpenResult = Property(bool,lambda self:not self._closed and self._owner.canOpen
                            and self._result_plan() is not None and self._result_plan().viewer_supported,notify=changed)

    def _result_plan(self):
        if self._job.state!='Completed' or not self._job.result:return None
        getter=getattr(self._use_case,'completed_result',None)
        plan=getter() if getter else None
        return plan if plan is not None and plan.path==self._job.result and plan.job_id==self._job.job_id else None

    @Slot()
    def _workspace_changed(self):
        self._sync_source()
        self._picture_state = self._current_picture_state()
        self.changed.emit()

    def _current_picture_state(self):
        item = self._workspace.active if self._workspace is not None else None
        if item is None or item.output.kind not in ('frame', 'sequence') or self._owner.sourceKind != 'video':
            return None
        pc = self._owner.playback
        ready = (pc is not None and pc._source == Path(item.metadata.source_path)
                 and pc.timelineAvailable and (item.output.kind == 'sequence' or pc.framesReliable))
        ordinal = pc._current_index if ready and item.output.kind == 'frame' else None
        return item.id, item.output.kind, ready, ordinal

    @Slot()
    def _picture_changed(self):
        if self._closed:
            return
        # The playback signal also carries clock, volume and frame events.
        # Rebind Output only when its picture eligibility or ordinal changes.
        state = self._current_picture_state()
        if state != self._picture_state:
            self._picture_state = state
            self.eligibilityChanged.emit()

    def _sync_source(self):
        if self._workspace is None:
            return
        active = self._workspace.active
        if self._source_item is active:
            return
        self._source_item = active
        self._clear_presentation()

    def _clear_presentation(self):
        self._job = ExportJob()
        self._job_id = None
        self._reveal_message = ""

    @Slot(result=bool)
    def openExportedResult(self):
        plan=self._result_plan()
        if not self.canOpenResult or plan is None:return False
        self._reveal_message=''
        try:return self._owner.openExportedResult(plan)
        except (OSError,ValueError,RuntimeError) as error:
            self._reveal_message=str(error);self.changed.emit();return False

    @Property("QVariantMap", notify=changed)
    def settings(self):
        item = self._workspace.active
        if item is None:
            return {}
        s = item.output
        return asdict(s) | {"audio_streams":list(item.metadata.audio_streams)}

    @Property("QVariantMap", notify=eligibilityChanged)
    def summary(self):
        snapshot=None
        state = self._current_picture_state()
        if state is not None:
            if not state[2]:
                return dict(valid=False,message='Exact source picture/timeline unavailable. Wait for indexing and a settled frame.')
            try:snapshot=self._owner.playback.export_picture_snapshot()
            except ValueError as error:return dict(valid=False,message=str(error))
        return self._workspace.output_summary(snapshot)

    @Slot(str, str, result=bool)
    def setSetting(self, field, value):
        if self.busy or self._closed:
            return False
        self._reveal_message = ""
        valid = self._workspace.set_output(field, value)
        if not valid:
            self._reveal_message = ('Start Number must contain 1–19 ASCII digits.' if field=='sequence_start'
                                    else f"Invalid {field.replace('_', ' ')}. Choose a supported value.")
        self._picture_state = self._current_picture_state()
        self.changed.emit()
        return valid

    @Slot(QUrl)
    def chooseFolder(self, url):
        if url.isLocalFile():
            self.setSetting("folder", url.toLocalFile())

    @Slot(result=bool)
    def startExport(self):
        if not self.canExport:
            return False
        self._sync_source()
        self._clear_presentation()
        previous_id = self._use_case.snapshot().job_id
        try:
            active=self._workspace.active
            protected=[item.metadata.source_path for item in self._workspace.items]
            if active.output.kind in ('frame','sequence') and self._owner.sourceKind=='video':
                pc=self._owner.playback
                snapshot=pc.export_picture_snapshot()
                request=self._use_case.prepare(active,protected,snapshot=snapshot)
            else:
                request=self._use_case.prepare(active,protected)
            started=self._use_case.start_request(request)
        except (RuntimeError, ValueError, OSError) as error:
            self._reveal_message=str(error)
            self.changed.emit()
            return False
        if self._workspace.active is active:
            job_id = self._use_case.snapshot().job_id
            if job_id > previous_id:
                self._job_id = job_id
        self.refresh()
        if started:
            self._timer.start()
        return started

    @Slot()
    def cancelExport(self):
        if self.canCancel:
            self._use_case.cancel()
            self.refresh()

    @Slot(result=bool)
    def revealOutput(self):
        self._sync_source()
        if self._job.state != 'Completed' or not self._job.result or self._revealer is None:
            return False
        reveal = getattr(self._revealer, 'reveal_output', None) or self._revealer.reveal
        result = reveal(self._job.result)
        if not result.succeeded:
            self._reveal_message = result.message
        self.changed.emit()
        return result.succeeded

    @Slot()
    def refresh(self):
        if self._use_case is not None and not self._closed:
            self._sync_source()
            busy = self.busy
            job = self._use_case.snapshot()
            if self._job_id is not None and job.job_id == self._job_id:
                self._job = job
            elif self._job_id is not None and job.job_id > self._job_id:
                self._clear_presentation()
            if not busy:
                self._timer.stop()
            self.changed.emit()

    def shutdown(self):
        if self._closed:
            if self._shutdown_error:
                raise self._shutdown_error
            return
        self._closed = True
        self._timer.stop()
        try:
            if self._use_case is not None:
                self._use_case.shutdown()
        except Exception as error:
            self._shutdown_error = error
            raise
