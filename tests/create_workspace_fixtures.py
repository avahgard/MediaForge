"""Create ignored TASK-008 fixtures with the already provisioned managed runtime."""

import argparse
from pathlib import Path
import shutil
import subprocess

from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    runtime = resolve_ffmpeg_runtime()
    for name, size, rate, vfr in (
        ('interview_final.mov', '1920x1080', '25', False),
        ('vertical.mp4', '360x640', '25', False),
        ('clip_02.mp4', '640x360', '24000/1001', False),
        ('variable.mp4', '640x360', '25', True),
    ):
        command = [str(runtime.ffmpeg), '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate={rate}:duration=6',
                   '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=6']
        if vfr:
            command += ['-vf', r'select=not(mod(n\,2))+not(mod(n\,5))', '-fps_mode:v', 'vfr']
        command += ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', str(directory / name)]
        subprocess.run(command, check=True, timeout=60)
    for folder, name in (('Path with spaces', 'audio video copy.mp4'),
                         ('Папка Кириллица', 'видео со звуком.mov')):
        target = directory / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(directory / 'interview_final.mov', target)
    print(directory)


if __name__ == '__main__':
    main()
