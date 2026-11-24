# Development FFmpeg runtime

TASK-003 pins Gyan.dev Release Essentials Windows x64 9.0.2 in
`external/manifests/ffmpeg.json`. Version/provider/hash changes require a new
Architect decision. The packaged manifest is `external/manifests/ffmpeg.json` beside the validated
deployed executable; the shipped pair lives under `external/ffmpeg/` using the
manifest's member paths. Checkout manifest lookup is development-only. Packaging
qualification is separate; absent/tampered shipped assets fail closed.

After installing the project's development environment, run from the checkout:

```powershell
.\.venv\Scripts\python.exe tools/setup_ffmpeg.py
.\.venv\Scripts\python.exe tools/setup_ffmpeg.py --verify
.\.venv\Scripts\python.exe tools/check_ffmpeg_runtime.py --output .checks/task003-real
```

The first command downloads the exact versioned ZIP, locally hashes its bytes,
compares the pinned literal SHA-256, extracts only the executable pair and
available upstream license/readme into staging, and verifies both executable
SHA-256 hashes against project manifest pins before launching either `-version`
check and publishing. The second command never uses the network. A valid
existing runtime is verified without another download. Invalid existing runtime
fails explicitly and is not overwritten automatically.

`ffmpeg_sha256` and `ffprobe_sha256` were independently computed from entries in
the ZIP after authenticating its exact pinned archive hash. Every managed-runtime
resolution (including repeat setup) hashes both actual executables before running
either one. Modified bytes fail even if their version banner is unchanged.
Missing/invalid hash pins and unreadable/mismatching files fail with controlled
Infrastructure errors. Cache-side metadata is not trusted; the retained archive
is optional for normal reuse because the project manifest holds the trusted
executable hashes. The acceptance audit still requires the retained ZIP.

The third command independently rehashes the retained archive, executes both
version commands, creates one-second synthetic video fixtures with/without audio,
and runs the existing metadata workflow through the composition root. It checks
ordinary, spaced and Cyrillic paths, and writes `evidence.json` under the output
directory. Fixture generation is acceptance tooling only, not a processing feature.

Development requires explicit `MEDIAFORGE_DATA_ROOT` (absolute isolated root);
set it before setup/check/launch. No prior AppData install is auto-imported. Mutable
provisioning uses the portable v2 root:

```text
<MediaForge folder>/Data/v2/cache/ffmpeg/<runtime_id>-<version>-<sha256>/
  ffmpeg-9.0.2-essentials_build.zip
  ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe
  ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe
```

Resolution does not depend on the working directory. Tests use isolated profile
paths, including spaces and Cyrillic. Archives/executables are ignored by Git.
The archive is retained for independent local hash verification. Cache deletion
requires explicitly provisioning again. No startup download occurs; shell and
the existing controlled metadata error path remain available without runtime.

Only the versioned Gyan.dev URL and the authorized GyanD GitHub mirror from the
manifest are used. Mirror retry happens only on network/source-read failure.
Local creation, write, flush/close or hash-read failures terminate setup without
contacting the mirror. A completed
download with a mismatching hash is a hard failure; the checksum endpoint cannot
replace the expected hash. Neither system PATH nor Legacy locations are searched.

The existing development-only `MEDIAFORGE_FFPROBE_PATH` override is retained.
It is rejected in packaged mode. It must be an absolute path named `ffprobe.exe`, with companion `ffmpeg.exe` in
the same directory. Both must pass the same exact pinned banner verification.
An invalid override fails without falling back to managed runtime. The override
does not download, modify the managed cache, or prove archive provenance.
Project manifest executable hashes apply only to managed runtime. Override
validation remains a separate development policy based on executable/version
checks, and is not an authenticated managed installation. Use
the project-managed installation for acceptance. Leave this variable unset for
normal operation. The setup command always targets managed runtime and ignores
this override.

Infrastructure owns manifest, paths, provisioning and verification. Bootstrap
injects the validated FFprobe path. Metadata adapter, Application, Feature,
Presentation and QML do not discover or download runtime assets.
