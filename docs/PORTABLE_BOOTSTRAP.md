# Portable setup and manual updates

Extract the complete ZIP to a writable local Windows 10 or 11 x64 folder and
open `MediaForge.exe`. Allow one-time setup to download and verify about 397 MiB
from PyPI, GitHub and Gyan. Keep at least 1.2 GiB free for the download and staged
runtime. Setup opens MediaForge after verification. Later Media launches work
offline. Optional Dub components are acquired separately through Settings.

Cancel setup to stop the current transfer. If a transfer or setup is interrupted,
launch the executable again and confirm. Setup reuses downloads whose exact size
and SHA-256 verify. The current incomplete transfer restarts. An interrupted
runtime is prepared again before it becomes active. For a damaged download,
remove only the file named in the error. For a damaged installed runtime, close
MediaForge, move the affected version folder out of `Runtime`, then launch again.
Keep the portable folder path short for Windows components with legacy path limits.

## Manual update

Close MediaForge, back up `Data/v2` and extract the new ZIP into a separate
folder. Copy `Data/v2` to the new folder to preserve settings, compatible models
and persistent data. Copy `Runtime/Cache` if you want to reuse the original
downloads. The new version authenticates and uses only cache entries it pins.
Keep the old installation and its own copy of Data until you verify the new
version and your model availability in Settings. Runtime version folders belong
to their original release. Keep the backup for rollback.

## Source and runtime licenses

The ZIP contains the GPL-3.0-only MediaForge launcher executable and Python application source,
including separately licensed vendored source with its notices. The GitHub
source repository and matching tag supply the launcher sources and build
scripts under `packaging/` and `tools/`. The sealed `Bootstrap/Inputs.tsv`
identifies the original third-party archives. The bootstrap connects directly
to the listed vendors after consent. MediaForge hosts the application ZIP and
its source. Third-party runtime binaries are obtained from their upstream
distributors under their own terms. The setup preserves the applicable
license files from the downloaded distributions. [Third-party notices](THIRD_PARTY_NOTICES.md)

Release producers attach the matching MediaForge source archive alongside the
ZIP. It contains the exact runtime manifest, locks, launcher source, and build
and packaging scripts. Any separately redistributed runtime requires its own
license and corresponding-source review. Upstream acquisition does not grant
permission to rehost those binaries or to grant rights in the vendors' code.
