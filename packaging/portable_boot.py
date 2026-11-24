"""Private CPython entry, selected only by the manifest-authenticated launcher."""
from time import perf_counter

startup=[('Python entry', perf_counter())]
import os
from pathlib import Path
import sys

try:
    begin, verified, spawning, frequency = map(int, os.environ.pop('MEDIAFORGE_LAUNCH_TIMING','').split(':'))
    if 0 < begin <= verified <= spawning and frequency > 0 and spawning / frequency <= startup[0][1]:
        startup[:0]=[('launcher entered',begin/frequency),('launcher verified core',verified/frequency),
                     ('launcher creating Python',spawning/frequency)]
except ValueError:
    pass

core=Path(__file__).resolve().parent
runtime=core.parent
executable=Path(sys.argv[1]) if len(sys.argv)==3 else None
if (executable is None or not executable.is_absolute() or executable.name.casefold()!='mediaforge.exe'
        or (executable.parent!=runtime and executable.parent/'Runtime'!=runtime.parent)
        or Path(sys.executable).parent!=core/'Python'):
    raise SystemExit('Invalid portable deployment. Launch MediaForge.exe from its extracted folder.')
sys.path[:0]=[str(core/'App'),str(core/'Site')]
sys.dont_write_bytecode=True
sys.mediaforge_portable_executable=str(executable)
sys.mediaforge_portable_runtime=runtime
for name in tuple(os.environ):
    if name.startswith(('PYTHON','QML_','QT_PLUGIN','QT_QPA_PLATFORM_PLUGIN','QSG_RHI_PIPELINE_CACHE_')):
        os.environ.pop(name,None)
os.environ.update(QML_DISABLE_DISK_CACHE='1',QT_DISABLE_SHADER_DISK_CACHE='1',
    QSG_RHI_DISABLE_SHADER_DISK_CACHE='1',QT_PLUGIN_PATH=str(core/'Site'/'PySide6'/'plugins'),
    QML_IMPORT_PATH=str(core/'Site'/'PySide6'/'qml'))
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
profile=ProfilePaths.from_environment();profile.activate_temporary()
startup.append(('portable profile activated', perf_counter()))
from mediaforge.infrastructure.models.core_runtime import CoreRuntime
guard=CoreRuntime(runtime,sys.argv[2]);sys.mediaforge_core_guard=guard
startup.append(('Python core guard authenticated', perf_counter()))
from mediaforge.bootstrap.application import main
result=main([str(executable)],prepared_profile=profile,startup=startup)
if result==0:guard.close()
raise SystemExit(result)
