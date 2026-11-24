# Testing and Evidence

Owner: test maintainer; Architect/PM approves acceptance policy. Use the active task
to select exact checks. AI_WORKFLOWS (private project reference) owns role/closure decisions.
Use the project `.venv/Scripts/python.exe`; PASS requires a completed successful
required check, not test counts alone.

## Verification tiers

| Tier | Coverage | Builder | Independent Reviewer |
| --- | --- | --- | --- |
| 1 Smoke | Import/startup, essential wiring and affected boundaries | When baseline uncertain; after wiring changes | Fresh checks on review target |
| 2 Focused | Changed behavior, adversarial cases and adjacent shared contracts | During iteration and final fix checks | Independent affected tests/counterexamples |
| 3 Native / Integration | Managed processing, actual decoded/painted frames, child/player lifecycle and affected input/device modes | Where mocks/offscreen cannot establish acceptance | Fresh independent affected native gates |
| 4 Full Regression | Complete permanent suite, including native cases | Once on stable final candidate by default | Once independently on final review target by default |
| 5 Release / Milestone | Packaging, clean Windows, broad corpus, devices/DPI and performance | Explicit milestone contract only | Required release claims independently checked |

Documentation/mechanical tasks may explicitly waive product tiers with justification.
Do not rerun a full baseline when applicable accepted evidence exists. Two complete
suites require an explicit nondeterminism concern or acceptance disposition, not habit.
If Tier 4 equivalently executes a required Tier 3 case, do not duplicate it.

A tiny fix does not restart all earlier gates. Map changed code/test/runtime/fixture
identity to affected evidence and rerun it plus justified dependencies. Unchanged
independent evidence can remain applicable; state why. A harness correction requires
diagnosis and affected checks, not automatic restart of unrelated corpus/GUI campaigns.
An unexplained nonzero result blocks acceptance. Preserve it and diagnose before retry;
never retry until green, increase timeouts, skip or weaken assertions to hide a failure.

## Selectors and marker classification

### TASK-012 C0/C1 bounded assignment (Owner-approved, 2026-10-23)

Run portable-root/refusal/isolation, Dub source/track/rational-clock/output-plan
contracts and small native MP4, ProRes MOV, selected MKV and audio probes. Native
probes require explicit `MEDIAFORGE_TEST_RUNTIME_ROOT` pointing to an existing
installation; authenticate both executable hashes using the canonical manifest.
This is read-only test dependency access, never production profile migration.
Use `tests/unit/test_{portable_paths,dub_contracts,dub_process,dub_native}.py`.
Shared changes additionally require foundation/runtime, startup/runtime wiring,
metadata, source/publication/gate/shutdown and affected playback compatibility
checks. Keep basetemps and logs in a new bounded `.checks/task012-*` directory;
preserve failed probes and negative qualification classes. C1 planning/decode facts
do not establish final Dub mux/render preservation or portable ZIP acceptance.
Tier 4 and the entire TASK-011 campaign are explicitly excluded for this checkpoint;
C2–C6, model/provider cache audit, hour case and release qualification remain future.

