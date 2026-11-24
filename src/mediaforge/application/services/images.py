"""Bounded prepare-before-activate image opening through an injected reader."""
from dataclasses import dataclass
from threading import RLock
from pathlib import Path
from collections import OrderedDict
from dataclasses import replace
import time
import os
import weakref
from mediaforge.application.contracts.image_source import ImageReader, ImageRaster, StillSource
from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.contracts.image_source import ImagePreviewPlan, ImagePreviewRaster, preview_reuse_eligible


class ImageRendererPressure(ValueError):
    """Measured GPU pressure; distinct from CPU/commit or source validation."""


class ImageBufferBudget:
    limit = 256 * 1024 * 1024
    # CPU control-plane/allocator allowance. The old mixed 96 MiB charge is
    # superseded: renderer high-water is now a separate, never-debited envelope.
    overhead = 16 * 1024 * 1024
    raster_reservations = 8

    def __init__(self):
        self._lock = RLock()
        self._leases = {}
        self._refs = {}
        self.peak = self.overhead
        self.control_admission = None

    @property
    def reserved(self):
        with self._lock:
            return self.overhead + sum(self._leases.values())

    def reserve(self, width, height, encoded_bytes=0):
        # Original reader raster, normalization/bytes, premultiplication, upload,
        # bounded RGBA8 QML layer and render scratch. Previous live leases count.
        amount = width * height * 4 * self.raster_reservations + encoded_bytes
        with self._lock:
            if width <= 0 or height <= 0 or self.reserved + amount > self.limit:
                raise ValueError('Image does not fit the current 256 MiB aggregate memory reservation. Resolution or simultaneous live frames exceed the safe envelope.')
            lease = object()
            self._leases[lease] = amount
            self._refs[lease] = 1
            self.peak = max(self.peak, self.reserved)
            return lease

    def release(self, lease):
        with self._lock:
            if self._refs.get(lease,0)>1:
                self._refs[lease]-=1
            else:
                self._leases.pop(lease,None);self._refs.pop(lease,None)

    def owns(self,lease):
        with self._lock:
            return lease in self._leases and self._refs.get(lease,0)>0

    def hold(self,lease):
        with self._lock:
            if lease not in self._leases:raise ValueError('Preview backing is already retired.')
            self._refs[lease]+=1

    def reserve_peak(self, amount, ceiling=None):
        with self._lock:
            if amount < 0 or self.reserved + amount > (self.limit if ceiling is None else ceiling):
                raise ValueError('This preview allocation exceeds admitted live CPU/commit headroom. The current picture is preserved.' if ceiling is not None else 'This preview tier exceeds the 256 MiB live resource envelope. The current picture is preserved.')
            lease = object()
            self._leases[lease] = amount
            self._refs[lease] = 1
            self.peak = max(self.peak, self.reserved)
            return lease

    def reserve_control(self,amount):
        # Receipt/hash/processing control data overlaps the actually live large
        # preview. It needs the same current host-derived CPU gate, not a stale
        # 256 MiB total ceiling. This never authorizes a decoder allocation.
        ceiling=self.control_admission(amount) if self.control_admission else None
        return self.reserve_peak(amount,ceiling)

    def retain(self, lease, amount):
        with self._lock:
            if lease not in self._leases or not 0 <= amount <= self._leases[lease]:
                raise ValueError('Invalid image resource ownership transition.')
            self._leases[lease] = amount


@dataclass(slots=True,weakref_slot=True)
class PreparedImage:
    metadata: MediaMetadataDto
    raster: ImageRaster | ImagePreviewRaster | None
    budget: ImageBufferBudget
    lease: object
    source_guard: object = None
    released: bool = False
    _pinned: bool = False

    def reusable_for(self, source):
        return (self._pinned and not self.released and self.source_guard is not None
                and callable(getattr(self.source_guard, "retain", None))
                and not getattr(self.source_guard, "closed", False)
                and self.budget.owns(self.lease) and isinstance(self.raster, ImagePreviewRaster)
                and self.raster.plan.full_source and self.raster.source == source
                and self.raster.straight is not None)

    def borrow(self):
        if self.released:raise ValueError('Preview backing is already retired.')
        retain=getattr(self.source_guard,'retain',None)
        if not callable(retain):return None
        self.budget.hold(self.lease)
        try:
            guard=retain()
            if guard is None:
                self.budget.release(self.lease)
                return None
            return PreparedImage(self.metadata,self.raster,self.budget,self.lease,guard,_pinned=True)
        except BaseException:
            self.budget.release(self.lease)
            raise

    def release(self):
        if self.released:return
        self.released=True
        try:
            self.close_source()
        finally:
            # Qt-owned frame copies remain in the whole renderer envelope.
            # Only this application's distinct backing reference is dropped.
            self.raster = None
            self.budget.release(self.lease)

    def close_source(self):
        guard, self.source_guard = self.source_guard, None
        if guard is not None:
            guard.__exit__(None, None, None)


