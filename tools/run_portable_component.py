"""Developer native integration using the built private core, never .venv.

The supplied core digest is the independently recorded compiled build identity.
This is a developer entry, not an additional command accepted by MediaForge.exe.
"""
import argparse
from pathlib import Path
import runpy
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--action',choices=('runtime','synthesis','dub','voices','playback','words','observation','observation-join'),required=True)
    parser.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args();root=args.app
    if (not root.is_absolute() or Path(sys.executable).parent!=root/'Core/Python'
            or len(args.manifest_sha256)!=64):parser.error('Use the actual built private CPython and frozen core identity.')
    sys.dont_write_bytecode=True
    sys.path[:0]=[str(root/'Core/App'),str(root/'Core/Site'),str(Path(__file__).resolve().parent)]
    sys.mediaforge_portable_executable=str(root/'MediaForge.exe')
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    paths=ProfilePaths.from_environment();paths.activate_temporary()
    from mediaforge.infrastructure.models.core_runtime import CoreRuntime
    guard=CoreRuntime(root,args.manifest_sha256);sys.mediaforge_core_guard=guard
    scripts={'runtime':'probe_dub_qwen.py','synthesis':'synthesize_dub_qwen.py',
             'dub':'run_dub_final.py','voices':'qualify_dub_voices.py','playback':'qualify_voice_playback.py',
             'words':'observe_dub_words.py','observation':'qualify_dub_observation.py',
             'observation-join':'qualify_native_observation_join.py'}
    script=Path(__file__).with_name(scripts[args.action])
    arguments=args.arguments
    if arguments and arguments[0]=='--':arguments=arguments[1:]
    sys.argv=[str(script),*arguments]
    try:runpy.run_path(str(script),run_name='__main__')
    finally:guard.close()


if __name__=='__main__':main()