The current configuration discovers `tests/`; full regression includes costly Windows
native fixture/campaign setup. Use explicit files/node IDs during iteration. For example,
a pure timeline-contract change may select:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_source_presentation_timeline.py
```

This example is not sufficient for a playback/parser change. Document selectors in
the task. Full regression, only when required by its plan:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Process Optimization v2 C2.3 registers four explicit item/parameter markers in
`pyproject.toml`. They describe independent properties and may overlap:

| Marker | Meaning | Current examples / boundaries |
| --- | --- | --- |
| `native` | Real platform GUI/multimedia or native OS-dependent behavior | Windows workspace/window probes; kernel32 GIL watchdog; real hardlinks, kernel-lock/process-death controls and atomic non-overwriting publication collisions/races. Ordinary temporary-file I/O, fake Win32 messages/screens, fake players/audio and synthetic frames alone do not qualify. |
| `integration` | Real runtime/process or substantial cross-component integration | Authenticated FFmpeg/FFprobe cases, controlled interpreter children, isolated `run_python` checks (including import boundaries), offscreen Qt/QML/application wiring and real Bootstrap composition. Narrow in-process fake transport/adapter contract tests remain unmarked. |
| `heavy` | Demonstrated significant runtime/storage cost | Ten native workspace items identified by the accepted C2 cost audit: layout, product long-CFR, Trim paint/admission, wheel, PC3 closure and PC2 frame/workspace/index/origin parameters. A long timeout, filename or cumulative module time alone does not qualify. |
| `performance` | Explicit measured performance gate | Only the PC2 frame parameter: six sources, 100 steps each, median <=150 ms / p95 <=250 ms. Product long-CFR timing diagnostics, readiness deadlines, shutdown bounds and watchdogs are correctness checks, not performance gates. |

Use Boolean expressions to select properties, for example:

```powershell
.\.venv\Scripts\python.exe -m pytest --collect-only -q -m "native"
.\.venv\Scripts\python.exe -m pytest --collect-only -q -m "not native and not heavy"
.\.venv\Scripts\python.exe -m pytest --collect-only -q -m "integration and not native"
.\.venv\Scripts\python.exe -m pytest --collect-only -q -m "native and integration and heavy and performance"
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_source_timeline_indexer.py -m "integration and not native"
```

Collection-only selection does not execute fixtures or native harnesses. The final
example executes small real-child and managed MOV checks; it requires the existing
authenticated Windows runtime. Inspect selected node IDs before executing a broad
filter. `not native` still includes real processes/offscreen Qt and is not a pure
unit-suite guarantee. `native` also includes inexpensive OS checks without a GUI.
`heavy` is a cost label, not an automatic skip or storage budget approval.

All four markers on a single item mean that every property applies; `or` unions
items without executing an overlapping item twice. PC2 matrix parameters retain
their original IDs: frame has all four markers, workspace/index/origin have
native+integration+heavy, dispatch has native+integration. Publication collision
parameters use native only for actual collisions; zero-collision naming controls
remain unmarked. Markers do not transfer through calls/imports to fake wrapper tests.

These labels preserve default full `tests/` discovery, assertions, fixture semantics,
timeouts, sample counts and native oracles. No global `-m` filter, automatic skip,
dynamic classification hook or CI change is installed. The existing tiers and
task-selected acceptance requirements remain authoritative; a filtered run is not
full regression. Heavy labels use the audit's observed runtime/storage and current
C2.1 dependencies, not a universal ten-second threshold or a fresh benchmark.
PC3's failed 17.1-second audit call is a lower-bound cost observation, not a PASS;
historical non-PASS evidence and the `.checks` storage warning remain active.

During the next already-required campaign, use `--durations=20`; distinguish setup,
call and teardown and record fixture generation separately. Review dominant-cost cases
and setups over roughly 10 seconds; this is a tuning signal, not a product threshold.

## Fixture and oracle policy

Future generation should be lazy/case-specific. Reuse immutable public synthetic fixtures
only after recipe, runtime and content identity validation; keep writable profiles,
outputs and process state fresh per run. Avoid generating long/high-resolution media
for a case that needs only a small source. Reviewer validates shared fixture identity
and uses independent behavioral oracles; reuse does not substitute Builder conclusions.

Preserve decoded-image/ordinal coherence, actual painted-scene checks where required,
stale ownership probes and watchdog negative controls. Offscreen/fake tests cannot prove
physical audible output, native destruction or painted-frame exclusion. Native loops
must have bounded completion and cleanup; a historical non-recurrence does not establish cause.

### C2.2 optional synthetic source reuse

The native workspace fixture defaults to fresh generation. Opt in by setting
`MEDIAFORGE_TEST_SOURCE_CACHE` to an absolute test-owned directory (spaces/Unicode
are supported), for example an ignored `.checks/<run>/synthetic-cache` directory.
Unset the variable to exercise the original fresh path. Only `long60.mp4` and
`practical.mp4` may be reused; all other sources and dependencies remain lazy/fresh.
No markers, discovery filters or native oracles change.

[The test helper](../tests/synthetic_source_cache.py) keeps two live source slots.
Every access hashes both managed executables against current manifest pins, compares
exact recipe parameters and helper implementation hashes, and validates source
size/SHA-256 plus FFprobe video/audio/container properties (including count, rate,
dimensions, codecs and start/duration). It never reuses a runtime authentication
result or trusts a filename/report. A recipe/runtime/implementation change invalidates
its slot. This is local synthetic test data, not a production or hostile-writer cache.

A per-source kernel lock serializes validation, generation, publication and copy
across processes; the default lock deadline is 700 seconds. The OS releases it on
process death. Generation/probe deadlines remain bounded. Complete read-only media
and manifest are published by one same-filesystem directory rename. Invalid entries
are renamed to unique `.invalid-*` evidence directories; abandoned `.partial-*`
entries are ignored and retained. Recovery emits explicit warnings and per-run
diagnostics. Launch/probe/generation/copy/lock failures fail the requesting test;
they never silently fall back to an unvalidated source. Retention remains separately
authorized; the cache bounds live eligibility, not total retained historical bytes.

Consumers get independent verified ordinary copies in their fresh writable run
directory, never a cache path or hardlink. Existing run files are not overwritten.
`native-source-generation.json` retains legacy output byte/time accounting and adds
`cache` cold/warm identities, encode/copy timings, diagnostic messages and
`encoded_bytes` (zero for a warm hit). `generated_bytes` counts logical run output
bytes, including copies, and does not represent physical writes saved. Warm reuse
still incurs executable/content hashing, semantic probing and a complete copy.
Measure those costs; never infer full-suite speedups from encode time alone.

Focused selectors: `tests/unit/test_native_sources.py` and
`tests/unit/test_synthetic_source_cache.py`. The latter uses tiny sentinels for
faults/concurrency; its Windows hardlink rejection probe requires an execution
environment that permits creating test-owned hardlinks. Actual recipe cold/warm
timings and existing native decoder/index paths are separate required C2.2 evidence.

## Campaign evidence and retention

### C2.4 native painted-scene storage

`tests/native_trim_paint.py` selects storage once when constructing a
`ScenePaintOracle`, before any readback. `MEDIAFORGE_TEST_CAPTURE_STORAGE` accepts
exactly `raw+png` (the default when unset) or `png-only`. Empty, misspelled,
case-changed or whitespace-padded values fail explicitly. This test-only setting
does not affect production startup, other screenshots, fixture reuse or markers.
For a bounded, storage-preflighted native selector, opt in with:

```powershell
$env:MEDIAFORGE_TEST_CAPTURE_STORAGE = 'png-only'
.\.venv\Scripts\python.exe -m pytest -q tests/presentation/test_workspace_native.py::test_prepublication_trim_admission_native_matrix
Remove-Item Env:MEDIAFORGE_TEST_CAPTURE_STORAGE
```

Both modes retain every capture event and separate full-scene, video-region and
proof-region lossless PNGs, including references and scenes later classified as
failed/unmapped. Default `raw+png` also preserves the historical same-stem `.rgba`
files, filenames and `raw_sha256`/`png_sha256`/`size` fields. Historical bundles,
schema records and raw-dependent verifiers are never rewritten. Selecting
`raw+png` for future runs provides diagnostic raw output/rollback without conversion.

PNG-only capture still hashes the original canonical, tightly packed RGBA8888
pixels. It reloads each saved PNG, reconstructs RGBA8888 and verifies exact bytes,
dimensions, format, stride, byte count and pixel SHA-256 before returning the proof
digest or adding a reference. Encoded PNG-file hashes are separate evidence and
never substitute for painted-picture identity. Journal `storage.version=1` records
mode/status/errors and serialization/roundtrip nanoseconds; each image adds format,
bytes-per-line, canonical byte count, raw presence, roundtrip verification and PNG
byte count/timings. Original geometry/DPR, identities, generations and render/readback
revisions remain intact. External scanline padding is normalized to `width*4`;
ordinary native RGBA8888 capture hashes retain their existing semantics.

Save/read/decode/reconstruction/hash/journal/serialization errors fail closed. The
oracle attempts all regions, retains original captured bytes as `.rgba`, keeps
suspect PNGs and attempts separate `.recovery.png` pictures, an incomplete journal
record and `capture-NNNN-incomplete.json` diagnostics. It returns no accepted digest
and rejects subsequent captures on that oracle. Recovery is diagnostic, never a
successful fallback. Unwritable/exhausted destinations can prevent persistence;
the raised error reports those failures without promising durable files. Images
and the journal close before caller classification/assertions; this is the existing
durability boundary, without a power-loss/fsync guarantee. An incomplete sidecar
invalidates that event even if a failed journal close left a complete-looking row.

Qt signal delivery does not propagate Python slot exceptions to the enclosing
check. `trim_render_checks` therefore retains the first `frameSwapped` capture
exception in its enclosing failure state and checks it, plus the oracle's latched
`storage_error`, after playback/event processing and immediately before returning
success. It re-raises the original exception through the normal Python acceptance
path, preserving its context. Earlier valid pictures or four completed returns
cannot mask a failed later required capture. Its existing signal disconnection
and cleanup boundary remains in `finally`. Small permanent regressions exercise
13 distinct references and a failed final boundary via actual direct/queued QObject
signals in both modes, alongside successful callback controls.

Independent verification of new bundles must first reject incomplete records or
sidecars, authenticate each PNG-file digest, reconstruct RGBA8888 and check exact
dimensions/stride/byte count and canonical pixel hash for all three regions of every
event. Authenticate raw bytes too when `raw_present` is true. Replay the unchanged
paused-reference maps and strict painted-scene membership; timestamps alone are
not picture proof. Use a fresh representation-aware verifier for PNG-only bundles;
historical raw-dependent scripts remain valid for their original bundles.

Focused regressions: `tests/unit/test_native_trim_paint.py`. Native acceptance must
retain affected admission, standalone Trim, long-CFR product and PC2 workspace
oracles, C1 placement/cleanup, counts/timeouts/latency gates and a controlled real
failed/unmapped scene. Preflight projected `.checks` growth before allocation; do
not truncate proof to meet a budget. Measure event counts, source/runtime identity,
per-event serialization/roundtrip cost and logical bytes. Reduced storage alone
does not establish a full-suite speedup. This policy does not authorize cleanup.

Heavy generated artifacts are temporary by default. Recommended future layout:

```text
.checks/<task>/<run-id>/
  campaign.json
  results.json
  logs/
  evidence/
  tmp/
