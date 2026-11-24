"""Human-readable source facts; none of these labels supplies timeline truth."""
from fractions import Fraction
from pathlib import Path
import os
import re
from mediaforge.application.contracts.media import MediaInfoFactsDto

PROFILES = {'Proxy': '422 Proxy', 'LT': '422 LT', 'Standard': '422', '422': '422',
            'HQ': '422 HQ', '4444': '4444', 'XQ': '4444 XQ'}
TAGS = dict(zip(('apco','apcs','apcn','apch','ap4h','ap4x'),
                ('422 Proxy','422 LT','422','422 HQ','4444','4444 XQ')))


def container_label(metadata, facts):
    family = set(metadata.format_name.split(','))
    suffix = Path(metadata.source_path).suffix.lower()
    if suffix in ('.mov', '.mp4') and family.intersection({'mov','mp4'}):
        return 'QuickTime / MOV' if suffix == '.mov' else 'MP4'
    if suffix == '.mkv' and 'matroska' in family: return 'Matroska / MKV'
    return facts.container_long_name or 'Unknown'


def codec_label(name, long_name=None, profile=None, tag=None):
    if name == 'prores':
        a, b = PROFILES.get(profile), TAGS.get(tag)
        if a and b and a != b: return 'Apple ProRes'
        recognized = a or b
        return 'Apple ProRes' + (' ' + recognized if recognized else '')
    known = {'h264':'H.264 / AVC', 'hevc':'H.265 / HEVC', 'aac':'AAC', 'mp3':'MP3',
             'opus':'Opus', 'flac':'FLAC', 'av1':'AV1'}
    label = 'PCM' if name and name.startswith('pcm_') else known.get(name)
    if label: return label + (f' ({profile})' if profile and profile != 'unknown' else '')
    return long_name or name or 'Unknown'


def pixel_label(facts):
    pixel = facts.pixel_format
    if not pixel: return 'Unknown'
    label, depth = pixel, None
    yuv = re.fullmatch(r'(yuv|yuva)(420|422|444)p(?:(8|9|10|12|14|16)(?:le|be))?', pixel)
    rgb = re.fullmatch(r'(gbrp|gbrap)(?:(8|9|10|12|14|16)(?:le|be))?', pixel)
    if yuv:
        label = ('YUVA ' if yuv[1] == 'yuva' else 'YUV ') + {'420':'4:2:0','422':'4:2:2','444':'4:4:4'}[yuv[2]]
        depth = int(yuv[3] or 8)
    elif rgb:
        label = 'RGBA' if rgb[1] == 'gbrap' else 'RGB'
        depth = int(rgb[2] or 8)
    elif pixel in ('rgb24','bgr24','rgba','bgra','argb','abgr'):
        label = 'RGBA' if pixel in ('rgba','bgra','argb','abgr') else 'RGB'
        depth = 8
    depth = facts.bit_depth if facts.bit_depth and facts.bit_depth > 0 else depth
    return label + (f' {depth}-bit' if depth else '')


def rate_label(ratio, basis=None):
    try:
        rate = Fraction(ratio)
        if rate > 0:
            if rate.denominator == 1:
                value = str(rate.numerator)
            else:
                value = f'{float(rate):.2f}'.rstrip('0').rstrip('.')
                if '.' not in value:
                    value = '≈' + value
            suffix = f' ({basis})' if basis in ('average', 'nominal') else ''
            return f'{value} fps{suffix}'
    except (TypeError, ValueError, ZeroDivisionError, OverflowError): pass
    return 'Unknown'


def info_rows(metadata, frames):
    if metadata is None: return []
    from mediaforge.application.contracts.image_source import StillSource, SequenceSource
    if isinstance(metadata.source,SequenceSource):
        m = metadata.source.manifest
        return [{'label':k,'value':v,'section':False} for k,v in
                [('Sequence',m.pattern),('First / last',f'{m.numbers[0]} / {m.numbers[-1]}'),
                 ('Frames',str(m.count)),('Resolution',f'{metadata.width} × {metadata.height}'),
                 ('Color',m.members[0].facts.color_class),('Source layout',m.members[0].facts.variant or ('RGBA' if m.members[0].facts.alpha else 'RGB')),
                 ('Source samples',f'{m.members[0].facts.depth}-bit {m.members[0].facts.sample_type}'),
                 ('Preview pixels','8-bit premultiplied RGBA; originals remain authoritative'),
                 ('FPS authority','User confirmed in sequence inspector'),('Gap policy','Error'),
                 ('Source',metadata.source_path)]]
    if isinstance(metadata.source,StillSource):
        f=metadata.source.facts
        return [{'label':key,'value':value,'section':False} for key,value in
                [('File',metadata.source_name),('Path',os.path.normpath(metadata.source_path)),
                 ('Image',f.format.upper()),('Resolution',f'{f.width} × {f.height}'),
                 ('Orientation',f'{f.orientation} (applied)'),('Channels','RGBA' if f.alpha else 'RGB'),
                 ('Source layout',f.variant or ('RGBA' if f.alpha else 'RGB')),('Preview pixels','8-bit premultiplied RGBA; originals remain authoritative'),
                 ('Depth',f'{f.depth}-bit {f.sample_type}'),('Color',f.color_class),('Images','1')]]
    facts = metadata.info_facts or MediaInfoFactsDto()
    def row(label, value='', section=False): return {'label':label,'value':value,'section':section}
    duration = 'Unknown'
    if metadata.duration_seconds is not None:
        hours, remainder = divmod(round(metadata.duration_seconds * 1000), 3600000)
        minutes, remainder = divmod(remainder, 60000)
        seconds, millis = divmod(remainder, 1000)
        duration = f'{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}'
    rows = [row('File',metadata.source_name),row('Path',os.path.normpath(metadata.source_path)),
            row('Container',container_label(metadata,facts))]
    if frames in ('Indexing…', 'Unavailable') and not metadata.audio_only:
        if metadata.navigation_frame_count:
            frames = f'{metadata.navigation_frame_count} (stream metadata; unverified)'
        elif metadata.duration_seconds and metadata.frame_rate and facts.frame_rate_ratio:
            estimated = round(metadata.duration_seconds * metadata.frame_rate)
            if estimated > 0:
                frames = f'≈{estimated} (duration × frame rate; unverified)'
    if not metadata.audio_only:rows += [row('Video',section=True),
            row('Codec',codec_label(metadata.video_codec,facts.video_codec_long_name,facts.video_profile,facts.video_tag)),
            row('Pixel format',pixel_label(facts)),row('Resolution',f'{metadata.width} × {metadata.height}'),
            row('Frame rate',rate_label(facts.frame_rate_ratio,facts.frame_rate_basis)),row('Frames',frames)]
    rows += [row('Duration',duration),row('Audio',section=True)]
    if not facts.audio_codec: return rows + [row('None',section=True)]
    sample_rate = f'{facts.audio_sample_rate / 1000:g} kHz' if facts.audio_sample_rate and facts.audio_sample_rate % 100 == 0 else f'{facts.audio_sample_rate} Hz' if facts.audio_sample_rate else 'Unknown'
    channels = 'Unknown'
    if facts.audio_channels:
        layout = {'stereo':'Stereo','mono':'Mono'}.get(facts.audio_layout,facts.audio_layout)
        channels = f'{layout} / {facts.audio_channels}' if layout else f'{facts.audio_channels} channels'
    return rows + [row('Codec',codec_label(facts.audio_codec,facts.audio_codec_long_name)),
                   row('Sample rate',sample_rate),row('Channels',channels)]
