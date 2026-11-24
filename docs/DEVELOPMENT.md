# Development and recovery

Use Windows x64 and Git for Windows. The validated development baseline is CPython
3.12 x64. [Project metadata](../pyproject.toml) declares Python 3.12 and 3.13.
Use the repository virtual environment. Set up a new environment only when needed.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tools/requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .
$env:MEDIAFORGE_DATA_ROOT = Join-Path (Get-Location).Path '.local\developer\Data\v2'
.\.venv\Scripts\python.exe -m mediaforge
```

The [FFmpeg runtime guide](FFMPEG_RUNTIME.md) documents separate provisioning,
verification and the development override. The [image codec guide](IMAGE_OUTPUT_CODECS.md)
records codec pins and notices. Test from an explicitly isolated profile when
required by the selected test or tool. Avoid the Owner installation and private footage.

For changes affecting the named boundaries, a focused starting selector is:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q tests/unit/test_foundation.py tests/unit/test_portable_paths.py tests/unit/test_portable_preferences.py
```

Select actual tests by changed behavior. For runtime trust, model acquisition,
publication or native Qt behavior, use the applicable integration/native cases
under [Testing](TESTING.md). Preserve permanent regression tests and budget native
fixtures before running them. This command is a development example.

## Portable build and release

The public ZIP uses the [portable bootstrap](PORTABLE_BOOTSTRAP.md). Rebuild it
from the release's [runtime manifest](../packaging/bootstrap-runtime.tsv),
the [pinned FFmpeg archive](../external/manifests/ffmpeg.json) and the matching
source tree. The bootstrap builder verifies the supplied manifest and FFmpeg
archive before assembling the project-only ZIP.

```powershell
$manifest = 'packaging/bootstrap-runtime.tsv'
$manifestHash = (Get-FileHash -Algorithm SHA256 $manifest).Hash.ToLowerInvariant()
.\.venv\Scripts\python.exe -B tools/build_portable_bootstrap.py --output .local/release-rebuild `
  --manifest $manifest --manifest-sha256 $manifestHash --ffmpeg-archive $verifiedVendorArchive
```

Use a new output folder and set `$verifiedVendorArchive` to the original vendor
ZIP. The following procedure builds a private reference runtime and supplies a
manifest after a dependency selection changes.

For an authorized candidate, use a new writable local test deployment with a
separate profile and fresh receipt paths. The actual tool arguments are the
contract. Read the scripts before running them. [Core lock](../packaging/core-lock.json)
records the pinned private payload inputs.

1. [Acquire core](../tools/acquire_portable_core.py) with `--root --lock --receipt`
   when approved inputs are missing. Acquisition can download pinned assets.
2. [Prepare core](../tools/prepare_portable_core.py) with
   `--root --lock --target --receipt`. It consumes the managed acquired assets
   and unpacks a new private Core.
3. [Build portable shell](../tools/build_portable_shell.py) with
   `--root --unpack-receipt --receipt`. It uses pinned FFmpeg/FFprobe and Windows
   Framework64 `csc.exe` to build the [launcher](../packaging/Launcher.cs).
   The launcher checks the manifest and starts the private Python
   [bootstrap](../packaging/portable_boot.py).
4. [Package candidate](../tools/package_portable_candidate.py) with
   `--root --build-receipt --receipt` and optional `--package-generation` for
   an internal QA ZIP. It selects authenticated members and verifies a clean
   extraction. Public release needs its own manifest, notices and qualification.

The packaged profile is `<executable parent>/Data/v2`. Keep models, mutable data,
logs, local recovery material, development artifacts and private media out of the
binary ZIP. Archive membership follows the public release manifest, independent
of workspace cleanup. Production model acquisition belongs to the app's explicitly
confirmed component flow.

## Recovery

The dependency lock provides the test and Qt environment. The editable install
also resolves the exact NumPy, imagecodecs and tifffile pins in pyproject.
For authenticated wheel recovery, use the pinned hashes in the image codec
guide and Core lock. Install into this checkout's own virtual environment.
The validation described in the handoff used an external interpreter only as
a dependency provider.

From the checkout, keep MEDIAFORGE_DATA_ROOT set to the writable absolute
developer profile shown above. Run tools/setup_ffmpeg.py through the project
interpreter to explicitly acquire the pinned binaries, then run the same tool
with --verify to authenticate them offline. Media processing needs them. Shell
navigation and installation-free tests can run before provisioning. Optional Dub
components remain absent until the supported Settings consent and install flow.
A portable candidate additionally needs the pinned private Core inputs and
the Windows Framework64 compiler named by the shell builder. See the
[cleanup handoff](CLEANUP_HANDOFF.md) for transfer and excluded assets.
