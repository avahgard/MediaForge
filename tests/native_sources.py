"""Fixed native workspace recipes, generated only for the requesting case.

One store belongs to one module run and a fresh pytest directory. Optional
cross-run reuse is confined to two validated synthetic sources; consumers always
receive independent writable copies. Existing run files never count as hits.
"""

import json
import subprocess
import time

from tests.late_timing_fixture import (
    create_cfr_control_fixture,
    create_late_timing_from_control,
)


BASE_RECIPES = {
    "integral.mp4": ("25", False),
    "fractional.mp4": ("24000/1001", False),
    "variable.mp4": ("25", True),
    "long60.mp4": ("60", False),
    "thirty.mp4": ("30", False),
    "practical.mp4": ("25", False),
}
DEPENDENCIES = {
    **{name: () for name in BASE_RECIPES},
    "cfr-control.mp4": (),
    "late-vfr.mp4": ("cfr-control.mp4",),
    "origin5.mp4": ("integral.mp4",),
}
MODE_SOURCES = {
    "integral": ("integral.mp4",),
    "thirty": ("thirty.mp4",),
    "fractional": ("fractional.mp4",),
    "unreliable": ("variable.mp4",),
    "layout": ("integral.mp4", "fractional.mp4", "variable.mp4"),
    "icons": ("integral.mp4",),
    "product": ("integral.mp4", "long60.mp4"),
    "input": ("thirty.mp4",),
    "late-timing": ("integral.mp4", "cfr-control.mp4", "late-vfr.mp4"),
    "trim-boundary": ("integral.mp4",),
    "wheel-fallback": ("long60.mp4",),
}
SCRIPT_SOURCES = {
    "pc3_closure_native.py": ("integral.mp4",),
    "trim_admission_native.py": ("integral.mp4", "fractional.mp4"),
    "pc2_frame_native.py": ("integral.mp4", "fractional.mp4", "long60.mp4",
                            "practical.mp4", "variable.mp4", "late-vfr.mp4"),
    "pc2_workspace_native.py": ("integral.mp4",),
    "pc2_index_native.py": ("practical.mp4", "integral.mp4", "variable.mp4", "late-vfr.mp4"),
    "pc2_origin_shutdown_native.py": ("integral.mp4", "origin5.mp4", "practical.mp4"),
    "pc2_dispatch_native.py": ("integral.mp4",),
}


class NativeSources:
    def __init__(self, directory, runtime, *, cache_directory=None):
        self.directory = directory
        self.runtime = runtime
        self._completed = set()
        self._failures = {}
        self.generation = []
        self.cache_directory = cache_directory
        self.reuse = []

    def for_mode(self, mode):
        return self.require(*MODE_SOURCES[mode])

    def for_script(self, script):
        return self.require(*SCRIPT_SOURCES[script])

    def require(self, *names):
        # Validate the entire request before allocating anything for a typo.
        for name in names:
            if name not in DEPENDENCIES:
                raise KeyError(name)
        for name in names:
            self._ensure(name)
        return self.directory

    def _ensure(self, name):
        if name in self._completed:
            return
        if name in self._failures:
            raise self._failures[name]
        for dependency in DEPENDENCIES[name]:
            self._ensure(dependency)
        started = time.perf_counter()
        outputs = [self.directory / name]
        if name == "late-vfr.mp4":
            outputs.append(self.directory / "late-vfr-fixture.json")
        succeeded = False
        failure = None
        try:
            self._generate(name)
            self._completed.add(name)
            succeeded = True
        except Exception as error:
            # Preserve partial evidence and fail again without regenerating it.
            self._failures[name] = error
            failure = str(error)
            raise
        finally:
            self.generation.append({
                "recipe": name, "dependencies": list(DEPENDENCIES[name]),
                "seconds": time.perf_counter() - started, "succeeded": succeeded,
                "outputs": {path.name: path.stat().st_size for path in outputs if path.exists()},
                "failure": failure,
            })

    def _generate(self, name):
        directory, runtime = self.directory, self.runtime
        if self.cache_directory is not None and name in ("long60.mp4", "practical.mp4"):
            from tests.synthetic_source_cache import SyntheticSourceCache

            self.reuse.append(SyntheticSourceCache(self.cache_directory, runtime).materialize(
                name, directory / name, self._base_command(name, directory),
                600 if name == "practical.mp4" else 150,
            ))
            return
        if name == "cfr-control.mp4":
            create_cfr_control_fixture(directory, runtime)
            return
        if name == "late-vfr.mp4":
            create_late_timing_from_control(directory, runtime)
            return
        if name == "origin5.mp4":
            result = subprocess.run([str(runtime.ffmpeg), "-hide_banner", "-loglevel", "error",
                "-i", str(directory / "integral.mp4"), "-c", "copy", "-output_ts_offset", "5",
                str(directory / "origin5.mp4")], capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            return
        command = self._base_command(name, directory)
        result = subprocess.run(command, capture_output=True, text=True, timeout=600 if name=='practical.mp4' else 150)
        assert result.returncode == 0, result.stderr

    def _base_command(self, name, directory):
        runtime = self.runtime
        rate, vfr = BASE_RECIPES[name]
        size, duration = ('1280x720',600) if name=='practical.mp4' else ('1024x768', 180) if name == 'long60.mp4' else ('320x180', 4)
        command = [str(runtime.ffmpeg), "-hide_banner", "-loglevel", "error",
                   "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={duration}",
                   "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
        if vfr:
            command += ["-vf", r"select=not(mod(n\,2))+not(mod(n\,5))", "-fps_mode:v", "vfr"]
        command += ["-c:v", "libx264", "-preset", "veryfast" if name=='practical.mp4' else "ultrafast", "-g", "250", "-bf", "2", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(directory / name)]
        return command

    def write_generation(self):
        """Small per-run accounting; timings exclude dependency generation."""
        (self.directory / "native-source-generation.json").write_text(json.dumps({
            "recipes": self.generation,
            "generated_bytes": sum(sum(record["outputs"].values()) for record in self.generation),
            "generation_seconds": sum(record["seconds"] for record in self.generation),
            "cache": self.reuse,
            "encoded_bytes": sum(sum(record["outputs"].values()) for record in self.generation
                                 if record["recipe"] not in {hit["recipe"] for hit in self.reuse})
                             + sum(hit["encoded_bytes"] for hit in self.reuse),
        }, indent=2), encoding="utf-8")
