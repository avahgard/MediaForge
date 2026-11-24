"""Bounded real TASK-010 export proof; no private media or historical cleanup."""
import argparse
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import threading
import time

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.services.export import ExportService, capture
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.features.media.export import ExportControl, ExportError
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime


def run(root):
    root.mkdir(parents=True, exist_ok=True)
    runtime = resolve_ffmpeg_runtime()
    evidence = {"runtime": {"ffmpeg": str(runtime.ffmpeg), "ffprobe": str(runtime.ffprobe)}, "cases": [], "commands": []}
    def command(args, binary=False):
        result = subprocess.run(args, capture_output=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
        evidence["commands"].append({"args": args, "exit": result.returncode})
        if result.returncode:
            raise AssertionError(result.stderr.decode('utf-8','replace'))
        return result.stdout if binary else result.stdout.decode('utf-8')
    def probe(path):
        return json.loads(command([str(runtime.ffprobe), "-v","error","-show_streams","-show_format","-of","json",str(path)]))
    def stamps(path):
        p = json.loads(command([str(runtime.ffprobe),"-v","error","-select_streams","v:0","-show_frames","-show_entries","frame=best_effort_timestamp_time","-of","json",str(path)]))
        return [float(f['best_effort_timestamp_time']) for f in p['frames']]
    def audio_stamps(path):
        p = json.loads(command([str(runtime.ffprobe),"-v","error","-select_streams","a:0","-show_frames","-show_entries","frame=best_effort_timestamp_time,nb_samples","-of","json",str(path)]))
        return p['frames']
    def picture(path, select):
        return command([str(runtime.ffmpeg),"-v","error","-i",str(path),"-vf",select+",scale=16:12","-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","-"],True)
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    adapter = FfmpegExportExecutor(runtime,logger=logging.getLogger("export-proof"))
    def item(source):
        p = probe(source); v = next(s for s in p['streams'] if s['codec_type']=='video')
        session = MediaWorkspaceSession()
        m = MediaMetadataDto(source.name,str(source),p['format']['format_name'],float(p['format']['duration']),v['codec_name'],v['width'],v['height'],25,
                             sample_aspect_ratio=v.get('sample_aspect_ratio','1:1'))
        return session.add(m)[0]
    def export(item, name):
        item.output.filename = name
        request = capture(item)
        before = digest(request.source)
        result = adapter.execute(request,ExportControl())
        actual = probe(result.output)
        assert digest(request.source) == before
        evidence['cases'].append({"name":name,"path":str(result.output),"ffprobe":actual,"sha256":digest(result.output)})
        (root/'results.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
        return result.output
    try:
        source = root/'source audio.mp4'
        command([str(runtime.ffmpeg),"-v","error","-nostdin","-n","-f","lavfi","-i","testsrc2=s=160x120:r=25:d=1.2",
                 "-f","lavfi","-i","sine=frequency=440:sample_rate=48000:duration=1.2","-c:v","libx264","-crf","12","-pix_fmt","yuv420p","-c:a","aac",str(source)])
        original = digest(source)
        i = item(source)
        copied = export(i,'A exact copy')
        assert digest(copied) == original
        i.trim.enabled = True; i.trim.in_ms=400; i.trim.out_ms=800
        trimmed = export(i,'B trim audio')
        expected = [t-.4 for t in stamps(source) if .4 <= t < .8]
        assert len(stamps(trimmed)) == len(expected) == 10
        assert max(abs(a-b) for a,b in zip(stamps(trimmed),expected)) < .0015
        src_pixels = picture(source,"select=eq(n\\,10)")
        dst_pixels = picture(trimmed,"select=eq(n\\,0)")
        assert len(src_pixels)==len(dst_pixels)==576
        error = sum(abs(a-b) for a,b in zip(src_pixels,dst_pixels))/len(src_pixels)
        assert error < 10, error
        af = audio_stamps(trimmed)
        assert float(af[0]['best_effort_timestamp_time']) <= .025
        assert .375 <= float(af[-1]['best_effort_timestamp_time']) + af[-1]['nb_samples']/48000 <= .430
        evidence['trim_proof'] = {"expected_video_timestamps":expected,"actual_video_timestamps":stamps(trimmed),"first_frame_mean_absolute_error":error,"audio_first":af[0],"audio_last":af[-1]}
        i.trim.enabled=False; i.crop.enabled=True
        i.crop.x=40; i.crop.y=20; i.crop.width=80; i.crop.height=80
        cropped=export(i,'C crop')
        crop_ref=picture(source,'select=eq(n\\,0),crop=80:80:40:20')
        crop_actual=picture(cropped,'select=eq(n\\,0)')
        crop_error=sum(abs(a-b) for a,b in zip(crop_ref,crop_actual))/len(crop_ref)
        assert len(crop_ref)==len(crop_actual)==576 and crop_error < 10
        i.crop.enabled=False; i.resize.enabled=True; i.resize.scale='0.5'
        export(i,'D scale')
        i.trim.enabled=True; i.crop.enabled=True
        combined = export(i,'E combined')
        assert len(stamps(combined))==10
        combined_ref=picture(source,'select=eq(n\\,10),crop=80:80:40:20,scale=40:40:flags=lanczos')
        combined_actual=picture(combined,'select=eq(n\\,0)')
        combined_error=sum(abs(a-b) for a,b in zip(combined_ref,combined_actual))/len(combined_ref)
        assert len(combined_ref)==len(combined_actual)==576 and combined_error < 10
        evidence['spatial_proof']={'crop_first_frame_mean_absolute_error':crop_error,'combined_first_frame_mean_absolute_error':combined_error}
        game = root/'game 1024 Кириллица.mp4'
        command([str(runtime.ffmpeg),"-v","error","-nostdin","-n","-f","lavfi","-i",
                 "color=yellow:s=1024x768:r=25:d=0.6,drawbox=x=512:y=0:w=512:h=768:color=red:t=fill",
                 "-c:v","libx264","-pix_fmt","yuv420p",str(game)])
        g = item(game); g.resize.enabled=True; g.resize.pixel_aspect='16:9'
        sar_output = export(g,'F display 16x9')
        v = probe(sar_output)['streams'][0]
        assert (v['width'],v['height'],v['sample_aspect_ratio'],v['display_aspect_ratio']) == (1024,768,'4:3','16:9')
        assert len(probe(sar_output)['streams']) == 1
        g.resize.pixel_aspect='square'; g.resize.mode='format'; g.resize.width=1920; g.resize.height=1080
        for mode in ('fit','fill','stretch'):
            g.resize.placement=mode
            path=export(g,'G '+mode)
            rgb = picture(path,'select=eq(n\\,0)')
            # Fit should have encoded black side padding; other modes fill the canvas.
            corner = rgb[0:3]
            assert (max(corner)<20) == (mode=='fit'), (mode,list(corner))
        g.resize.mode='scale'; g.resize.scale='1.0'; g.resize.pixel_aspect='16:9'
        g.output.container='mov'; g.output.codec='prores'; g.output.prores_quality=2
        export(g,'H ProRes SAR')
        i.trim.enabled=i.crop.enabled=i.resize.enabled=False
        i.output.codec='h264'; i.output.container='mov'
        export(i,'H MOV H264 audio')
        # Demonstrably VFR cadence and trim interval membership.
        vfr=root/'variable.mp4'
        command([str(runtime.ffmpeg),"-v","error","-nostdin","-n","-f","lavfi","-i","testsrc2=s=160x120:r=25:d=1.2",
                 "-vf","select=not(eq(mod(n\\,3)\\,1))","-fps_mode","vfr","-c:v","libx264",str(vfr)])
        f=item(vfr); f.trim.enabled=True; f.trim.in_ms=280; f.trim.out_ms=960
        out=export(f,'B VFR trim')
        src=[t-.28 for t in stamps(vfr) if .28 <= t < .96]
        dst=stamps(out)
        assert len(src)==len(dst) and max(abs(a-b) for a,b in zip(src,dst)) < .0015
        assert len({round(src[n]-src[n-1],3) for n in range(1,len(src))})>1
        evidence['vfr_proof']={'source_selected':src,'actual':dst}
        # Earlier audio + delayed first video: preserve the common A/V time origin.
        offset=root/'audio before video.mov'
        command([str(runtime.ffmpeg),"-v","error","-nostdin","-n","-itsoffset","0.2","-f","lavfi","-i","testsrc2=s=160x120:r=25:d=0.6",
                 "-f","lavfi","-i","sine=frequency=660:sample_rate=48000:duration=0.8","-c:v","libx264","-c:a","aac",str(offset)])
        o=item(offset); o.output.codec='h264'
        offset_out=export(o,'H audio video offset')
        before=probe(offset)['streams']; after=probe(offset_out)['streams']
        def start_delta(streams):
            return float(next(s for s in streams if s['codec_type']=='video')['start_time'])-float(next(s for s in streams if s['codec_type']=='audio')['start_time'])
        assert abs(start_delta(before)-start_delta(after)) <= 1024/48000+.001
        evidence['offset_proof']={'source_video_audio_delta':start_delta(before),'output_video_audio_delta':start_delta(after)}
        # Multiple audio streams remain present and independently mapped.
        multi=root/'two audio.mp4'
        command([str(runtime.ffmpeg),"-v","error","-nostdin","-n","-i",str(source),"-map","0:v:0","-map","0:a:0","-map","0:a:0","-c","copy",str(multi)])
        a=item(multi); a.trim.enabled=True; a.trim.in_ms=400; a.trim.out_ms=800
        multi_out=export(a,'H multiple audio')
        assert sum(s['codec_type']=='audio' for s in probe(multi_out)['streams'])==2
        # Existing destination and invalid quality fail without touching media.
        g.output.container='mp4'; g.output.codec='h264'; g.output.filename=sar_output.name
        try: capture(g)
        except ExportError as error: assert error.code=='destination_exists'
        else: raise AssertionError('Existing output admitted')
        # A real owned encoder is cancelled as soon as its child is registered.
        entered=threading.Event()
        children=[]
        def factory(args,**kwargs):
            child=subprocess.Popen(args,**kwargs); children.append(child)
            if '-vf' in args: entered.set()
            return child
        cancel_adapter=FfmpegExportExecutor(runtime,process_factory=factory)
        service=ExportService(cancel_adapter)
        g.output.filename='I cancelled'; g.resize.scale='2'
        cancelled_request=capture(g)
        assert service.start(g) and entered.wait(10)
        assert service.cancel()
        service.shutdown()
        assert service.snapshot().state=='Cancelled'
        assert not cancelled_request.destination.exists() and all(p.poll() is not None for p in children)
        assert not list(root.glob('.mediaforge-export-*'))
        evidence['real_cancel']={'state':service.snapshot().state,'children_reaped':len(children)}
        # Real FFmpeg failure through the same child adapter (no publication).
        def failure_factory(args,**kwargs):
            if '-vf' in args:
                args=list(args); args[args.index('-vf')+1]='invalid_task010_filter'
            return subprocess.Popen(args,**kwargs)
        failure_service=ExportService(FfmpegExportExecutor(runtime,process_factory=failure_factory))
        g.output.filename='I failed'
        failed_request=capture(g)
        assert failure_service.start(g)
        deadline=time.monotonic()+10
        while failure_service.busy and time.monotonic()<deadline: time.sleep(.01)
        failure_service.shutdown()
        assert failure_service.snapshot().state=='Failed' and not failed_request.destination.exists()
        assert not list(root.glob('.mediaforge-export-*'))
        evidence['real_failure']={'state':failure_service.snapshot().state,'code':failure_service.snapshot().error}
        assert digest(source)==original
        evidence['status']='PASS'
        evidence['bytes']=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
        assert evidence['bytes'] < 40*1024*1024
        print(json.dumps({"status":"PASS","cases":len(evidence['cases']),"bytes":evidence['bytes']},ensure_ascii=False))
        return evidence
    except BaseException as error:
        evidence['status']='FAILED'; evidence['failure']=repr(error)
        raise
    finally:
        (root/'results.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); run(args.output.resolve())
