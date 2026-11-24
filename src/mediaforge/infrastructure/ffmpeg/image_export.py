"""Source-based image routes inside the existing owned Export executor."""
from contextlib import ExitStack,nullcontext
from fractions import Fraction
from itertools import zip_longest
import hashlib,json,re
import sys,sysconfig
from pathlib import Path
from mediaforge.application.contracts.image_source import VideoSource,StillSource,SequenceSource
from mediaforge.application.contracts.source_timeline import SourceSignature
from mediaforge.features.media.export import ExportError,ExportResult,validate_video_encoder
from mediaforge.infrastructure.filesystem.image_output import ImageOutput,ReadLocks
from mediaforge.infrastructure.ffmpeg.resize import reformat_filters
from mediaforge.application.contracts.image_export import jpeg_qscale
from mediaforge.infrastructure.filesystem import image_capacity
from mediaforge.infrastructure.filesystem.profile import portable_runtime


def _codec_command(plan):
    python=Path(sys._base_executable)
    worker=Path(__file__).parents[1]/'images/encode_worker.py'
    portable=getattr(sys,'mediaforge_portable_executable',None)
    if portable is None:
        site=sysconfig.get_paths()['purelib']
    else:
        guard=getattr(sys,'mediaforge_core_guard',None)
        if (guard is None or guard.root!=portable_runtime(Path(portable)) or python!=guard.root/'Core/Python/python.exe'
                or python not in guard.members or worker not in guard.members):
            raise ExportError('runtime_integrity','The portable image codec runtime could not be authenticated.')
        site=guard.root/'Core/Site'
    return [str(python),'-I','-B',str(worker),str(site),str(plan)]


def _sha(path,control):
    digest=hashlib.sha256()
    with path.open('rb') as file:
        while chunk:=file.read(1024*1024):control.check();digest.update(chunk)
    return digest.hexdigest()


def _hash_rows(file):
    while line := file.readline(512):
        if not line.endswith(b'\n'):
            raise ExportError('validation_failed','Malformed frame hash ledger.')
        if not line.startswith(b'#'):
            digest=line.rsplit(b',',1)[-1].strip()
            if not re.fullmatch(rb'[0-9a-f]{64}',digest):
                raise ExportError('validation_failed','Invalid frame digest.')
            yield digest


def _compare_hash_ledgers(left,right,count,control):
    if any(path.stat().st_size>4096+count*256 for path in (left,right)):
        raise ExportError('validation_failed','Frame hash ledger exceeds its planned byte budget.')
    seen=0
    with left.open('rb') as original,right.open('rb') as intermediate:
        for a,b in zip_longest(_hash_rows(original),_hash_rows(intermediate)):
            control.check();seen+=1
            if seen>count or a!=b:
                raise ExportError('pixel_validation_failed','Codec input does not match authoritative source processing.')
    if seen!=count:
        raise ExportError('pixel_validation_failed','Codec input does not contain every planned picture.')


def _check_source_clock(path, request, control):
    clock = None
    seen = 0
    with path.open('r', encoding='ascii') as stream:
        for row in stream:
            if row.startswith('#tb 0:'):
                clock = Fraction(row.split(':', 1)[1].strip())
            if row.startswith('#') or not row.strip():
                continue
            control.check()
            if clock is None or seen >= request.count:
                raise ExportError('frame_identity_failed', 'Incomplete or extra original picture timing.')
            pts = int(row.split(',', 4)[2]) * clock * 1_000_000
            if abs(pts - request.snapshot.timeline.timestamps[request.start + seen]) > 1:
                raise ExportError('frame_identity_failed', 'Decoded picture timing differs from the captured source interval.')
            seen += 1
    if seen != request.count:
        raise ExportError('frame_identity_failed', 'The decoder did not supply every selected source picture.')


def _orientation(value):
    return {1:'',2:'hflip',3:'hflip,vflip',4:'vflip',5:'transpose=1,hflip',6:'transpose=1',7:'transpose=1,vflip',8:'transpose=2'}[value]


def _spatial_transform(plan):
    return plan is not None and not (
        (plan.input_width,plan.input_height)==(plan.output.width,plan.output.height)
        and plan.picture_width==plan.input_width and plan.picture_height==plan.input_height
        and plan.x==0 and plan.y==0)


