"""Test-only late-onset cadence mutation; no production indexing/parser path."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe

def create_cfr_control_fixture(root, runtime):
    source = root / 'cfr-control.mp4'
    result = subprocess.run([str(runtime.ffmpeg), '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25:duration=12',
        '-c:v', 'libx264', '-preset', 'medium', '-g', '25', '-bf', '2',
        '-pix_fmt', 'yuv420p', '-y', str(source)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def create_late_timing_fixture(root, runtime):
    create_cfr_control_fixture(root, runtime)
    return create_late_timing_from_control(root, runtime)


def create_late_timing_from_control(root, runtime):
    """Mutate an already generated control, retaining the original recipe checks."""
    source = root / 'cfr-control.mp4'
    target = root / 'late-vfr.mp4'
    def probe(path):
        p = subprocess.run([str(runtime.ffprobe), '-v', 'error', '-show_streams',
            '-show_format', '-show_packets', '-of', 'json', str(path)],
            capture_output=True, text=True, timeout=15)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)
    before = probe(source)
    packets = before['packets']
    stream = before['streams'][0]
    period = int(packets[0]['duration'])
    origin = min(int(p['pts']) for p in packets)
    changes = []
    def rebuild(data):
        out = bytearray()
        pos = 0
        while pos < len(data):
            size, kind = struct.unpack_from('>I4s', data, pos)
            assert size >= 8 and pos+size <= len(data), (pos,size,kind)
            payload = data[pos+8:pos+size]
            if kind in (b'moov', b'trak', b'mdia', b'minf', b'stbl'):
                payload = rebuild(payload)
            elif kind == b'ctts':
                assert payload[0] == 0
                entries = struct.unpack_from('>I',payload,4)[0]
                offsets = []
                for j in range(entries):
                    count, offset = struct.unpack_from('>II',payload,8+j*8)
                    offsets.extend([offset]*count)
                assert len(offsets) == len(packets) == 300
                for j, packet in enumerate(packets):
                    ordinal = (int(packet['pts'])-origin)//period
                    # A regular prefix, a late 60ms interval, a shifted regular
                    # region, then a 20ms interval, with total count/duration intact.
                    delta = period//2 if ordinal in (100,181) else period if 101 <= ordinal <= 180 else 0
                    if delta:
                        offsets[j] += delta
                        changes.append({'ordinal':ordinal,'old_pts':int(packet['pts']),
                                        'new_pts':int(packet['pts'])+delta})
                runs = []
                for offset in offsets:
                    if runs and runs[-1][1] == offset: runs[-1][0] += 1
                    else: runs.append([1,offset])
                payload = payload[:4]+struct.pack('>I',len(runs))+b''.join(struct.pack('>II',*r) for r in runs)
            out.extend(struct.pack('>I4s',len(payload)+8,kind)+payload)
            pos += size
        return bytes(out)
    target.write_bytes(rebuild(source.read_bytes()))
    after = probe(target)
    assert len(changes) == 82
    assert after['streams'][0]['nb_frames'] == '300'
    assert after['streams'][0]['avg_frame_rate'] == after['streams'][0]['r_frame_rate'] == '25/1'
    assert float(after['streams'][0]['duration']) == 12.0
    assert after['streams'][0]['duration'] == before['streams'][0]['duration']
    assert [p['pts'] for p in before['packets'][:65]] == [p['pts'] for p in after['packets'][:65]]
    for p,q in zip(before['packets'],after['packets']):
        assert p['pos'] == q['pos'] and p['size'] == q['size']
    metadata = FfprobeMediaMetadataProbe(runtime.ffprobe).probe(target)
    evidence = {'control_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'late_vfr_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                'stream':after['streams'][0], 'changes':changes,
                'navigation_rate':metadata.navigation_frame_rate,
                'navigation_count':metadata.navigation_frame_count,
                'presentation_timestamps':[int(p['pts']) for p in sorted(after['packets'],key=lambda p:int(p['pts']))]}
    (root/'late-vfr-fixture.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    return evidence
