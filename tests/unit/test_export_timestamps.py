"""Real filtered-PTS oracles: between-frame Trim and non-frame-grid VFR."""
from fractions import Fraction
import json
from pathlib import Path
import subprocess

import pytest

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.services.export import capture
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.features.media.export import ExportControl
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime


@pytest.fixture(scope="module")
def timestamp_sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("точные timestamps")
    runtime = resolve_ffmpeg_runtime()
    for kind in ("cfr", "vfr"):
        args = [str(runtime.ffmpeg), "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                "testsrc2=s=160x120:r=25:d=" + ("1.2" if kind == "cfr" else "0.8"),
                "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=1.2"]
        if kind == "vfr":
            args += ["-vf", r"settb=1/1000,setpts=N*40+mod(N\,3)*7", "-enc_time_base:v:0", "1/1000"]
        args += ["-fps_mode:v:0", "passthrough", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", str(root / (kind + ".mp4"))]
        subprocess.run(args, capture_output=True, check=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return root, runtime


@pytest.mark.integration
@pytest.mark.parametrize("kind,codec", [("cfr", "h264"), ("vfr", "h264"), ("cfr", "prores"), ("vfr", "prores")])
def test_exact_filtered_pts_and_audio_origin(timestamp_sources, tmp_path, kind, codec):
    root, runtime = timestamp_sources
    source = root / (kind + ".mp4")
    evidence = {"kind": kind, "codec": codec, "commands": []}
    def probe(path, frames=False, stream="v:0"):
        args = [str(runtime.ffprobe), "-v", "error", "-of", "json"]
        args += (["-select_streams", stream, "-show_frames", "-show_entries", "frame=best_effort_timestamp,nb_samples"]
                 if frames else ["-show_streams", "-show_format"])
        p = subprocess.run(args + [str(path)], capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
        return json.loads(p.stdout)
    def times(path, metadata):
        video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
        return [Fraction(f["best_effort_timestamp"]) * Fraction(video["time_base"]) for f in probe(path, True)["frames"]]
    def factory(args, **kwargs):
        evidence["commands"].append(args)
        return subprocess.Popen(args, **kwargs)
    try:
        src = probe(source); v = src["streams"][0]
        m = MediaMetadataDto(source.name, str(source), src["format"]["format_name"], float(src["format"]["duration"]),
                             v["codec_name"], v["width"], v["height"], None)
        item = MediaWorkspaceSession().add(m)[0]
        item.trim.enabled = True
        start, end = (410, 810) if kind == "cfr" else (47, 640)
        item.trim.in_ms, item.trim.out_ms = start, end
        item.output.folder = str(tmp_path)
        item.output.filename = kind + " точный экспорт"
        item.output.codec = codec
        item.output.container = "mp4" if codec == "h264" else "mov"
        output = FfmpegExportExecutor(runtime, process_factory=factory).execute(capture(item), ExportControl()).output
        actual_metadata = probe(output)
        origin = Fraction(v.get("start_pts", 0)) * Fraction(v["time_base"])
        selected = [t for t in times(source, src) if origin + Fraction(start,1000) <= t < origin + Fraction(end,1000)]
        expected = [t-origin-Fraction(start,1000) for t in selected]
        actual = times(output, actual_metadata)
        evidence.update(source=src, output=actual_metadata, expected=[str(t) for t in expected], actual=[str(t) for t in actual])
        assert len(expected) == (10 if kind == "cfr" else 15)
        assert actual == expected  # Exact rational equality: no nominal-FPS tolerance.
        audio = next(s for s in actual_metadata["streams"] if s["codec_type"] == "audio")
        decoded_audio = probe(output, True, "a:0")["frames"]
        audio_start = Fraction(decoded_audio[0]["best_effort_timestamp"]) * Fraction(audio["time_base"])
        evidence["decoded_audio_start"] = str(audio_start)
        assert audio_start == 0
        assert actual[0] - audio_start == (Fraction(30,1000) if kind == "cfr" else 0)
        if kind == "vfr":
            assert set(expected[n]-expected[n-1] for n in range(1,len(expected))) == {Fraction(47,1000),Fraction(26,1000)}
        evidence["status"] = "PASS"
    finally:
        (tmp_path / "pts-proof.json").write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding="utf-8")


@pytest.mark.integration
def test_combined_crop_fit_fill_stretch_keeps_between_frame_pts(timestamp_sources, tmp_path):
    root,runtime=timestamp_sources;source=root/'cfr.mp4'
    def cli(args):
        return subprocess.run([str(a) for a in args],capture_output=True,check=True,timeout=30,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout
    src=json.loads(cli([runtime.ffprobe,'-v','error','-show_streams','-show_format','-of','json',source]))
    v=src['streams'][0]
    item=MediaWorkspaceSession().add(MediaMetadataDto(source.name,str(source),src['format']['format_name'],
         float(src['format']['duration']),v['codec_name'],160,120,25))[0]
    item.trim.enabled=True;item.trim.in_ms=410;item.trim.out_ms=810
    item.crop.enabled=True;item.crop.x=40;item.crop.y=20;item.crop.width=item.crop.height=80
    item.resize.enabled=True;item.resize.mode='format';item.resize.width=160;item.resize.height=90
    item.output.folder=str(tmp_path)
    rows=[]
    for placement in ('fit','fill','stretch'):
        item.resize.placement=placement;item.output.filename='combined '+placement
        output=FfmpegExportExecutor(runtime).execute(capture(item),ExportControl()).output
        meta=json.loads(cli([runtime.ffprobe,'-v','error','-show_streams','-of','json',output]))
        video=meta['streams'][0]
        assert (video['width'],video['height'],video['sample_aspect_ratio'])==(160,90,'1:1')
        frames=json.loads(cli([runtime.ffprobe,'-v','error','-select_streams','v:0','-show_frames',
             '-show_entries','frame=best_effort_timestamp','-of','json',output]))['frames']
        actual=[Fraction(f['best_effort_timestamp'])*Fraction(video['time_base']) for f in frames]
        assert actual==[Fraction(30+40*n,1000) for n in range(10)]
        rgb=cli([runtime.ffmpeg,'-v','error','-i',output,'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
        def pixel(x,y):return rgb[(y*160+x)*3:(y*160+x)*3+3]
        assert (max(pixel(2,45))<20)==(placement=='fit')
        assert max(pixel(80,45))>40
        ref=cli([runtime.ffmpeg,'-v','error','-i',source,'-vf',r'select=eq(n\,11),crop=80:80:40:20,scale=160:90:flags=lanczos',
                 '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-']) if placement=='stretch' else None
        error=sum(abs(a-b) for a,b in zip(rgb,ref))/len(rgb) if ref is not None else None
        if error is not None: assert error<10
        rows.append({'placement':placement,'pts':[str(t) for t in actual],'ffprobe':meta,'stretch_picture_mae':error})
    (tmp_path/'combined-proof.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
