"""One off-GUI preparation and one latest intent for the persistent publisher."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, replace
import logging
import threading
import time
from PySide6.QtCore import QObject, Property, Signal, Slot, Qt, QTimer
from mediaforge.application.services.images import ImageRendererPressure


@dataclass(frozen=True, slots=True)
class _Request:
    serial: int
    path: str
    expected: object
    item_id: str | None
    quality: str
    viewport: tuple[int,int]
    cancel: threading.Event
    requested: float
    window_key: tuple | None
    context: object = None
    discover: bool = False
    validate_manifest: bool = False
    exported: object = None
    prefetch: bool = False
    view: object = None
    reuse: object = None
    after_retirement: bool = False
    shared_source: bool = False
    automatic_sequence: bool = False
    renderer_refusal: ImageRendererPressure | None = None


class _BackendProbe(QObject):
    observed = Signal(object)
    textureLimit = Signal(int)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.sampled = False
        self.closed = False
        self.connected = True
        window.beforeRendering.connect(self.inspect, Qt.ConnectionType.DirectConnection)
        window.sceneGraphInvalidated.connect(self.invalidated, Qt.ConnectionType.DirectConnection)

    @Slot()
    def invalidated(self):
        if self.closed:return
        self.sampled = False
        self.observed.emit(None)
        if not self.connected:
            self.window.beforeRendering.connect(self.inspect,Qt.ConnectionType.DirectConnection)
            self.connected=True

    def close(self):
        self.closed=True
        self.window.sceneGraphInvalidated.disconnect(self.invalidated)
        if self.connected:
            self.window.beforeRendering.disconnect(self.inspect)
            self.connected=False

    @Slot()
    def inspect(self):
        if self.closed or self.sampled: return
        self.sampled = True
        # One observation per scenegraph lifetime. Keeping a Python callback on
        # every render can invert the GIL/GUI synchronization order in Qt tests.
        self.window.beforeRendering.disconnect(self.inspect)
        self.connected=False
        try:
            # Render-thread only. No borrowed native handles or pointer conversions.
            rhi = self.window.rhi()
            info = rhi.driverInfo()
            from PySide6.QtGui import QRhi
            self.textureLimit.emit(rhi.resourceLimit(QRhi.ResourceLimit.TextureSizeMax))
            self.observed.emit((self.window.rendererInterface().graphicsApi().name,
                                info.vendorId,info.deviceId))
        except Exception:
            self.observed.emit(None)


class ImagePreviewController(QObject):
    changed = Signal()
    failed = Signal(object, str)
    discovered = Signal(object)
    exportedDiscovered = Signal(object,object)
    completed = Signal(object,object,object)
    viewChanged = Signal()
    # This initial limited envelope is qualified by native Checkpoint-A evidence.
    # No capacity claim for other hardware/backends follows from a GPU name alone.
    qualified_backend = ('Direct3D11',4318,10370)

    def __init__(self, service, activate, parent=None):
        super().__init__(parent)
        self.service = service
        self._activate = activate
        self._pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='image-preview')
        self._serial = 0
        self._active = self._pending = self._future = None
        self._pressure_future = None
        self._closed = False
        self._quality = 'Auto'
        self._status = ''
        self._status_kind = 'idle'
        self._effective = ''
        self._channel = 'RGBA'
        self._backend = None
        self._window = self._probe = None
        self._viewport = (1920,1080)
        self._pressure_fault = None
        self._view = None
        self.live_prepared = None
        self.retire_video = None
        self.retirement_pending = None
        self.retirement_wait_seconds = 5.0
        self._nearby = None
        self.completed.connect(self._done,Qt.ConnectionType.QueuedConnection)
        self._pressure_result.connect(self._pressure_done,Qt.ConnectionType.QueuedConnection)
        self._monitor = QTimer(self)
        self._monitor.setInterval(1000)
        self._monitor.timeout.connect(self._observe_pressure)

    def attach(self,window):
        if self._window is not None: return
        self._window = window
        self._probe = _BackendProbe(window)
        self._probe.textureLimit.connect(self._texture_limit,Qt.ConnectionType.QueuedConnection)
        self._probe.observed.connect(self._backend_changed,Qt.ConnectionType.QueuedConnection)
        window.sceneGraphError.connect(self._renderer_error)

    @Slot(int)
    def _texture_limit(self,limit):
        # Public capability is necessary, not sufficient: the measured UHD pool
        # and exact qualified backend remain independent admission conditions.
        self.service.texture_limit=max(0,limit)

    @Slot(object)
    def _backend_changed(self,backend):
        if self._backend != backend:
            self.cancel(stop_monitor=True)
        self._backend = backend

    @Slot(object,str)
    def _renderer_error(self,_code,message):
        self._backend = None
        self.cancel()
        self._set_status('Image renderer unavailable: '+message,'error')
        self.changed.emit()

    @Property(str,notify=changed)
    def quality(self): return self._quality
    @Property(str,notify=changed)
    def status(self): return self._status
    @Property(str,notify=changed)
    def statusKind(self): return self._status_kind
    @Property(int,notify=changed)
    def requestGeneration(self): return self._serial

    def _set_status(self,text='',kind='idle'):
        self._status,self._status_kind = text,kind

    @Property(str,notify=changed)
    def notice(self):
        if not self._status:return ''
        if self._status_kind=='progress':return 'Preparing preview…'
        return 'Preview unavailable · click for details'
    @Property(str,notify=changed)
    def effective(self): return self._effective
    @Property(str,notify=changed)
    def channel(self): return self._channel
    @Property(bool,notify=changed)
    def busy(self): return (self._active is not None and not self._active.prefetch) or self._pending is not None

    @Slot(float,float)
    def setViewport(self,width,height):
        dpr = self._window.devicePixelRatio() if self._window else 1
        self._viewport = (max(1,round(width*dpr)),max(1,round(height*dpr)))
        if self._window is not None and hasattr(self.service,'render_surface'):
            try:self.service.render_surface(round(self._window.width()*dpr),round(self._window.height()*dpr))
            except ValueError as error:self._set_status(str(error),'error');self.changed.emit()

    @Slot(str,int,int,int,int,int,int)
    def setView(self,item_id,x,y,width,height,out_width,out_height):
        value=(item_id,(x,y,width,height,max(1,out_width),max(1,out_height)))
        if value!=self._view:
            self._view=value
            self.viewChanged.emit()

    def resume_current_monitor(self):
        """Resume observation after a failed video probe kept the image active."""
        if not self._closed and self._backend is not None and not self._pressure_fault:
            self._monitor.start()

    def request(self,path,expected=None,item_id=None,quality=None,context=None,discover=False,validate_manifest=False,exported=None,*,shared_source=False,automatic_sequence=False):
        if self._closed: return False
        # Pressure is re-admitted on the worker before allocation, not a durable
        # device-loss diagnosis. A later healthy source/resize may recover.
        self._pressure_fault=None
        if quality is not None:
            if quality not in ('Auto','1:1','1:2','1:4'): return False
            self._quality = quality
        # Publish one complete latest-intent state. Observers also reconcile
        # manifest ownership, so an intermediate empty cancellation would retire
        # the incoming manifest before this request owns it.
        self.cancel(stop_monitor=True,notify=False)
        viewport = self._viewport
        if min(viewport) <= 1 and self._window is not None:
            # The loaded-page Viewer may still be hidden on first import. The
            # whole window is a conservative footprint until layout is available.
            dpr = self._window.devicePixelRatio()
            viewport = (round(self._window.width()*dpr),round(self._window.height()*dpr))
        owner=self.live_prepared() if self.live_prepared is not None else None
        reuse=(owner.borrow() if owner is not None and not owner.released and owner.raster.source==expected
               and owner.raster.plan.full_source else None)
        view=self._view[1] if self._view is not None and self._view[0]==item_id and item_id else None
        request = _Request(self._serial,str(path),expected,item_id,self._quality,
                           viewport,threading.Event(),time.perf_counter(),self._window_key(),context,
                           discover,validate_manifest,exported,view=view,reuse=reuse,shared_source=shared_source,
                           automatic_sequence=automatic_sequence)
        self._set_status('Preparing image…','progress')
        if self._active is not None:
            self._pending = request
        else:
            self._start(request)
        self.changed.emit()
        return True

    def admit_fullscreen(self,window):
        sample = self.service.resources.last if self.service.resources else None
        dpr = window.devicePixelRatio()
        geometry = window.screen().geometry()
        current = window.width()*window.height()*dpr*dpr
        target = geometry.width()*geometry.height()*dpr*dpr
        increment = max(0,target-current)*8  # two additional RGBA swapchain surfaces
        if (self.busy or self._backend != self.qualified_backend or sample is None
                or geometry.width()*geometry.height()*dpr*dpr > 3840*2160
                or time.monotonic()-sample['time'] > 2
                or sample['dedicated']+sample['shared']+increment > self.service.render_reservation+increment):
            self._set_status('Image fullscreen is outside the current renderer headroom. The windowed picture is preserved.','error')
            self.changed.emit()
            return False
        self.service.render_surface(round(geometry.width()*dpr),round(geometry.height()*dpr))
        return True

    def _window_key(self):
        if self._window is None: return None
        return (self._window.width(),self._window.height(),self._window.devicePixelRatio())

    def cancel(self,stop_monitor=False,*,notify=True):
        self._serial += 1
        if self._active is not None: self._active.cancel.set()
        if self._pending is not None:
            self._pending.cancel.set()
            if self._pending.reuse is not None:self._pending.reuse.release()
        self._pending = None
        self._nearby = None
        self._set_status()
        if stop_monitor: self._monitor.stop()
        if notify:self.changed.emit()

    def _start(self,request):
        if (not request.prefetch and not request.after_retirement and self.retirement_pending is not None
                and self.retirement_pending()):
            request=replace(request,after_retirement=True)
        if self._backend != self.qualified_backend or self._pressure_fault:
            self._set_status(self._pressure_fault or 'Image previews are unavailable on this unqualified renderer. Video is unchanged.','error')
            self.changed.emit()
            self.failed.emit(request,self._status)
            if request.reuse is not None:request.reuse.release()
            return
        if self._window is not None and hasattr(self.service,'render_surface'):
            try:self.service.render_surface(round(self._window.width()*self._window.devicePixelRatio()),round(self._window.height()*self._window.devicePixelRatio()))
            except ValueError as error:
                self._set_status(str(error),'error');self.failed.emit(request,self._status);self.changed.emit()
                if request.reuse is not None:request.reuse.release()
                return
        self._active = request
        options={}
        if request.view is not None:options['view']=request.view
        if request.reuse is not None:options['reuse']=request.reuse
        if request.exported is not None and request.discover:
            future=self._submit(request,self.service.discover_exported_sequence,request.exported,request.cancel)
        elif request.exported is not None and request.context is not None:
            source,slot,_=request.context
            future=self._submit(request,self.service.prepare_exported_sequence_preview,request.exported,source,slot,
                request.quality,request.cancel,request.viewport)
        elif request.exported is not None:
            future=self._submit(request,self.service.prepare_exported_preview,request.exported,request.quality,request.cancel,request.viewport)
        elif request.discover:
            future = self._submit(request,self.service.discover_sequence,request.path,request.cancel,
                                  **({'automatic': True} if request.automatic_sequence else {}))
        elif request.context is not None:
            future = self._submit(request,self.service.prepare_sequence_preview,request.path,request.quality,
                request.cancel,request.expected,request.viewport,request.context[0].manifest,request.validate_manifest,**options)
        else:
            future = self._submit(request,self.service.prepare_preview,request.path,request.quality,
                                      request.cancel,request.expected,request.viewport,**options)
        self._future = future
        def finished(done):
            try: value,error = done.result(),None
            except Exception as failure:
                value,error = None,(failure if isinstance(failure,ImageRendererPressure) else str(failure))
                # A terminal Future traceback otherwise retains decoder QImages
                # and worker frames after their application leases are released.
                failure.__traceback__ = None
            self.completed.emit(request,value,error)
        future.add_done_callback(finished)

    def _submit(self,request,prepare,*args,**kwargs):
        def run():
            attempts=0
            refusal = request.renderer_refusal
            deadline=time.monotonic()+self.retirement_wait_seconds
            while True:
                if request.after_retirement:
                    # Destroying the QObject does not certify residency. Fresh
                    # eligibility precedes every preparation, including one
                    # possible transient post-decode pressure refusal.
                    while True:
                        if request.cancel.is_set():raise ValueError('Image preparation canceled.')
                        try:
                            if getattr(refusal,'admission',None) is not None:
                                self.service.recheck_renderer_admission(refusal)
                            else:
                                self.service.check_pressure()
                        except ImageRendererPressure as pressure:
                            if time.monotonic()>=deadline:
                                self._log_retirement('deadline', request, pressure)
                                raise ValueError('Video resources still exceed renderer headroom after retirement. Image activation refused.')
                            request.cancel.wait(.05)
                        else:break
                    if time.monotonic()>=deadline:
                        raise ValueError('Renderer eligibility wait expired after retirement. Image activation refused.')
                if request.cancel.is_set():raise ValueError('Image preparation canceled.')
                attempts+=1
                try:return prepare(*args,**kwargs)
                except ImageRendererPressure as error:
                    if not request.after_retirement or attempts>=2 or time.monotonic()>=deadline:raise
                    # The service has released its failed preparation. Do not
                    # let the Future/traceback keep old decoder arrays alive
                    # while admitting another full cold peak.
                    error.__traceback__=None
                    refusal = error
                    request.cancel.wait(.05)
        return self._pool.submit(run)

    def _log_retirement(self, phase, request, refusal):
        sample = getattr(refusal, 'sample', {})
        logging.getLogger('mediaforge').info(
            'Image transition %s generation=%d elapsed_ms=%.1f texture_charge=%s gpu=%s/%s private=%s',
            phase, request.serial, (time.perf_counter()-request.requested)*1000,
            getattr(refusal,'admission',None), sample.get('dedicated'), sample.get('shared'), sample.get('private'))

    @Slot(object,object,object)
    def _done(self,request,value,error):
        self._active = self._future = None
        if self._closed or request.serial != self._serial or request.cancel.is_set():
            if value is not None and hasattr(value,'release'): value.release()
        elif request.prefetch:
            if value is not None:value.release()
            # Speculation never activates, reports a false current frame, or
            # hides a failure of the next real request (which checks again).
        elif value is None:
            if (isinstance(error,ImageRendererPressure) and not request.after_retirement
                    and self.retire_video is not None):
                try:retired=self.retire_video(request)
                except Exception as failure:retired=False;error=str(failure)
                if retired:
                    self._log_retirement('retired-player', request, error)
                    if not self._closed and request.serial==self._serial and not request.cancel.is_set():
                        self._start(replace(request,after_retirement=True,renderer_refusal=error))
                    else:
                        if request.reuse is not None:request.reuse.release()
                        self.changed.emit()
                    return
            self.service.channel=self._channel
            self._set_status(str(error) if error else 'Image preview unavailable.','error')
            self.failed.emit(request,self._status)
        elif request.discover:
            self._set_status()
            if request.exported is not None:self.exportedDiscovered.emit(request.exported,value)
            else:self.discovered.emit(value)
        else:
            try:
                if self._backend != self.qualified_backend:
                    raise ValueError('Viewer configuration changed during preparation. The previous picture is preserved; select again.')
                # Source stays write/delete-denied until GUI publication completes.
                activating=getattr(getattr(value,'source_guard',None),'activating',None)
                if activating is not None:
                    with activating():self._activate(request,value)
                else:self._activate(request,value)
                self._channel=value.raster.channel
                self._effective = f'{request.quality} → {value.raster.plan.effective_quality} · {value.raster.plan.width}×{value.raster.plan.height}'
                if getattr(value.raster.plan,'full_source',False):
                    self._effective += f' · full source staged {value.raster.source.facts.width}×{value.raster.source.facts.height}; visible source region {value.raster.plan.region}'
                self._set_status()
                self._monitor.start()
            except Exception as failure:
                value.release()
                self._set_status(str(failure),'error')
                self.failed.emit(request,self._status)
        if request.reuse is not None:request.reuse.release()
        pending,self._pending = self._pending,None
        if not self._closed and pending is not None and pending.serial == self._serial:
            self._start(pending)
        elif not self._closed and self._nearby is not None:
            source,slot,item_id=self._nearby;self._nearby=None
            member=source.manifest.members[slot]
            if not any(key[0]==member for key in self.service._cache):
                self._start(_Request(self._serial,member.path,member,item_id,self._quality,self._viewport,
                    threading.Event(),time.perf_counter(),self._window_key(),(source,slot,None),prefetch=True))
        self.changed.emit()

    def prefetch(self,source,slot,item_id):
        f=source.manifest.members[slot].facts
        if f.width*f.height*4>self.service.cache_limit:return
        self._nearby=(source,slot,item_id)

    def _observe_pressure(self):
        if self._closed or self._active is not None or self._pressure_future is not None: return
        # Same executor as preparation: no parallel allocation or growing watcher queue.
        future = self._pool.submit(self.service.check_pressure)
        self._pressure_future = future
        generation = self._serial
        def observed(done):
            try: done.result(); error = None
            except Exception as failure:
                error = (ImageRendererPressure(str(failure)) if isinstance(failure,ImageRendererPressure) else str(failure))
            self._pressure_result.emit((generation,error))
        future.add_done_callback(observed)

    _pressure_result = Signal(object)

    @Slot(object)
    def _pressure_done(self,result):
        self._pressure_future = None
        generation,error = result
        if self._closed or generation != self._serial: return
        if isinstance(error, ImageRendererPressure):
            message = str(error)
            if self._pressure_fault != message:
                self._pressure_fault = message
                self._set_status(message, 'error')
                self.changed.emit()
            # Observation cannot admit a texture. Keep sampling so a transient
            # refusal cannot become a permanent error on an already painted image.
            return
        if error is None and self._pressure_fault:
            self._pressure_fault = None
            self._set_status()
            self.changed.emit()
            return
        if error:
            self._pressure_fault = error
            self.cancel()
            self._set_status(error,'error')
            self._monitor.stop()
            self.changed.emit()

    def shutdown(self):
        if self._closed: return
        self._closed = True
        self.cancel()
        self._monitor.stop()
        if self._probe is not None:self._probe.close()
        for future in (self._future,self._pressure_future):
            if future is None: continue
            try:
                value = future.result(timeout=10)
                if hasattr(value,'release'): value.release()
            except TimeoutError as error:
                raise RuntimeError('Image preparation ownership could not be reconciled at shutdown.') from error
            except Exception:
                # Terminal worker failures already ran the service's ownership
                # cleanup. Continue closing the executor and telemetry query.
                pass
        self._pool.shutdown(wait=True,cancel_futures=True)
        if self._active is not None and self._active.reuse is not None:self._active.reuse.release()
        self.live_prepared=None
        if hasattr(self.service,'close_cache'):self.service.close_cache()
        if self.service.resources is not None: self.service.resources.close()
