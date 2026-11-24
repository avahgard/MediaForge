"""Source-kind conversion admission, shared by Output summary and frozen capture."""
from pathlib import Path
from fractions import Fraction
import os
import stat
from mediaforge.application.contracts.image_source import StillSource,SequenceSource,VideoSource
from mediaforge.application.contracts.image_export import ImageExportRequest,NumberedOutput,jpeg_qscale
from mediaforge.application.contracts.export import output_filename
from bisect import bisect_left
from mediaforge.features.media.export import ExportError,output_aspect,validate_video_encoder
from mediaforge.features.media.crop import CropRect


def uses_image_export(item):
    return item is not None and (not isinstance(item.metadata.source,VideoSource) or item.output.kind!='media')


def geometry(item):
    m=item.metadata
    if isinstance(m.source,VideoSource) and not set(m.format_name.lower().split(',')).intersection({'mov','mp4','matroska'}):
        raise ExportError('unsupported_container','Image extraction qualifies single-file MP4/MOV/MKV sources only; playback remains available.')
    crop=CropRect(item.crop.x,item.crop.y,item.crop.width,item.crop.height) if item.crop.enabled else None
    if crop:
        if isinstance(m.source,VideoSource):crop.validate(m.width,m.height)
        elif not(0<=crop.x<m.width and 0<=crop.y<m.height and crop.width>0 and crop.height>0
                 and crop.x+crop.width<=m.width and crop.y+crop.height<=m.height):raise ExportError('invalid_crop','Crop lies outside original source pixels.')
    w,h=(crop.width,crop.height) if crop else (m.width,m.height)
    if isinstance(m.source, VideoSource):
        from mediaforge.application.contracts.image_geometry import dimensions, plan
        from mediaforge.application.services.export import geometry as video_geometry
        from mediaforge.features.media.reformat import sample_aspect
        source_sar = sample_aspect(m.sample_aspect_ratio)
        input_height = h
        if item.resize.enabled:
            coded_width, h, sar, _, reformat = video_geometry(item)
        else:
            coded_width, sar, reformat = w, source_sar, None
        if sar == 1:
            return coded_width, h, crop, reformat, sar
        display_width = coded_width * sar
        # Bake the intended display raster once. Keep height and round width
        # to the nearest integer pixel (ties up); no SAR metadata dependency.
        baked_width = max(1, (2 * display_width.numerator + display_width.denominator)
                          // (2 * display_width.denominator))
        target = dimensions(baked_width, h)
        placement = reformat.placement if reformat is not None else 'stretch'
        return baked_width, h, crop, plan(w, input_height, target, placement, str(source_sar)), sar
    reformat=None
    if item.resize.enabled:
        from mediaforge.application.contracts.image_geometry import scaled_dimensions,dimensions,plan
        r=item.resize
        if r.error:raise ExportError('invalid_geometry',r.error)
        target=scaled_dimensions(w,h,r.scale) if r.mode=='scale' else dimensions(r.width,r.height)
        sar=output_aspect(r.pixel_aspect,target.width,target.height,m.sample_aspect_ratio)
        reformat=plan(w,h,target,'stretch' if r.mode=='scale' else r.placement,str(Fraction(m.sample_aspect_ratio.replace(':','/'))/sar))
        w,h=target.width,target.height
    if output_aspect(item.resize.pixel_aspect,w,h,m.sample_aspect_ratio)!=1 and item.resize.enabled and item.output.kind!='media':
        raise ExportError('unsupported_par','Image conversion requires square pixels. Set Pixel Aspect to Square.')
    if Fraction(m.sample_aspect_ratio.replace(':','/'))!=1:
        raise ExportError('unsupported_par','Image extraction currently requires square source pixels.')
    return w,h,crop,reformat,Fraction(1)


def describe(item,snapshot=None):
    context={}
    try:
        w,h,_,reformat,baked_sar=geometry(item);s=item.output;source=item.metadata.source
        kind=s.kind
        if isinstance(source,StillSource):kind='still'
        if isinstance(source,SequenceSource) and not item.sequence.confirmed:raise ExportError('fps_unconfirmed','Confirm the sequence FPS first.')
        if kind=='media' and not isinstance(source,SequenceSource):raise ExportError('invalid_mode','Choose a supported conversion.')
        if kind=='sequence' and isinstance(source,StillSource):raise ExportError('invalid_mode','Still input produces one image.')
        image=s.container in ('png','jpeg','tiff','webp')
        if s.image_depth not in ('8','16','float') or s.image_depth=='float' and s.container!='tiff' or s.image_depth!='8' and s.container not in ('png','tiff'):
            raise ExportError('unsupported_depth','Choose a supported data type for the selected format.')
        if s.jpeg_subsampling not in ('411','422','444') or type(s.jpeg_quality)is not int or not 1<=s.jpeg_quality<=100 or s.tiff_compression not in ('raw','packbits','lzw','deflate') or type(s.webp_quality)is not int or not 0<=s.webp_quality<=100:
            raise ExportError('invalid_codec_settings','Choose supported quality, subsampling and compression settings.')
        if (kind=='media' and s.container not in ('mp4','mov')) or (kind!='media' and not image):raise ExportError('invalid_format','Choose a compatible File Type.')
        if kind=='sequence' and s.container=='webp':raise ExportError('unsupported_format','WebP is a single still format.')
        alpha=(source.facts.alpha if isinstance(source,StillSource) else source.manifest.members[0].facts.alpha if isinstance(source,SequenceSource) else False)
        padding=bool(reformat and reformat.placement=='fit' and (reformat.picture_width<w or reformat.picture_height<h))
        context=dict(hasAlpha=alpha or padding,matteRequired=bool((alpha or padding) and s.container in ('jpeg','mp4','mov')))
        facts=source.facts if isinstance(source,StillSource) else source.manifest.members[0].facts if isinstance(source,SequenceSource) else None
        if facts and facts.variant=='associated-alpha':raise ExportError('unsupported_alpha_class','Associated-alpha TIFF conversion is not qualified. Preview remains available; originals are preserved.')
        if (alpha or padding) and (s.container in ('jpeg','mp4','mov')) and not s.matte:
            raise ExportError('matte_required','Transparency or Fit padding: choose a Black or White matte explicitly.')
        if (alpha or padding) and s.container=='webp' and not s.webp_ack:
            raise ExportError('webp_confirmation','Confirm that invisible RGB under fully transparent pixels may change, or choose PNG/TIFF.')
        if kind=='media' and (w%2 or h%2 or min(w,h)<16):raise ExportError('invalid_video_geometry','MOV/MP4 requires even dimensions, minimum 16×16.')
        if kind=='media' and (s.codec not in ('h264','prores') or s.codec=='prores' and s.container!='mov'):
            raise ExportError('invalid_codec','Choose H.264, or ProRes with MOV.')
        if kind=='media':
            validate_video_encoder(s.codec,s.prores_quality if s.codec=='prores' else s.h264_quality,s.speed,s.h264_profile)
        sar=output_aspect(item.resize.pixel_aspect,w,h,item.metadata.sample_aspect_ratio) if kind=='media' and item.resize.enabled else Fraction(1)
        extension='.'+('jpg' if s.container=='jpeg' else s.container)
        dar=Fraction(w,h)*sar
        message=('Numbered images keep every selected picture; VFR timestamps are not stored in filenames. Reimport requires confirmed FPS.' if kind=='sequence' else
                'Authoritative source pixels; Viewer quality is ignored. '+(f'JPEG is lossy. Quality {s.jpeg_quality/100:.2f}; chroma {s.jpeg_subsampling[0]}:{s.jpeg_subsampling[1]}:{s.jpeg_subsampling[2]}.' if s.container=='jpeg' else
                 'WebP preserves visible colors/alpha; invisible RGB may change.' if s.container=='webp' else ''))
        if facts and facts.color_class=='BT.709':message+=' BT.709 encoded RGB is explicitly converted to sRGB for image output.'
        if baked_sar != 1:
            message+=f' Display aspect is baked into {w}×{h} square pixels (recipe SAR {baked_sar.numerator}:{baked_sar.denominator}).'
        if kind=='media':message+=' RGB uses the pinned SDR BT.709 matrix and limited-range YUV conversion, with explicit source transfer and chroma subsampling; ProRes promotion adds no source detail or alpha.'
        if not isinstance(source,VideoSource) or snapshot is not None:
            start,end,rate=picture_selection(item,snapshot)
            message+=f' {end-start} picture(s).'
            if isinstance(source,SequenceSource):message+=f' Source numbers {source.manifest.numbers[start]}–{source.manifest.numbers[end-1]}; slots [{start}, {end}); confirmed FPS {rate.numerator}/{rate.denominator}.'
            elif isinstance(source,VideoSource):message+=f' Source ordinals [{start}, {end}).'
            if kind=='sequence':
                layout=numbering(item,start,end)
                message+=f' Output: {layout.name(0)} … {layout.name(layout.count-1)}.'
        return dict(valid=True,width=w,height=h,sar=f'{sar.numerator}:{sar.denominator}',dar=f'{dar.numerator}:{dar.denominator}',extension=extension,
            codec=s.codec if kind=='media' else s.container,actualCodec=s.codec if kind=='media' else s.container,
            message=message,**context,viewerSupported=True,dataType=s.image_depth)
    except (ValueError,RuntimeError) as error:return dict(**context,valid=False,message=str(error))


def video_trim_span(item, snapshot):
    """Source-picture ordinals inside the accepted half-open player time range."""
    timestamps = snapshot.timeline.timestamps
    bounds = []
    for ms, picture in ((item.trim.in_ms, item.trim.in_picture), (item.trim.out_ms, item.trim.out_picture)):
        if picture is not None and picture.signature != snapshot.signature:
            raise ExportError('source_changed', 'Source changed since the Trim picture was selected. Set the Trim boundary again.')
        pts_us = picture.pts_us if picture is not None else ms * 1000 - snapshot.offset_us
        bounds.append(bisect_left(timestamps, pts_us))
    return tuple(bounds)


def picture_selection(item,snapshot=None):
    m,s=item.metadata,item.output;source=m.source
    mode='still' if isinstance(source,StillSource) else s.kind
    if isinstance(source,VideoSource):
        if snapshot is None:raise ExportError('frame_unavailable','Exact source timeline/current picture is unavailable. Wait for indexing or select a settled frame.')
        values=snapshot.timeline.timestamps
        if mode=='frame':
            if snapshot.ordinal is None or not 0<=snapshot.ordinal<len(values):raise ExportError('frame_unavailable','Current picture identity is unavailable.')
            start,end=snapshot.ordinal,snapshot.ordinal+1
        else:
            if item.trim.enabled:
                start,end=video_trim_span(item,snapshot)
            else:start,end=0,len(values)
        rate=None
    elif isinstance(source,SequenceSource):
        start,end=(item.sequence.current_slot,item.sequence.current_slot+1) if mode in ('frame','still') else ((item.sequence.in_slot,item.sequence.out_slot) if item.trim.enabled else (0,source.manifest.count))
        rate=item.sequence.rate
    else:start,end,rate=0,1,None
    if start>=end:raise ExportError('invalid_range','Select an interval containing at least one picture.')
    return start,end,rate


def numbering(item,start,end):
    s=item.output
    extension='.'+('jpg' if s.container=='jpeg' else s.container)
    digits=s.sequence_start
    if not isinstance(digits,str) or not digits.isascii() or not digits.isdigit() or len(digits)>19:
        raise ExportError('invalid_numbering','Start Number must contain 1–19 ASCII digits.')
    first=int(digits)
    if first+end-start-1>2**63-1:
        raise ExportError('invalid_numbering','The last output number exceeds the supported 64-bit range.')
    layout=NumberedOutput(output_filename(s.filename)+'.','',extension,first,len(digits),end-start)
    output_filename(layout.name(layout.count-1))
    return layout


def capture(item,protected_sources=(),snapshot=None):
    summary=describe(item)
    if not summary['valid']:raise ExportError('invalid_settings',summary['message'])
    m,s=item.metadata,item.output;source=m.source
    mode='still' if isinstance(source,StillSource) else s.kind
    start,end,rate=picture_selection(item,snapshot)
    if isinstance(source, VideoSource) and mode == 'sequence' and m.navigation_rate_ratio and 1 < end-start <= 10000:
        # Only same-session receipts carry this hint. Check every selected
        # timestamp against the metadata rate before offering it as a default.
        candidate = Fraction(m.navigation_rate_ratio)
        clock = snapshot.timeline.timestamps
        origin = clock[start]
        if (1 <= candidate <= 120 and all(
                abs((clock[index] - origin) * candidate.numerator
                    - (index - start) * 1_000_000 * candidate.denominator) <= candidate.numerator
                for index in range(start + 1, end))):
            rate = candidate
    w,h,crop,reformat,_=geometry(item)
    folder=Path(s.folder)
    if not folder.is_absolute() or '..' in folder.parts or str(folder).startswith(('\\\\?\\','\\\\.\\')) or ':' in str(folder)[2:]:
        raise ExportError('unsafe_path','Choose an ordinary absolute output folder without parent traversal or alternate streams.')
    for ancestor in reversed((folder,*folder.parents)):
        info=ancestor.lstat()
        if getattr(info,'st_file_attributes',0)&0x400 or stat.S_ISLNK(info.st_mode):
            raise ExportError('unsafe_path','Reparse destination folders/ancestors are unsupported.')
        if not stat.S_ISDIR(info.st_mode):raise ExportError('output_unavailable','Choose an existing output folder.')
        if any(p.endswith((' ','.')) for p in ancestor.parts[1:]):raise ExportError('unsafe_path','Ambiguous Windows output path.')
    name=output_filename(s.filename,'' if mode=='sequence' else summary['extension'])
    # Keep the validated lexical path for native no-follow opening/ancestor locks.
    folder=Path(os.path.abspath(folder));target=folder/name
    if mode=='sequence' and isinstance(source,SequenceSource) and folder.is_relative_to(Path(source.path).parent):
        raise ExportError('source_directory_collision','Choose an output folder outside the input sequence directory tree.')
    if target.exists():raise ExportError('destination_exists','Destination exists. Choose a new name; image/sequence conversion never replaces existing output.')
    protected=tuple(map(Path,protected_sources))
    if isinstance(source,SequenceSource):protected+=tuple(Path(v.path) for v in source.manifest.members)
    for path in (*protected,Path(m.source_path)):
        if os.path.normcase(os.path.abspath(path))==os.path.normcase(str(target)):raise ExportError('source_collision','Loaded sources are never overwritten.')
    source_text=sum(len(v.path.encode('utf-8')) for v in source.manifest.members) if isinstance(source,SequenceSource) else len(m.source_path.encode('utf-8'))
    count=source.manifest.count if isinstance(source,SequenceSource) else 1
    output_text=0
    layout=None
    if mode=='sequence':
        layout=numbering(item,start,end)
        max_path_bytes=len(str(target/layout.name(layout.count-1)).encode('utf-8'))
        if max_path_bytes>2048:
            raise ExportError('output_metadata_limit','Output path exceeds 2 KiB per member. Choose a shorter folder or filename.')
        output_text=max_path_bytes*layout.count
    control_bytes=8*1024**2+(count+(layout.count if layout else 0))*4096+6*(source_text+output_text)
    return ImageExportRequest(Path(m.source_path),target,source,mode,s.container,w,h,m.width,m.height,start,end,rate,
        crop,reformat,s.matte,s.codec,s.h264_quality,s.prores_quality,snapshot,speed=s.speed,h264_profile=s.h264_profile,
        sar=Fraction(summary['sar'].replace(':','/')),jpeg_quality=s.jpeg_quality,png_compression=s.png_compression,tiff_compression=s.tiff_compression,control_bytes=control_bytes,numbering=layout,protected_sources=protected,
        image_depth=s.image_depth,jpeg_subsampling=s.jpeg_subsampling,webp_lossless=s.webp_lossless,webp_quality=s.webp_quality)