```

Small manifests/results should record owner, candidate identity/fingerprint, contract
revision, commands/exits/duration, runtime and fixture identities, byte totals,
retention state and protected incident IDs. This is a documented format, not a new
runner or mandatory retrofit of historical campaigns.

| Artifact | Retention / Git policy |
| --- | --- |
| Pytest basetemps, generated media, isolated profiles | Keep through needed acceptance/diagnosis; temporary, never commit |
| Raw framebuffer dumps | Diagnostic-only by default; protect required failed scenes until disposition; normally never commit |
| Screenshots | Commit selected small authoritative public-safe captures; intermediate/duplicate captures temporary |
| JSON/JSONL journals, native diagnostics | Commit concise outcomes/critical incident facts; repetitive traces temporary |
| Failed runs | Preserve truthful outcome, traceback, reproduction and essential evidence; protect heavy bundle until closure/disposition |
| Temporary scripts | Temporary unless approved as maintained reproduction/regression tooling |
| Accepted reports/evidence | Concise report, critical public-safe proof and reproduction/provenance information |
| Private footage, local profiles, runtime binaries, caches | Never commit; user sources are not test-owned artifacts |

Do not erase/relabel failed runs as PASS. Before expiry, retain the facts needed to
understand their disposition and reproduce meaningful defects. Successful repetitive
captures can use event/hash summaries, but required failure scenes must survive assertions.
Budget control must not silently truncate required proof or weaken exact oracles.

| Initial threshold | Action |
| --- | --- |
| 1 GiB per campaign | Warn and review capture/fixture forecast |
| 2 GiB per campaign | Pause allocation unless larger budget pre-approved |
| 5 GiB total .checks | Warn and request ownership/retention review |
| 10 GiB total .checks | Pause new heavy allocations |
| 10 MiB committed evidence per task | Target; justify larger authoritative package |

These are warning/pause thresholds, NOT deletion authorization. They are policy only;
automatic accounting/enforcement and cleanup tooling are deferred.

For routine cleanup, wait for accepted merge or documented closure. Promote small
authoritative evidence, verify provenance and live references, establish that
generated resources are inactive, and obtain authorization for the exact paths.
The final workspace cleanup (private project reference) (original: `../reports/FINAL_WORKSPACE_CLEANUP_MANIFEST.json`)
is a one-time pre-release exception and requires a current checkpoint and the
listed custody and dependency checks before removal. Use the
retention rule (private project reference).

Suggested eligibility remains 7 days for successful temporary runs and 30 days
for disposed failure bundles. Preserve unresolved incidents. Age alone
authorizes nothing. Cleanup tooling must default to dry-run, validate resolved
containment, reject unexpected tracked, unknown or active paths and Windows
reparse points, and record receipts.

## Secondary-monitor native policy

For automated/native harnesses only, prefer an available NON-PRIMARY QScreen using
QGuiApplication.primaryScreen() and screens(). Never hard-code Display1/Display2.
An optional local preference may refine selection among current non-primary screens;
otherwise use a documented deterministic tie-break. One-screen systems use the available
screen gracefully; absence of a secondary never fails a case unless explicitly required.

Set the selected screen AND explicitly place the window within availableGeometry().
Account for negative desktop coordinates, taskbars, decorations and DPR. Place before
first visible exposure where possible; keep companion/transient windows on that screen,
reassess disconnects, and report/skip a size-specific scenario when no screen fits it.
Qt notes that setScreen alone does not move a window on a virtual desktop:
[Qt 6.8 QWindow](https://doc.qt.io/qt-6.8/qwindow.html#setScreen).

Use targeted QTest/QWindow events where possible; avoid global cursor/input manipulation.
Avoid requestActivate except activation-sensitive tests, which retain their real
activation/negative controls. Run native GUI campaigns serially. Close windows and
release player/audio/children in finally. Attempt focus restoration only when it will
not override newer user activity. Windows/Qt cannot guarantee zero focus steal;
this policy minimizes disruption. It MUST NOT change normal end-user window behavior.
Phase C1 implements this policy in [native_window_policy.py](../tests/native_window_policy.py)
and the maintained native workspace/PC2/PC3/Trim and visible runtime harnesses.
Production startup/QML never imports the helper. Offscreen harness loading is unchanged.

Selection filters for the exact requested logical client size plus actual native
frame margins, then prefers fitting non-primary screens. The deterministic tie-break
is screen name followed by available x/y/width/height. If no secondary fits, a fitting
primary is allowed. No fitting screen produces `NATIVE SCREEN SKIP:` and process exit
77; native pytest wrappers recognize both and report SKIP, never PASS. Other nonzero
results remain failures. Windows/Qt geometry is in logical pixels; DPR is recorded,
not multiplied into desktop coordinates.

The test loader supplies `visible: false` through engine initial properties, creates
the hidden native handle for decoration metrics, sets screen and centers the decorated
frame in available geometry, then shows it. Companion placement preserves existing
native ownership/activation semantics. QWindow transients inherit the owner's screen
on Show; in-window QML popups share their root. Screen add/remove, primary changes and
available-work-area changes trigger rediscovery. Reassessment failures close owned test
windows and remain explicit non-PASS outcomes. External applications such as Explorer
are outside QScreen placement control.

Native test processes acquire `.checks/native-gui.lock` with QLockFile. A simultaneous
campaign is rejected with a failure instructing serial execution; the lock is released
at process exit. This guard does not schedule campaigns or change test tiers. The helper
never requests activation, moves global input or restores focus: without reliable evidence
of newer user activity, restoration could itself override the user. Activation-sensitive
input scenarios explicitly retain their activation and negative controls. Harness finally
blocks close companion/root windows alongside existing model/player/child cleanup.

C1 focused verification selects `tests/unit/test_native_window_policy.py`,
`tests/presentation/test_secondary_monitor_native.py` and the unchanged startup regressions.
The small native probe covers real secondary placement, minimum/reference dimensions,
companion/transient placement, activation controls, fullscreen/maximized restoration and
serial exclusion without generating media. Run affected existing native scenarios serially
when validating integration; retain their exact behavioral/painted-frame oracles.

## Local-only environment

Optional ignored `.local/environment.md` and `.local/native-tests.json` may contain
monitor preference, Windows/GPU facts, private corpus paths, model/tool configuration
and necessary developer absolute paths. Date discovery facts and revalidate when needed.
Do not put credentials there. No machine-specific files are committed/created by A+B.
Normal tests/CI must work without local context; public defaults use discovery.
For C1, the optional `native-tests.json` field is `{"screen_name": "current Qt screen name"}`.
It refines only fitting non-primary selection; missing/stale names fall back to discovery,
and invalid optional configuration is diagnosed and ignored. C1 creates no local preference
file and adds no local-path requirement to tests.

## Future performance work

Implement benchmarks only under a separate approved contract, eventually in
`tests/performance/`. Keep public recipes in Git and private corpus paths/media local.
Consider 1080p/4K H.264, supported HEVC, ProRes, 6K and VFR. Measure command-to-first
admitted visible frame, seek/step-to-correct painted settlement, play startup,
EOF/Trim return latency, indexing time and memory where relevant. Record cold/warm
state, fixture/runtime/GPU/screen/power context, sample count, median/p95 and failures.
Compare baseline/candidate under equivalent conditions; correctness remains prerequisite.
Existing fixture-specific latency limits are not universal high-resolution guarantees.