def _future_parent(service,sample):
    # Already resident parent commit is reflected in host availability. Charge
    # only the remaining conservative R + Qt-video + renderer growth score.
    return max(0,getattr(service,'resident_reservation',service.budget.limit)+service.video_cpu_reservation+
               service.render_reservation-sample['image_private_growth'])


def processing_peak(r):
    """Sequential original-media CPU plan, separate from resident Viewer leases.

    Native typed input owns encoded bytes, decoded/oriented samples, conversion
    scratch, writer bytes and independent decoded verification simultaneously.
    The float area integrator additionally owns float64 prefix/edge arrays.
    This is an allocation upper score, not a measured RSS or GPU allocation.
    """
    source=r.source_identity
    members=source.manifest.members if isinstance(source,SequenceSource) else (source,) if isinstance(source,StillSource) else ()
    s=r.source_width*r.source_height;d=r.width*r.height
    peak=64*1024**2+32*max(s,d)+r.count*1024
    typed=bool(members and members[0].facts.depth>8)
    transfer=bool(members and members[0].facts.color_class=='BT.709') or isinstance(source,VideoSource) and r.mode!='media'
    if typed or transfer or r.image_depth in ('16','float'):
        f=members[0].facts if members else None
        alpha=bool(f and f.alpha) or isinstance(source,VideoSource)
        padding=bool(r.reformat and r.reformat.placement=='fit' and
                     (r.reformat.picture_width<r.width or r.reformat.picture_height<r.height))
        alpha_transform=bool((alpha or padding) and not r.matte and _spatial_transform(r.reformat))
        # FFmpeg and the native codec run sequentially, never sharing live
        # rasters. Native originals retain full decode backing through a crop;
        # 8-bit/video inputs instead hand over a validated, already cropped PNG.
        h,w=((r.crop.height,r.crop.width) if typed and r.crop else (r.source_height,r.source_width)) if typed else (
            (r.reformat.input_height,r.reformat.input_width) if alpha_transform else (r.height,r.width))
        work=w*h
        b=f.decoded_bytes_per_pixel if typed else 8
        full=s if typed else work
        selected=members[r.start:r.end] if isinstance(source,SequenceSource) else members
        encoded=max(m.signature.size for m in selected) if typed else 8*work+1024**2
        out_b=16 if r.image_depth=='float' else 8 if r.image_depth=='16' or r.mode=='media' else 4
        # Source, orientation/normalization, retained decoded validation; three
        # destination arrays plus encoded writer/reader bytes and conversion.
        live=3*b*full+3*out_b*d+2*(encoded+out_b*d+1024**2)
        if r.image_depth!=('float' if f and f.sample_type=='float' else str(f.depth) if f else '16'):
            live+=24*max(work,d)  # destination conversion, after source Crop
        if r.matte and typed:live+=40*work
        if transfer:live+=96*max(work,d)
        if _spatial_transform(r.reformat) and (typed or alpha_transform):
            live+=32*work+64*h*(r.width+1)+80*d
        decode=4*b*full+encoded
        peak=max(peak,64*1024**2+(max(live,decode)*5+3)//4)
    elif _spatial_transform(r.reformat) and not r.matte:
        h,w=r.reformat.input_height,r.reformat.input_width
        peak=max(peak,64*1024**2+max(16*w*h+24*h*(r.width+1)+32*d,8*w*h+8*h*r.width+80*d))
    if r.mode=='media' and r.codec=='h264':
        # x264's preset queues outlive a single decode/filter iteration. A short
        # five-picture test cannot establish the peak of a 100-picture movie.
        # Pinned veryfast/medium/slow: lookahead 10/40/50, refs 1/3/5, B=3.
        # Four extra slots cover current/recon/thread/control overlap. A 4-byte
        # aligned full raster per slot includes YUV plus lowres/motion storage;
        # use phase maxima so retired typed-codec arrays aren't added twice.
        lookahead,refs={'fast':(10,1),'balanced':(40,3),'quality':(50,5)}[r.speed]
        queued=min(r.count,lookahead+refs+3+4)
        aligned=((r.width+63)//64)*64*((r.height+63)//64)*64
        encoded=max((m.signature.size for m in members[r.start:r.end]),default=0)
        movie_live=32*max(s,d)+4*aligned*queued+encoded
        peak=max(peak,64*1024**2+(movie_live*5+3)//4+r.count*1024)
    return peak


def job_requirement(r):
    return max(512*1024**2,((processing_peak(r)+4*1024**2-1)//(4*1024**2))*4*1024**2)


def host_job_capacity(service,sample,control_bytes):
    available=min(sample['available_physical']-max(2*1024**3,int(sample['total_physical']*.15)),
                  sample['available_commit']-max(2*1024**3,int(sample['total_commit']*.10)))
    return max(0,available-_future_parent(service,sample)-control_bytes)//4


def execute(executor,r,c):
    c.check()
    if r.mode=='media':
        validate_video_encoder(r.codec,r.prores_quality if r.codec=='prores' else r.quality,r.speed,r.h264_profile)
    from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
    from mediaforge.infrastructure.ffmpeg.child_process import ChildProcesses
    from mediaforge.features.media.export import ExportError
    peak=processing_peak(r)
    required=job_requirement(r)
    def admit():
        s=executor._image_service.admit_cpu_processing(incoming_cpu=r.control_bytes)
        host=host_job_capacity(executor._image_service,s,r.control_bytes)
        if host<required:raise ExportError('insufficient_memory','Insufficient host headroom for the complete original-sample processing plan. Nothing was published.')
        return required
    jobs=PreviewJobs(required,admission=admit)
    children=ChildProcesses(jobs,lambda code:ExportError(code,'Image codec processing failed; no unvalidated result is published.'),executor._logger)
    try:return _execute(executor,r,c,children)
    finally:
        try:
            children.check_reaped();jobs.close()
            executor.last_image_metrics=dict(planned_peak=peak,peak_job=jobs.peak,limits=jobs.applied,children=len(jobs.processes),reaped=True)
            executor._logger.info('Image export retired metrics=%s published=%s', executor.last_image_metrics, c.published)
        except BaseException:
            c.retain_resources((jobs,children));raise


def _execute(executor,r,c,processes):
    e=executor;c.check();e._processes.check_reaped()
    if e._runtime is None:raise ExportError('runtime_unavailable','Authenticated managed FFmpeg is unavailable.')
    if e._image_service is None:raise ExportError('resource_unavailable','Image conversion resource admission is unavailable.')
    image_capacity.check(r)
    source=r.source_identity
    budget=e._image_service.budget
    sample = e._image_service.admit_cpu_processing(incoming_cpu=r.control_bytes)
    e._logger.info('Image export software resource preflight sample=%s', sample)
    if host_job_capacity(e._image_service,sample,r.control_bytes)<job_requirement(r):
        raise ExportError('insufficient_memory','The complete processing plan exceeds current safely admitted host headroom. Nothing was decoded or published.')
    # R is Resident Preview/UI. The owned FFmpeg processing child is a separate
    # transient, as with accepted video Export, not an interactive Route-M preview.
    # Parent hash/telemetry/control-plane buffers remain charged to R.
    lease=budget.reserve_control(r.control_bytes)
    retained=False
    def run(command,*args,**kwargs):
        # Recheck current host pressure immediately before each owned
        # child, including validation. No stale preflight sample admits a spawn.
        image_capacity.check(r,kwargs.get('output'))
        current=e._image_service.admit_cpu_processing()
        if host_job_capacity(e._image_service,current,r.control_bytes)<job_requirement(r):
            raise ExportError('insufficient_memory','Host headroom changed before conversion/validation. Nothing was published.')
        if r.mode=='sequence':kwargs['monitor']=lambda:image_capacity.check_floor(r)
        return processes.run(command,*args,**kwargs)
    try:
        with getattr(e._image_service.reader,'cancellation',lambda _:nullcontext())(c.check), ReadLocks() as sources:
            members=(source.manifest.members if isinstance(source,SequenceSource) else (source,) if isinstance(source,StillSource) else ())
            paths=tuple(Path(m.path) for m in members) if members else (r.source,)
            selected_paths=paths[r.start:r.end] if isinstance(source,SequenceSource) else paths
            for path in paths:
                c.check();sources.parents(path.parent);sources.add(path)
            hashes={str(path):_sha(path,c) for path in paths}
            for member in members:
                if e._image_service.reader.probe(member.path)!=member:raise ExportError('source_changed','Image source changed since import. Reimport it.')
            c.report('preflight','Validating original sources and conversion plan…')
            video=None
            if isinstance(source,VideoSource):
                if r.snapshot is None or SourceSignature.capture(r.source)!=r.snapshot.signature:raise ExportError('source_changed','Video timeline source changed. Reopen the video.')
                payload,video,_=e._probe(r.source,c,processes=processes)
                if not set(payload.get('format',{}).get('format_name','').split(',')).intersection({'mov','mp4','matroska'}):
                    raise ExportError('unsupported_container','Image extraction accepts MP4/MOV/MKV input only.')
                from mediaforge.infrastructure.ffmpeg.resize import FfmpegResizeExecutor
                FfmpegResizeExecutor._orientation(video)
                if video.get('field_order','progressive') not in ('progressive','unknown') or video.get('color_transfer') in ('smpte2084','arib-std-b67'):
                    raise ExportError('unsupported_color','Image extraction currently qualifies progressive SDR sources only.')
                if video.get('color_primaries') not in (None,'unknown','unspecified','bt709','bt470bg','smpte170m'):
                    raise ExportError('unsupported_color','This video color-primary class is not qualified for ordinary image extraction.')
                if video.get('pix_fmt') not in ('yuv420p','yuvj420p','yuv422p','yuvj422p','yuv444p','yuvj444p','rgb24','rgba','bgra','gbrp','yuv422p10le','yuv444p10le','yuv444p12le'):
                    raise ExportError('unsupported_depth','This video pixel/depth class is not qualified for 8-bit image extraction.')
            alpha=members[0].facts.alpha if members else video.get('pix_fmt') in ('rgba','bgra')
            padding=bool(r.reformat and r.reformat.placement=='fit' and (r.reformat.picture_width<r.width or r.reformat.picture_height<r.height))
            output_alpha=(alpha or padding) and not r.matte
            if (alpha or padding) and (r.container in ('jpeg','mp4','mov')) and not r.matte:raise ExportError('matte_required','Choose a matte for transparency or Fit padding.')
            alpha_transform=bool(output_alpha and _spatial_transform(r.reformat))
            with ImageOutput(r,e._logger) as output:
                base=[str(e._runtime.ffmpeg),'-nostdin','-hide_banner','-v','error','-xerror','-n','-threads','1','-filter_threads','1','-filter_complex_threads','1','-copyts','-noautorotate']
                if isinstance(source,SequenceSource):
                    manifest=output.auxiliary_path('input.ffconcat')
                    lines=['ffconcat version 1.0']
                    for path in selected_paths:
                        quoted=str(path).replace('\\','/').replace("'","'\\''")
                        lines.append("file '"+quoted+"'")
                        if r.rate:lines.append(f'option framerate {r.rate.numerator}/{r.rate.denominator}')
                    manifest.write_text('\n'.join(lines)+'\n',encoding='utf-8')
                    inputs=['-f','concat','-safe','0','-i',str(manifest)]
                else:inputs=['-i',str(r.source)]
                filters=[]
                windowed = (video is not None and video['codec_name'] == 'h264'
                            and 'mp4' in payload['format']['format_name'].split(',')
                            and video.get('start_pts') == 0
                            and r.snapshot.timeline.timestamps[0] == 0
                            and r.snapshot.timeline.timestamps[r.start] >= 5_000_000)
                if windowed:
                    first, last = (r.snapshot.timeline.timestamps[i] for i in (r.start, r.end - 1))
                    seek = first - 2_000_000
                    inputs = ['-ss', f'{seek // 1_000_000}.{seek % 1_000_000:06d}', *inputs]
                    filters.extend(['settb=1/1000000', f'select=between(pts\\,{first-1}\\,{last+1})'])
                elif isinstance(source,VideoSource):
                    filters.append(f'select=between(n\\,{r.start}\\,{r.end-1})')
                elif members[0].facts.orientation!=1:filters.append(_orientation(members[0].facts.orientation))
                # map_metadata does not strip frame-side EXIF. After applying
                # orientation exactly once, remove it so PNG/TIFF reimport cannot
                # rotate an already oriented picture a second time.
                filters.append('sidedata=mode=delete')
                if r.matte:
                    filters.append(f'format=rgba,split[fg][bg];[bg]drawbox=c={r.matte}@1:t=fill:replace=1[solid];[solid][fg]overlay=shortest=1:format=auto,format=rgb24')
                if r.crop:
                    t=r.crop;filters.append(f'crop={t.width}:{t.height}:{t.x}:{t.y}:exact=1')
                if _spatial_transform(r.reformat) and not alpha_transform:
                    transform=reformat_filters(r.reformat,'rgba','rgba')
                    transform=transform.replace(':color=black',':color='+r.matte if r.matte else ':color=black@0')
                    filters.append(transform)
                filters.append(f'setsar={r.sar.numerator}/{r.sar.denominator}:max=65535')
                movie_transfer=('bt709' if members and members[0].facts.color_class=='BT.709'
                                and members[0].facts.depth==8 else 'iec61966-2-1')
                movie_color=('scale=out_color_matrix=bt709:out_range=tv,'
                             'setparams=range=limited:color_primaries=bt709:color_trc='
                             +movie_transfer+':colorspace=bt709')
                if r.mode=='media':filters.append(movie_color)
                picture_filters=','.join(filters)
                movie=r.mode=='media'
                if isinstance(source,SequenceSource):picture_filters+=f',settb=expr=1/{r.rate.numerator},setpts=N*{r.rate.denominator}'
                common=base+inputs+['-map','0:v:0','-an','-sn','-dn','-vf',picture_filters,'-frames:v',str(r.count),'-enc_time_base:v','filter','-fps_mode:v','passthrough','-map_metadata','-1','-threads:v','1']
                if movie:
                    codec=['-c:v','libx264','-preset',{'fast':'veryfast','balanced':'medium','quality':'slow'}[r.speed],'-crf',str(r.quality),'-pix_fmt','yuv420p'] if r.codec=='h264' else ['-c:v','prores_ks','-profile:v',str(r.prores_quality),'-pix_fmt','yuv444p10le' if r.prores_quality>=4 else 'yuv422p10le','-alpha_bits','0']
                    if r.codec=='h264' and r.h264_profile!='auto':codec+=['-profile:v',r.h264_profile]
                    codec+=['-video_track_timescale',str(r.rate.numerator)]
                    codec+=['-colorspace','bt709','-color_primaries','bt709','-color_range','tv',
                            '-color_trc',movie_transfer]
                elif r.container=='png':codec=['-c:v','png','-pix_fmt',('rgba64be' if output_alpha else 'rgb48be') if r.image_depth=='16' else ('rgba' if output_alpha else 'rgb24'),'-compression_level',str(r.png_compression)]
                elif r.container=='tiff':codec=['-c:v','tiff','-pix_fmt','rgba' if output_alpha else 'rgb24','-compression_algo',r.tiff_compression]
                elif r.container=='jpeg':codec=['-c:v','mjpeg','-q:v',str(jpeg_qscale(r.jpeg_quality)),'-pix_fmt','yuvj444p']
                else:codec=['-c:v','libwebp','-lossless','1' if r.webp_lossless else '0','-quality',str(r.webp_quality),'-pix_fmt','bgra']
                target=output.sequence_pattern if r.mode=='sequence' else output.staged
                if r.mode=='sequence':codec+=['-start_number',str(output.start_number)]
                stamps=[]
                if isinstance(source,VideoSource) and r.mode=='frame':
                    codec+=['-stats_enc_pre:v','pipe:1','-stats_enc_pre_fmt:v','mediaforge_current={pts}|{tb}']
                def record(line):
                    match=re.fullmatch(r'mediaforge_current=(-?\d+)\|([0-9]+/[0-9]+)\s*',line)
                    if match:stamps.append(Fraction(int(match[1]))*Fraction(match[2]))
                c.report('encode','Converting authoritative source pictures…')
                native_codec=not movie
                native_command=None
                if native_codec:
                    # Full-resolution straight intermediates, never Viewer
                    # pixels. Source and intermediates remain write/delete denied.
                    intermediates=[output.auxiliary_path(f'codec-source_{i:06d}.png') for i in range(r.count)]
                    intermediate_target=output.directory/'codec-source_%06d.png'
                    capture_stamp=(['-stats_enc_pre:v','pipe:1','-stats_enc_pre_fmt:v','mediaforge_current={pts}|{tb}'] if isinstance(source,VideoSource) and r.mode=='frame' else [])
                    source_depth=16 if video and any(s in video.get('pix_fmt','') for s in ('10','12','16')) else 8
                    native_source=bool(members and members[0].facts.depth>8)
                    if native_source:source_depth=members[0].facts.depth
                    if not native_source:
                        # Negotiate the same straight, native-depth raster in
                        # both source decodes. A different sink format can
                        # change the scaler's RGB conversion and rounding.
                        pixel_format=('rgba64be' if output_alpha else 'rgb48be') if source_depth==16 else ('rgba' if output_alpha else 'rgb24')
                        canonical=f'format={pixel_format}'
                        source_filters=f'{picture_filters},{canonical}'
                        intermediate_command=common.copy()
                        intermediate_command[intermediate_command.index('-vf')+1]=source_filters
                        run(intermediate_command+['-c:v','png','-pix_fmt',pixel_format,'-compression_level','1','-start_number','0',*capture_stamp,str(intermediate_target)],c,e._timeout,output=output,on_output=record)
                        for path in intermediates:output.member_locks.add(path)
                        def reference_hash(args,filters,name):
                            dest=output.auxiliary_path(name)
                            timing = ['-enc_time_base:v','filter'] if windowed else []
                            run(args+['-map','0:v:0','-an','-vf',filters,'-frames:v',str(r.count),'-fps_mode','passthrough',*timing,'-c:v','rawvideo','-pix_fmt',pixel_format,'-threads:v','1','-f','framemd5','-hash','sha256',str(dest)],c,e._timeout,output=output)
                            output.member_locks.add(dest)
                            return dest
                        expected=reference_hash(base+inputs,source_filters,'codec-original.sha')
                        if windowed:
                            _check_source_clock(expected, r, c)
                        actual=reference_hash(base+['-start_number','0','-i',str(intermediate_target)],canonical,'codec-input.sha')
                        _compare_hash_ledgers(expected,actual,r.count,c)
                    else:intermediates=list(selected_paths)
                    plan=output.auxiliary_path('codec-plan.json')
                    targets=output.members if r.mode=='sequence' else [output.staged]
                    if r.mode=='sequence' and not native_source:
                        layout=r.numbering
                        member_plan=dict(numbered=dict(input=str(output.directory),output=str(output.staged),
                            count=layout.count,prefix=layout.prefix,extension=layout.extension,start=layout.start,padding=layout.padding))
                    else:member_plan=dict(members=list(zip(map(str,intermediates),map(str,targets))))
                    plan_text=json.dumps(dict(**member_plan,format=r.container,
                        width=r.width,height=r.height,input_width=r.source_width if native_source else r.reformat.input_width if alpha_transform else r.width,input_height=r.source_height if native_source else r.reformat.input_height if alpha_transform else r.height,
                        transform=dict(picture_width=str(r.reformat.picture_width),picture_height=str(r.reformat.picture_height),x=str(r.reformat.x),y=str(r.reformat.y)) if alpha_transform or native_source and _spatial_transform(r.reformat) else None,
                        native_source=native_source,source_format=members[0].facts.format if native_source else 'png',
                        source_type=members[0].facts.sample_type if native_source else 'uint',orientation=members[0].facts.orientation if native_source else 1,
                        crop=[r.crop.x,r.crop.y,r.crop.width,r.crop.height] if native_source and r.crop else None,matte=r.matte if native_source else None,
                        source_color='BT.709' if (members and members[0].facts.color_class=='BT.709') or video and video.get('color_transfer')=='bt709' else 'sRGB',
                        channels=4 if output_alpha else 3,depth=r.image_depth,source_depth=source_depth,lossless=r.webp_lossless,
                        compression=r.tiff_compression,quality=r.webp_quality if r.container=='webp' else r.jpeg_quality,sampling=r.jpeg_subsampling))
                    plan.write_text(plan_text,encoding='utf-8')
                    output.member_locks.add(plan)
                    if plan.read_text('utf-8')!=plan_text:raise ExportError('source_changed','Codec plan identity changed before ownership.')
                    if plan.stat().st_size>16*1024**2:raise ExportError('metadata_limit','Codec plan exceeds its admitted bound.')
                    native_command=_codec_command(plan)
                    run(native_command+['encode'],c,e._timeout,output=output)
                else:
                    if members and members[0].facts.depth>8:
                        # Preserve typed source processing through a verified
                        # full-resolution PNG16 boundary before the selected
                        # SDR video encoder's explicit 8/10-bit quantization.
                        staged_sources=[output.auxiliary_path(f'movie-source_{i:06d}.png') for i in range(r.count)]
                        plan=output.auxiliary_path('movie-source-plan.json')
                        facts=members[0].facts
                        plan.write_text(json.dumps(dict(members=list(zip(map(str,selected_paths),map(str,staged_sources))),
                            format='png',width=r.width,height=r.height,input_width=r.source_width,input_height=r.source_height,
                            native_source=True,source_format=facts.format,source_type=facts.sample_type,orientation=facts.orientation,
                            source_color=facts.color_class,crop=[r.crop.x,r.crop.y,r.crop.width,r.crop.height] if r.crop else None,
                            matte=r.matte,transform=dict(picture_width=str(r.reformat.picture_width),picture_height=str(r.reformat.picture_height),x=str(r.reformat.x),y=str(r.reformat.y)) if _spatial_transform(r.reformat) else None,
                            channels=3,depth='16',source_depth=facts.depth,compression='raw')),encoding='utf-8')
                        output.member_locks.add(plan)
                        native=_codec_command(plan)
                        run(native+['encode'],c,e._timeout,output=output)
                        for path in staged_sources:output.member_locks.add(path)
                        run(native+['validate'],c,e._timeout,output=output)
                        common=base+['-framerate',str(r.rate),'-start_number','0','-i',str(output.directory/'movie-source_%06d.png'),
                            '-map','0:v:0','-an','-sn','-dn','-vf',f'setsar={r.sar.numerator}/{r.sar.denominator}:max=65535,{movie_color},settb=expr=1/{r.rate.numerator},setpts=N*{r.rate.denominator}',
                            '-frames:v',str(r.count),'-enc_time_base:v','filter','-fps_mode:v','passthrough','-map_metadata','-1','-threads:v','1']
                    run(common+codec+[str(target)],c,e._timeout,output=output,on_output=record)
                if isinstance(source,VideoSource) and r.mode=='frame':
                    if len(stamps)!=1 or abs(stamps[0]*1_000_000-r.snapshot.timeline.timestamps[r.start])>1:
                        raise ExportError('frame_identity_failed','Encoded Current Frame did not retain the captured source timestamp/ordinal.')
                c.report('validation','Verifying every output picture before publication…')
                if movie:
                    output.member_locks.add(output.staged)
                    _,stream,count=e._probe(output.staged,c,output,processes=processes)
                    from mediaforge.infrastructure.ffmpeg.resize import FfmpegResizeExecutor
                    FfmpegResizeExecutor._orientation(stream)
                    if count or stream.get('codec_name')!=r.codec or (stream.get('width'),stream.get('height'))!=(r.width,r.height):raise ExportError('validation_failed','Movie stream/codec/geometry mismatch.')
                    if (stream.get('color_space'),stream.get('color_primaries'),stream.get('color_transfer'),stream.get('color_range'))!=('bt709','bt709',movie_transfer,'tv'):
                        raise ExportError('validation_failed','Movie source transfer/matrix/range signaling mismatch.')
                    if Fraction(stream.get('sample_aspect_ratio','0').replace(':','/'))!=r.sar or Fraction(stream.get('avg_frame_rate','0'))!=r.rate:
                        raise ExportError('validation_failed','Movie exact FPS or pixel-aspect mismatch.')
                    if abs(Fraction(stream.get('duration','0'))-Fraction(r.count,1)/r.rate)>Fraction(1,1_000_000):raise ExportError('validation_failed','Movie terminal duration mismatch.')
                    if r.codec=='prores' and stream.get('profile')!={0:'Proxy',1:'LT',2:'Standard',3:'HQ',4:'4444',5:'XQ'}[r.prores_quality]:raise ExportError('validation_failed','ProRes profile mismatch.')
                    frames=json.loads(run([str(e._runtime.ffprobe),'-v','error','-select_streams','v:0','-read_intervals',f'%+#{r.count+1}','-show_frames','-show_entries','frame=best_effort_timestamp_time','-of','json',str(output.staged)],c,e._probe_timeout,output=output)).get('frames',[])
                    if len(frames)!=r.count or any(abs(Fraction(f['best_effort_timestamp_time'])-Fraction(i,1)/r.rate)>Fraction(1,1_000_000) for i,f in enumerate(frames)):
                        raise ExportError('validation_failed','Movie frame count or rational PTS mismatch.')
                else:
                    outputs=output.members if r.mode=='sequence' else [output.staged]
                    for path in outputs:
                        output.member_locks.add(path)
                        if native_codec:
                            # Successful native decoding alone is not import
                            # parity. Apply our bounded file/profile admission
                            # before publication too (e.g. no accidental BigTIFF
                            # or unsupported color metadata from a writer).
                            facts=e._image_service.reader.probe(str(path)).facts
                            depth=32 if r.image_depth=='float' else int(r.image_depth) if r.container in ('png','tiff') else 8
                            # WebP may omit an entirely opaque alpha plane.
                            # The codec validator still checks every decoded
                            # alpha sample against the original processed data.
                            alpha_ok=facts.alpha==output_alpha or (r.container=='webp' and output_alpha and not facts.alpha)
                            if not alpha_ok or (facts.format,facts.width,facts.height,facts.depth,facts.sample_type,facts.orientation)!=(r.container,r.width,r.height,depth,'float' if depth==32 else 'uint',1):
                                raise ExportError('validation_failed','Encoded image does not match its qualified import profile.')
                            continue
                        if r.container=='png' and r.image_depth=='16':
                            import struct
                            with path.open('rb') as file:header=file.read(33)
                            if header[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',header[16:24])!=(r.width,r.height) or header[24:26]!=bytes([16,6 if output_alpha else 2]):
                                raise ExportError('validation_failed','PNG output data type/channel/geometry mismatch.')
                        else:
                            facts=e._image_service.reader.probe(str(path)).facts
                            if facts.orientation!=1 or (facts.width,facts.height)!=(r.width,r.height):raise ExportError('validation_failed','Image output geometry/orientation mismatch.')
                    if native_codec:
                        def progress(line):
                            match=re.fullmatch(r'mediaforge_validated=(\d+)\s*',line)
                            if match and 0<int(match[1])<=r.count:
                                c.progress(min(.99,int(match[1])/r.count),'','')
                        run(native_command+['validate'],c,e._timeout,output=output,
                            on_output=progress if r.mode=='sequence' else None)
                    else:
                        # Lossless equality is checked in straight RGBA from files,
                        # never against a premultiplied Viewer representation.
                        def frame_hash(args,filters,name,output_args=()):
                            path=output.auxiliary_path(name)
                            pixel_format='rgba64le' if r.image_depth=='16' else 'rgba'
                            command=args+['-map','0:v:0','-an','-vf',filters+',format='+pixel_format,'-frames:v',str(r.count),'-fps_mode','passthrough','-c:v','rawvideo','-threads:v','1','-f','framemd5','-hash','sha256',*output_args,str(path)]
                            run(command,c,e._timeout,output=output)
                            if path.stat().st_size>2*1024**2:raise ExportError('validation_failed','Frame hash output exceeds its bound.')
                            return [line.rsplit(',',1)[-1].strip() for line in path.read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')]
                        mask="format=gbrap,geq=r='if(eq(alpha(X,Y),0),0,r(X,Y))':g='if(eq(alpha(X,Y),0),0,g(X,Y))':b='if(eq(alpha(X,Y),0),0,b(X,Y))':a='alpha(X,Y)'" if r.container=='webp' and output_alpha else 'null'
                        expected=frame_hash(base+inputs,picture_filters+','+mask,'source.sha') if r.container!='jpeg' and not(r.container=='webp' and not r.webp_lossless) else None
                        actual_args=(base+['-framerate','24','-start_number',str(output.start_number),'-i',str(target)] if r.mode=='sequence' else base+['-i',str(output.staged)])
                        actual=frame_hash(actual_args,mask,'output.sha')
                        if len(actual)!=r.count or expected is not None and (len(expected)!=r.count or actual!=expected):raise ExportError('pixel_validation_failed','Lossless output pixels do not match original-source processing.')
                for path in paths:
                    if _sha(path,c)!=hashes[str(path)]:raise ExportError('source_changed','Source bytes changed during conversion.')
                image_capacity.check(r,output)
                from mediaforge.infrastructure.filesystem.export_identity import capture_output
                members=output.members if r.mode=='sequence' else [output.staged]
                names=[p.name for p in members] if r.mode=='sequence' else [r.destination.name]
                identity=capture_output(members,names,r.destination,c,budget)
                c.check();c.report('publication','Publishing validated output…')
                return ExportResult(output.publish(c),identity)
    except BaseException as error:
        if getattr(error,'retained_resources',()) or c.cleanup_failed:
            c.retain_resources(((budget,lease),))
            retained=True
        raise
    finally:
        if not retained:budget.release(lease)