@dataclass(slots=True)
class PreparedManifest:
    manifest: object
    budget: ImageBufferBudget
    lease: object

    def release(self):
        # Metadata is exclusively Python-owned (no Qt/native borrowed backing).
        # The finalizer releases its charge only after all Tray/request/worker
        # references to the immutable manifest are gone, not on removal alone.
        self.manifest = None


class ImageSourceService:
    def discover_exported_sequence(self,result,cancel):
        admission=self._prepare_result(result,cancel)
        try:return self._discover_exported_sequence(result,cancel)
        finally:admission.close()

    def _prepare_result(self,result,cancel):
        if self.result_preparer is None:raise ValueError('Trusted completed-result preparation is unavailable.')
        return self.result_preparer.prepare_result(result,cancel)

    def _discover_exported_sequence(self,result,cancel):
        from mediaforge.application.services.exported_result import result_path
        path=result_path(result);identity=path.stat();layout=result.numbering
        if layout is None or not 0<layout.count<=10000:
            raise ValueError('Exported sequence has no bounded frozen numbering plan.')
        prepared=self.discover_sequence(str(path/layout.name(0)),cancel)
        try:
            manifest=prepared.manifest
            if (manifest.count!=layout.count or manifest.numbers!=tuple(range(layout.start,layout.start+layout.count))
                    or any(Path(m.path).name!=layout.name(i) or (m.facts.width,m.facts.height,m.facts.format)!=(result.width,result.height,result.format)
                           for i,m in enumerate(manifest.members))):
                raise ValueError('Exported sequence no longer matches its validated numbering/format/geometry.')
            # Discovery has a bounded scan and validates every header/signature.
            # Also reject unrelated added members in this new result directory.
            import os
            with os.scandir(path) as entries:
                count=0
                expected={layout.name(i) for i in range(layout.count)}
                for entry in entries:
                    count+=1
                    if cancel.is_set() or count>layout.count or entry.name not in expected:
                        raise ValueError('Exported sequence directory changed or preparation was cancelled.')
            after=result_path(result).stat()
            if count!=layout.count or (after.st_dev,after.st_ino)!=(identity.st_dev,identity.st_ino):
                raise ValueError('Exported sequence directory identity changed.')
            return prepared
        except BaseException:
            prepared.release();raise

    def prepare_exported_preview(self,result,quality,cancel,viewport):
        admission=self._prepare_result(result,cancel)
        try:
            from mediaforge.application.services.exported_result import GuardedSource
            value=self._prepare_exported_preview(result,quality,cancel,viewport)
            value.source_guard=GuardedSource(value.source_guard,admission)
            return value
        except BaseException:admission.close();raise

    def _prepare_exported_preview(self,result,quality,cancel,viewport):
        from mediaforge.application.services.exported_result import result_path
        path=result_path(result);source=self.reader.probe(str(path));facts=source.facts
        if (facts.width,facts.height,facts.format)!=(result.width,result.height,result.format):
            raise ValueError('Exported image no longer matches its validated format/geometry.')
        return self.prepare_preview(str(path),quality,cancel,source,viewport)

    def prepare_exported_sequence_preview(self,result,source,slot,quality,cancel,viewport):
        admission=self._prepare_result(result,cancel)
        try:
            from mediaforge.application.services.exported_result import GuardedSource
            value=self.prepare_sequence_preview(source.manifest.members[slot].path,quality,cancel,
                source.manifest.members[slot],viewport,source.manifest,True)
            value.source_guard=GuardedSource(value.source_guard,admission)
            return value
        except BaseException:admission.close();raise

    def prepare_sequence_preview(self, path, quality, cancel, expected, viewport, manifest, validate,**options):
        if validate: self.reader.validate_sequence(manifest,cancel)
        # Every requested member is independently byte-hash checked and locked
        # by the same A path, even between consecutive sequence frames.
        return self.prepare_preview(path,quality,cancel,expected,viewport,**options)

    def discover_sequence(self, path, cancel, *, automatic=False):
        # Admit directory/name/signature metadata before the bounded discovery.
        # 20k names + 10k immutable identities; no decoded frame collection.
        sample=self.admit_cpu_processing(incoming_cpu=48*1024*1024) if self.resources is not None else None
        ceiling=self.preview_capacity(sample)
        lease = self.budget.reserve_peak(48*1024*1024,ceiling)
        self.preview_cpu_high_water=max(self.preview_cpu_high_water,self.budget.reserved)
        try:
            manifest = self.reader.discover_sequence(path, cancel, automatic=automatic)
            self.budget.retain(lease,4096*manifest.count)
            weakref.finalize(manifest,self.budget.release,lease)
            return PreparedManifest(manifest,self.budget,lease)
        except BaseException:
            self.budget.release(lease)
            raise

    def __init__(self, reader: ImageReader, budget=None, resources=None,result_preparer=None):
        self.reader = reader
        self.budget = budget or ImageBufferBudget()
        self.resources = resources
        if resources is not None:
            target=weakref.WeakMethod(self._control_capacity)
            def control_capacity(amount):
                callback=target()
                if callback is None:raise ValueError('Preview resource owner is unavailable.')
                return callback(amount)
            self.budget.control_admission=control_capacity
        else:self.budget.control_admission=None
        self.result_preparer=result_preparer
        self._cache = OrderedDict()
        self.cache_limit = 96*1024*1024
        self.cache_bytes = self.cache_hits = self.cache_misses = self.cache_evictions = 0
        self.channel = 'RGBA'
        self.render_reservation = 96*1024*1024
        self._surface_pixels=1920*1080
        self._preview_pixels=1920*1080
        self.preview_cpu_high_water=self.budget.limit
        self.texture_limit=2048  # Set only by a public, render-thread QRhi capability observation.
        self._video_bytes=0
        self.video_cpu_reservation=0
        self.render_surface(1,1)

    def _control_capacity(self,amount):
        return self.preview_capacity(self.admit_cpu_processing(incoming_cpu=amount))

    def _render_bound(self,preview_pixels=None):
        return ((32*1024**2+8*4*self._surface_pixels+
                 8*max(4*max(self._preview_pixels,preview_pixels or 0),self._video_bytes)+1024**2-1)//1024**2)*1024**2

    def render_surface(self,width,height):
        pixels=max(1,int(width))*max(1,int(height))
        if pixels > 3840*2160 or max(width,height)>7680:
            raise ValueError('This display surface exceeds the tested 4K desktop envelope.')
        self._surface_pixels=max(self._surface_pixels,pixels)
        # High-water only. No timer, frame swap or Python reference debits this.
        self.render_reservation=max(self.render_reservation,
            self._render_bound())

    def evict_preview(self):
        if self._cache:
            _,(_,lease,size)=self._cache.popitem(last=False)
            self.cache_bytes-=size;self.budget.release(lease);self.cache_evictions+=1
            return True
        return False

    def video_texture(self,width,height,depth=8):
        # The same persistent renderer also retains video allocations. Charge
        # source-resolution video textures even after returning to image mode.
        if width*height>3840*2160:return
        # 10/12-bit YUV444 can require three 16-bit planes, unlike RGBA8.
        # Eight bytes/pixel covers those planes plus alignment conservatively.
        self._video_bytes=max(self._video_bytes,width*height*(8 if depth>8 else 4))
        # Qt/FFmpeg frame-threaded decoding is outside the image CPU lease.
        # Keep a session-wide envelope for two decoder generations plus display/
        # pending buffers; never debit it on player disposal or frame swaps.
        # The initial profile is bounded to <=16 logical decoder threads/UHD.
        frames=2*min(16,os.cpu_count() or 1)+4
        self.video_cpu_reservation=max(self.video_cpu_reservation,frames*self._video_bytes)
        self.render_surface(1,1)

    def close_cache(self):
        while self.evict_preview():pass

    @staticmethod
    def raster_bytes(raster):
        return raster.plan.width*raster.plan.height*4 + (raster.source.facts.width*raster.source.facts.height*raster.source.facts.decoded_bytes_per_pixel if raster.straight is not None else 0)

    def check_pressure(self, incremental_gpu=0, incoming_cpu=0):
        if self.resources is None:
            raise ValueError('Image pressure monitoring is unavailable.')
        sample = self.resources.sample()
        self._check_sample(sample,incremental_gpu,incoming_cpu)
        return sample

    def recheck_renderer_admission(self, refusal):
        """Re-test the same rejected texture charge after residency changes."""
        sample = self.resources.sample()
        self._check_sample(sample, *refusal.admission)
        return sample

    def admit_cpu_processing(self, incoming_cpu=0):
        """Host admission for software workers that do not publish Qt textures."""
        if self.resources is None:
            raise ValueError('Windows CPU processing resource monitoring is unavailable.')
        sample = self.resources.sample_host()
        self._check_host(sample, incoming_cpu, self.render_reservation)
        return sample

    def admit_video_resume(self):
        """Re-admit a retired source's already scored parent working set."""
        sample=self.check_pressure()
        free=min(sample['available_physical']-max(2*1024**3,int(sample['total_physical']*.15)),
                 sample['available_commit']-max(2*1024**3,int(sample['total_commit']*.10)))
        # Same R/V/render future-parent score as processing admission. Resident
        # process growth is already reflected in availability: do not charge it
        # again, or silently reopen a multi-frame decoder after RAM disappeared.
        future=max(0,self.resident_reservation+self.video_cpu_reservation+
                   self.render_reservation-sample['image_private_growth'])
        if future>free:raise ValueError('Insufficient RAM/commit headroom to reload video. Select it again after resource pressure falls.')
        return sample

    def _check_sample(self,sample,incremental_gpu=0,incoming_cpu=0,render_limit=None):
        render_limit=self.render_reservation if render_limit is None else render_limit
        self._check_host(sample, incoming_cpu, render_limit)
        if sample['dedicated']+sample['shared']+incremental_gpu > render_limit:
            error = ImageRendererPressure('Qt renderer pressure exceeds the qualified image envelope.')
            error.sample = sample
            error.admission = (incremental_gpu, incoming_cpu, render_limit)
            raise error

    def _check_host(self, sample, incoming_cpu, render_limit):
        head_phys = max(2*1024**3, int(sample['total_physical']*.15))
        head_commit = max(2*1024**3, int(sample['total_commit']*.10))
        if (sample['available_physical']-head_phys < self.budget.limit
                or sample['available_commit']-head_commit < self.budget.limit):
            raise ValueError('Insufficient Windows RAM/commit headroom. The current picture is preserved.')
        if sample['image_private_growth']+incoming_cpu > self.preview_capacity(sample)+self.video_cpu_reservation+render_limit:
            # Observed process CPU growth shares the same current host-derived ceiling
            # as lease admission; a historical 256 MiB image baseline is not a
            # bound for loaded codec libraries or large-source staging.
            # A conservative admission score, not a physical-memory total.
            # Shared GPU backing may alias CPU memory and is not added again.
            raise ValueError('Windows process memory pressure exceeds the qualified host envelope. The current picture is preserved.')

    def preview_plan(self, source, quality, viewport=(1920,1080), pressure=None,channel=None,view=None,reuse=None):
        if quality not in ('Auto','1:1','1:2','1:4'):
            raise ValueError('Choose Auto, 1:1, 1:2 or 1:4 preview quality.')
        f = source.facts
        channel=self.channel if channel is None else channel
        divisors = (1,2,4) if quality == 'Auto' else (int(quality[-1]),)
        reasons = []
        renderer_failures = 0
        renderer_refusal = None
        plans = []
        for divisor in divisors:
            width, height = (f.width+divisor-1)//divisor, (f.height+divisor-1)//divisor
            # GPU residency is a byte-bounded presentation working set, not a
            # source-resolution limit. Oversized rasters keep full source data on
            # the CPU and upload only the visible region / viewport sampling.
            region=None;full_source=False
            if width*height*4 > 32*1024**2 or max(width,height)>self.texture_limit:
                full_source=True
                if view is None:
                    x,y,rw,rh=0,0,f.width,f.height
                    scale=min(1/divisor,viewport[0]/rw,viewport[1]/rh)
                else:
                    x,y,rw,rh,ow,oh=view
                    x=max(0,min(f.width-1,int(x)));y=max(0,min(f.height-1,int(y)))
                    rw=max(1,min(f.width-x,int(rw)));rh=max(1,min(f.height-y,int(rh)))
                    scale=min(1/divisor,ow/rw,oh/rh)
                region=(x,y,rw,rh)
                width=max(1,round(rw*scale));height=max(1,round(rh*scale))
            if (width*height*4 > 32*1024**2 or max(width,height)>self.texture_limit):
                reasons.append('Visible preview upload exceeds the byte budget or actual Qt texture capability. Reduce viewport zoom/size.')
                continue
            p = width*height*4
            # Progressive JPEG can retain full-source coefficient buffers.
            decode = p if not full_source and f.format == 'jpeg' and f.variant == 'sequential' else f.width*f.height*f.decoded_bytes_per_pixel
            peak = 4*decode+2*p+source.signature.size
            cached=None if full_source else self._cache.get((source,divisor))
            reuse_source=full_source and preview_reuse_eligible(reuse,source)
            if reuse_source:
                # The old owner and this incoming lease both charge shared source
                # backing conservatively, until publication releases the old owner.
                # No decoder or encoded-file buffer on a pinned immutable hit.
                # Charge source ownership plus crop/premultiply/scale overlap.
                region_bytes=region[2]*region[3]*f.decoded_bytes_per_pixel
                peak=decode+3*region_bytes+4*p+1024**2
            if cached is not None:
                # Cache reuse needs no decoder/coefficient reservation. Charge
                # the real retained backing and native channel-conversion peak.
                scratch=2*f.width*f.height*f.decoded_bytes_per_pixel if channel!='RGBA' and cached[0].straight is not None else 0
                peak=self.raster_bytes(cached[0])+2*p+scratch
            if f.color_class=='BT.709':
                # Explicit display transfer uses bounded RGBA64/LUT scratch.
                peak+=16*(region[2]*region[3] if region else f.width*f.height)+4*1024**2
            ceiling=self.preview_capacity(pressure)
            if self.budget.reserved+peak > ceiling:
                reasons.append('This format/tier decode peak exceeds safely available CPU/commit headroom.')
                continue
            if pressure is not None:
                try:self._check_sample(pressure,2*p,peak,self._render_bound(width*height))
                except ValueError as error:
                    if isinstance(error,ImageRendererPressure):
                        renderer_failures+=1
                        if (renderer_refusal is None or error.admission[2] - error.admission[0]
                                > renderer_refusal.admission[2] - renderer_refusal.admission[0]):
                            renderer_refusal = error
                    reasons.append(str(error));continue
            plans.append(ImagePreviewPlan(divisor,width,height,peak,f'1:{divisor}',region,full_source,reuse_source))
        if not plans:
            message = 'No safe image preview tier is available. ' + ' '.join(dict.fromkeys(reasons))
            if renderer_failures and renderer_failures == len(reasons):
                renderer_refusal.args = (message,)
                raise renderer_refusal
            raise ValueError(message)
        if quality == 'Auto':
            adequate = [p for p in plans if p.width >= viewport[0] or p.height >= viewport[1]]
            return adequate[-1] if adequate else plans[0]
        return plans[0]

    @property
    def resident_reservation(self):
        return max(self.budget.limit,self.preview_cpu_high_water,self.budget.reserved)

    def preview_capacity(self,sample):
        if sample is None:return self.budget.limit
        free=min(sample['available_physical']-max(2*1024**3,int(sample['total_physical']*.15)),
                 sample['available_commit']-max(2*1024**3,int(sample['total_commit']*.10)))
        # No resolution tiers: one owned preparation may use at most a quarter
        # of current safe headroom, with existing live owners counted in it.
        return max(0,free//4)

    def prepare_preview(self, path, quality, cancel, expected=None, viewport=(1920,1080),view=None,reuse=None):
        from contextlib import nullcontext
        with getattr(self.reader,'cancellation',lambda _:nullcontext())(cancel):
            return self._prepare_preview(path,quality,cancel,expected,viewport,view,reuse)

    def _prepare_preview(self,path,quality,cancel,expected,viewport,view,reuse):
        channel=self.channel
        if cancel.is_set():
            raise ValueError('Image preparation canceled.')
        # A cached immutable identity already has validated header facts. Reuse
        # them only if the complete identity matches; fresh locked byte hashing
        # below still precedes cache publication (also catches restored mtimes).
        known=expected is not None and str(Path(path))==expected.path and any(key[0]==expected for key in self._cache)
        pinned=expected is not None and preview_reuse_eligible(reuse,expected)
        source = expected if known or pinned else self.reader.probe(path)
        if expected is not None and source != expected:
            raise ValueError('Image source changed. Explicit reimport is required.')
        # Sample before choosing a tier. Auto may use a smaller qualified plan;
        # explicit requests still have one candidate and can never downgrade.
        pressure=self.check_pressure()
        while True:
            try:
                plan = self.preview_plan(source,quality,viewport,pressure,channel,view,reuse)
                break
            except ValueError:
                if not self.evict_preview():raise
        key=(source,plan.divisor)
        self._preview_pixels=max(self._preview_pixels,plan.width*plan.height)
        self.render_surface(1,1)
        ceiling=self.preview_capacity(pressure)
        self.preview_cpu_high_water=max(self.preview_cpu_high_water,self.budget.reserved+plan.peak_bytes)
        cached=None if plan.full_source else self._cache.get(key)
        if cached is not None:
            # probe above authenticates bytes, but publication also needs the
            # immutable source lock to span cache handoff, including a second
            # signature check after reacquisition (no path/handle race).
            guard=self.reader.preview_guard(source,cancel)
            raster=replace(cached[0],plan=plan,decode_ms=0,normalize_ms=0,cache_hit=True);self._cache.move_to_end(key);self.cache_hits+=1
            lease=None
            try:
                lease=self.budget.reserve_peak(plan.peak_bytes,ceiling)
                raster=self.reader.channel_preview(raster,channel,cancel)
                self.budget.retain(lease,self.raster_bytes(raster))
                return PreparedImage(MediaMetadataDto(Path(path).name,source.path,source.facts.format,
                    None,'',source.facts.width,source.facts.height,None,source=source),raster,self.budget,lease,guard)
            except BaseException:
                guard.__exit__(None,None,None)
                if lease is not None:self.budget.release(lease)
                raise
        self.cache_misses+=1
        self.check_pressure(2*plan.width*plan.height*4,plan.peak_bytes)
        lease = self.budget.reserve_peak(plan.peak_bytes,ceiling)
        prepared = None
        guard = None
        try:
            raster, guard = (self.reader.decode_preview(source,plan,cancel,reuse=reuse)
                             if plan.full_source else self.reader.decode_preview(source,plan,cancel))
            base=raster
            size=self.raster_bytes(base)
            raster=self.reader.channel_preview(raster,channel,cancel)
            prepared = PreparedImage(MediaMetadataDto(Path(path).name,source.path,source.facts.format,
                None,'',source.facts.width,source.facts.height,None,source=source),raster,self.budget,lease,guard)
            if cancel.is_set():
                raise ValueError('Image preparation canceled.')
            self.check_pressure()
            self.budget.retain(lease,self.raster_bytes(raster))
            # Decoder scratch has retired. Cache references share the immutable
            # Qt backing; charge them conservatively without reserving a second
            # decoder peak. Large full-source stages remain current/request-only.
            while self.cache_bytes+size>self.cache_limit and self.evict_preview():pass
            if not plan.full_source and size<=self.cache_limit and self.budget.reserved+size<=self.budget.limit:
                cache_lease=self.budget.reserve_peak(size)
                self._cache[key]=(base,cache_lease,size);self.cache_bytes+=size
            return prepared
        except BaseException:
            if prepared is not None: prepared.release()
            else:
                try:
                    if guard is not None: guard.__exit__(None,None,None)
                finally: self.budget.release(lease)
            raise

    def is_image(self, path):
        return self.reader.is_image(path)

    def prepare(self, path: str, expected: StillSource | None = None) -> PreparedImage:
        source = self.reader.probe(path)
        if expected is not None and source != expected:
            raise ValueError('Image source changed. Remove it and explicitly reimport.')
        f = source.facts
        lease = self.budget.reserve(f.width, f.height, source.signature.size)
        try:
            raster = self.reader.decode(source)
            if len(raster.rgba) != f.width * f.height * 4:
                raise ValueError('Invalid decoded RGBA raster.')
            return PreparedImage(MediaMetadataDto(Path(path).name, source.path, f.format,
                None, '', f.width, f.height, None, source=source), raster, self.budget, lease)
        except BaseException:
            self.budget.release(lease)
            raise
