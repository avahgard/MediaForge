# Project Invariants

## TASK-014 Owner-approved extension — 2026-11-05/06 (implementation WIP)

The integrated TASK-014 assignment preserves INV-PUBLICATION-01's strict Dub
Same as Source meaning and adds an explicitly selected **Compatible MP4** route:
qualified original opaque SDR pictures are encoded as H.264 with one complete
Russian AAC track. It is never a silent substitution under Same as Source.
Its independent encoding oracle proves picture span/cadence, geometry/orientation,
qualified color signaling, decodability and complete audio; packet-byte equality
remains mandatory for copying, not for a disclosed encode. Unqualified critical
metadata/extras/color/orientation and unsafe ownership still refuse. All existing
no-replace publication, source leases, runtime/native/resource and retirement
requirements remain unchanged. This is implementation authority, not acceptance.

INV-WORKSPACE-01 / INV-PLAYBACK-01 clarification: MediaWorkspaceSession owns the
selected physical original shared by Media and Dub. Media edit recipes and Viewer
proxies do not become physical Dub inputs. Both tabs reuse the approved Media SVG
`00 Home` when no source is selected; only loaded Dub uses its setup inspector.
The existing persistent source Viewer remains singular; voice sample audition is
bounded QAudioSink playback, coordinated with source audio. No duplicate player,
import catalog, silent render/import or setting redesign is authorized.

**TASK-011 — CLOSED / OWNER PASS / 2026-10-22.**

Explicit Owner/PM acceptance covers the qualified cumulative Media implementation,
including the final duplicate Fullscreen exit-button correction. Accepted pre-commit
source/test/pyproject fingerprint (113 paths):
`5786be23a5dc8beacf4157cbddc2229771f7d3cd40c48d31408e8586eb332d19`.
The acceptance checkpoint (private project reference) (original: `../reports/TASK-011_OWNER_ACCEPTANCE_CHECKPOINT.md`)
records the complete positive staging manifest, evidence, support limits and Git
preservation gates. Earlier dated WIP/open statements below are historical stage
records superseded by this acceptance; their technical findings are preserved.
TASK-011 closure remains unchanged. Owner-approved TASK-012 C0/C1 is now the active
bounded assignment: portable governance, typed Dub source/output contracts and source
eligibility only. No C2/provider/QML/final-output implementation is authorized here.
Authority: final architecture amendment (private project reference) (original: `../reports/TASK-012_ARCHITECT_FINAL_AMENDMENT.md`)
and FINAL design handoff (private project reference).


Owner: Architect. These IDs describe accepted behavior through TASK-011, including its dated amendments.
An approved task changing an invariant must identify its ID, replacement semantics
and required regressions; update this document when that change is accepted. Otherwise
preserve it. Historical dispositions supply provenance, not competing current rules.
Pointers below are maintained implementation/test contracts, not a requirement to read
every linked file for every task.

## INV-PROFILE-01 — Portable application storage

Owner-approved TASK-012 C0 replacement, 2026-10-23: packaged Windows x64 onedir
MediaForge derives `<actual executable parent>/Data/v2` from a normalized, validated
local executable identity, independent of cwd, environment profiles and checkout.
Resolution is lazy; before managed writes, validate paths and required create/write/
rename/delete operations. Unwritable or ambiguous/reparse roots refuse with an
actionable extract-to-writable-folder error. Never fall back to AppData, LocalAppData
or system Temp; never automatically import old v2/Legacy state. Development/tests
inject an explicit isolated absolute root, which packaged mode cannot override.
Preserve spaces/Cyrillic/Unicode. One injected ProfilePaths owns config, data, models,
manifests, cache, logs, downloads, sessions and ordinary tmp. Global tempfile and owned
children TEMP/TMP/cwd belong there. Third-party writes must be redirected and verified
before portable qualification; unsupported cache behavior blocks that claim.
Only current final-output transaction staging beside the user-selected destination
is exempt (INV-PUBLICATION-01). No ordinary spools/caches/model staging there.
Complete-folder relocation must retain settings/models and reauthenticate identities;
no zero-OS-traces claim. ZIP/release and third-party portability need native qualification.
Sources: [profile](../src/mediaforge/infrastructure/filesystem/profile.py),
[foundation tests](../tests/unit/test_foundation.py).

## INV-LAYERS-01 — Ownership and dependency boundaries

QML is presentation only and sends commands through bridges/Application contracts.
Application orchestrates Features without QML/concrete execution dependencies.
Features do not depend on Qt presentation, QThread or concrete Infrastructure.
Infrastructure implements ports without UI logic; Bootstrap composes concrete objects.
Presentation must not import internal Feature engines or bypass Application contracts.
Qt playback presentation remains owned by the accepted controller, not a Feature engine.
Sources: [baseline](ARCHITECTURE_BASELINE.md),
[Bootstrap](../src/mediaforge/bootstrap/application.py),
[boundary regressions](../tests/presentation/test_resize_bridge.py).

## INV-RUNTIME-01 — Deterministic external runtime

Reuse the sole injected FFmpeg/FFprobe runtime pair. Managed resolution authenticates
both executable byte hashes before launch using the pinned manifest. No system PATH,
Legacy search, silent substitution or startup download. The explicit development
override is separately validated and is not authenticated managed acceptance evidence.
Packaged manifest and shipped pair resolve from the validated executable parent;
mutable provisioning belongs under the injected portable root. Checkout-relative
manifest resolution is development-only, never packaged fallback. Missing/tampered
assets refuse; cwd and PATH supply no alternative.
Version/provider/hash changes require Architect approval; binaries stay outside Git.
Sources: [runtime policy](FFMPEG_RUNTIME.md), [manifest](../external/manifests/ffmpeg.json),
[runtime tests](../tests/unit/test_ffmpeg_runtime.py).

## INV-PROCESSING-01 — Admission, cancellation and shutdown

Trim/Crop/Resize/Export and future Dub jobs share one gate. Busy rejection precedes
new executor/staging work. Owned ordinary temp, child environment and working directory
use the injected portable root; this extends ownership without relaxing admission,
cancellation, retained-resource or shutdown guarantees.
Cancellation admission, child spawn and ownership registration remain serialized.
Reap owned processes before clean release; unresolved termination retains child/staging
and poisons admission for the application lifetime. Unresolved Export transaction,
raw-handle or staging cleanup ownership also retains resources and poisons admission.
Shutdown retains/reports failures,
does not claim a clean exit, and rejects queued success/stale completion.
Sources: [gate](../src/mediaforge/application/services/processing_gate.py),
[spawn regressions](../tests/unit/test_trim_spawn_gate.py),
[shutdown regressions](../tests/unit/test_processing_shutdown_failure.py),
[Export ownership regressions](../tests/unit/test_export_transaction_ownership.py).

## INV-PUBLICATION-01 — Source preservation and safe publication

Never modify source media, including aliases/hardlinks or another loaded item's source.
Encode/write into owned same-filesystem staging and validate before publication.
Destination-side current-output staging (and its required validation receipts/handles)
is the narrow exception to portable data residency; no model/cache/log/extraction or
long-lived session writes there. Space admission counts portable and destination
volumes without treating source bytes as reclaimable.
Dub Same as Source preserves the qualified source container/extension and original
encoded video, replacing all source audio with one Russian dub track, or refuses.
No silent transcode/container switch/ancillary omission. Unqualified extras/critical
metadata refuse video copying; independent video-to-audio and standalone-audio routes
produce only Russian mono 48 kHz MP3 192 kb/s or PCM16 WAV. Source/selected-track
rational timing and complete output validation are mandatory; a C1 plan is no mux,
decode-preservation or publication proof. Dub uses exact-object validated no-replace
publication; existing Media replacement and image-specific TxF rules remain scoped.
Default publication never replaces existing destinations, including collision races.
TASK-010/A01 permits optional confirmed replacement only on compatible local Windows
NTFS: explicit Cancel-default confirmation binds exactly one request/target identity,
with TxF isolation and rechecked identity. Unsupported replacement rejects safely with
a new-filename alternative; accepted TASK-010 new-file video export requires no TxF.
**TASK-011 FIR-E01 scoped PM ratification (2026-10-16):** new image-route single-file
results (including sequence→movie) use no-replace hard-link publication of validated
files on qualified same-volume local NTFS while validation read locks remain held.
Numbered image output requires TxF: construct one NEW isolated final directory and
only the frozen validated hard links, then expose that set with one CommitTransaction
inside the existing publication/cancellation critical section. Parent/stage identities
and member write/delete-denying validation handles survive through commit. Unavailable
TxF, non-local/non-NTFS/cross-volume targets or uncertain ownership fail closed; there
is no rename/copy/replacement fallback. This exception does not alter accepted
TASK-010 new-file or confirmed-replacement publication. TxF is Windows-specific;
availability on other/future Windows configurations is not guaranteed. No unsafe fallback or
general Windows/filesystem/power-loss/future-availability certification. Cancellation
must not publish partial success. Failed encode/validation/cancel/pre-commit publication
preserves the prior destination; post-commit cleanup failure is reported truthfully as
Failed even if validated replacement or image directory committed. A post-commit
cleanup failure can leave a visible committed result; do not auto-open or report
Completed. Retain uncertain resources and poison the shared gate. External edits
after validation locks close remain possible, including writes through hard-link
aliases; this is not immutable output or crash/power-loss durability. Delete only owned staging after
termination is established; retain it when cleanup ownership is unresolved.
Sources: [staging](../src/mediaforge/infrastructure/filesystem/staged_output.py),
[output tests](../tests/unit/test_trim_output.py),
[confirmed Export](../src/mediaforge/infrastructure/filesystem/confirmed_output.py),
[ownership tests](../tests/unit/test_export_transaction_ownership.py),
A01 acceptance (private project reference) (original: `../tasks/TASK-010_ACCEPTANCE_DISPOSITION.md`),
TASK-011 PM reconciliation (private project reference) (original: `../tasks/TASK-011.md#final-pm-reconciliation--2026-10-16`),
[image publication](../src/mediaforge/infrastructure/filesystem/image_publication.py).

## INV-MEDIA-SEMANTICS-01 — Accepted processing slices

Fast Trim is stream-copy with accepted keyframe accuracy/tolerance, not exact re-encoding.
Crop uses coded coordinates, even x/y/width/height, minimum 16x16 and source bounds;
invalid typed geometry is rejected. Exact Resize requires even dimensions 16..7680,
at most 33,177,600 pixels, deliberately allows aspect change/upscale, uses Lanczos and
SAR 1:1. Crop/Resize reject transformed orientation; retain their narrow MP4/MOV/MKV
eligibility, fixed libx264/medium/CRF18/yuv420p and audio-copy/count validation policies.
Resize explicitly preserves timestamp cadence via passthrough and its first-video
duration tolerance; do not broaden Crop's distinct duration-validation fallback.
Sources: [Trim](../src/mediaforge/features/media/trim.py),
[Crop](../src/mediaforge/features/media/crop.py),
[Resize](../src/mediaforge/features/media/resize.py),
[cadence tests](../tests/unit/test_resize_vfr_cadence.py),
[Crop validation tests](../tests/unit/test_crop_ffmpeg.py).

## INV-PLAYBACK-01 — One authoritative playback lifecycle

TASK-011 Stage 1 Owner-approved amendment (2026-10-14; implementation awaiting
independent review): image/sequence mode owns zero concrete QMediaPlayers and
publishes through the same persistent VideoOutput. ARGB32 premultiplied display
pixels, opacity 1 and one bounded internal image-only RGBA8 QML layer provide
alpha composition over checkerboard. Owner's 2026-10-15 production amendment
uses explicit admitted-preview texture size and a bounded Qt-managed renderer
envelope under the unchanged 256 MiB candidate, including deferred Qt CPU/GPU
backing and scratch. The envelope remains charged for the session; swaps do not
prove retirement. Qualify actual backend/hardware/raster and observe pressure;
unsupported modes fail closed. Disable the layer for video. Original RGBA/source stays
separate; Viewer/layer/readback pixels are never authoritative export pixels.
Video generation ownership, staging admission, shared audio and accepted timeline
semantics remain binding. No Stage 2/3 functionality is implied.

One controller, one live concrete QMediaPlayer, one persistent QAudioOutput and one
persistent QML VideoOutput serve the workspace. Retire/destroy the old player and
staging sink before successor construction. Guard stale source/backend callbacks;
shutdown releases ownership. New media primes paused without autoplay. Dub reuses this Viewer; validated Completed
offers only Open folder, with no autoplay/import/second player. Existing Media controls
remain accepted. Fullscreen
and Crop Preview reuse the current pipeline; default-device following preserves
shared playback/volume/mute state. No FFmpeg interactive playback, proxy or second decoder.

TASK-015 Owner/PM final Media 1.0 decision (2026-11-14) supersedes the
historical selectable Viewer-quality and short-video proxy amendments below.
Video always uses the original-source Qt route. Stills/sequences retain bounded
automatic display sampling and channel views. Playback generation, admission,
resource and original-source export rules continue to apply. Seekable whole-source
playback may use the installed Qt backend's loop. Armed Trim continues to use
the explicit picture-admitted half-open range.
Sources: [controller](../src/mediaforge/presentation/bridge/playback.py),
[lifecycle tests](../tests/presentation/test_playback_controller.py),
[audio tests](../tests/presentation/test_pc3_default_audio.py).

## INV-FRAME-ADMISSION-01 — Admit before painting

QMediaPlayer feeds its per-player non-rendering staging QVideoSink, not the Viewer sink.
Only current-generation admitted frames reach the persistent renderer/public frame truth.
Armed TrimRangeLoop forwards In <= timestamp < Out; withhold excluded, preroll,
unknown/invalid timestamp and stale deliveries before publication. Admitted return
picture acknowledges the owned loop seek. Manual outside navigation disarms; enabled
Trim alone does not clip whole-source playback. Preserve actual-scene proof.
Sources: [controller](../src/mediaforge/presentation/bridge/playback.py),
[admission tests](../tests/presentation/test_trim_admission.py),
[scene oracle](../tests/native_trim_paint.py).

## INV-FRAME-TRUTH-01 — Authoritative frame identity

Exact Frame N/Total and adjacency use the complete SourcePresentationTimeline and
current admitted decoded-picture mapping, not player position, duration x FPS,
nominal rate or packet order. CFR and VFR may qualify. Unknown/ambiguous truth stays
unavailable; no per-keypress FFprobe, approximate labels or readiness inferred solely
from clock progress. Failed/missing decoded or painted observations cannot become PASS.
Sources: [timeline contract](../src/mediaforge/application/contracts/source_timeline.py),
[timeline tests](../tests/unit/test_source_presentation_timeline.py),
[settlement tests](../tests/presentation/test_native_frame_settlement.py).

## INV-TIMELINE-01 — Exact asynchronous indexing

One cancellable scan owns its child. Cache only completed immutable timelines in the
session, validated by source signature; reject stale generations/completions. Storage
is capped at 64 MiB per timeline, not a claimed global workspace-memory cap. Strict
frame records and the singular selected top-level stream time_base supply authority;
known incidental stream_group and payload-empty separators have none. Malformed,
duplicate/non-monotonic or unknown non-empty records fail closed. No persistent cache.
Sources: [service](../src/mediaforge/application/services/source_timeline.py),
[indexer](../src/mediaforge/infrastructure/ffmpeg/source_timeline.py),
[indexer tests](../tests/unit/test_source_timeline_indexer.py).

TASK-016 amendment. Metadata counts and duration × rate estimates are labeled
unverified. A same-identity revisit can restore a time-only seek after the new
decoder's initial PTS observation. Decoded-picture authority still requires
the exact timeline, a matching admitted picture and the current generation.
A visit to a source with cached timing may let the one existing scan finish.
An uncached different source, source removal and shutdown may cancel it.

TASK-017 keeps decoded picture records as the only exact frame authority.
The application requests four software decode threads for its one existing
bounded scan. Frame counts and rate metadata remain provisional until the
decoded timeline completes.

## TASK-016 — Independent software Export

Original-source software encoding, validation and receipt hashing use Windows
physical/commit headroom and parent reservation accounting. Production FFmpeg
export children use suspended Job admission. Qt texture admission still uses
the existing GPU ceiling when presenting rasters. Export applies no Qt texture
admission and does not retire the Viewer. Source reads, authenticated runtime,
exact picture selection, staging, validation and publication remain protected.
Supported video picture outputs bake non-square display aspect into square
pixels at the intended height, with width rounded to the nearest pixel
(ties up). The Output summary shows the resulting raster and recipe SAR.

## TASK-017 — Still and sequence activation

Ordinary image selection considers the exact-case prefix, suffix and
extension of the selected file. A singleton is a still, even when other
formats share its name. Explicit sequence discovery retains the strict
mixed-group, contiguous-numbering and member-validation checks. Discovery
uses host memory admission. Picture allocation and presentation require the
existing renderer admission.

When a typed image admission refusal retires video, the picture node is
hidden and decoder-backed frame references are dropped while the existing
Viewer surface and source identity remain owned. The bounded wait rechecks
the rejected texture charge with fresh telemetry. On the TASK-017 Owner MOV
the allocation retired after the former three-second deadline. The image
activation window is five seconds with the existing cap of two preparation
attempts after retirement. A recovered observation
clears its own pressure notice. The established renderer ceilings and exact
picture source/generation checks still apply.

MediaForge can offer FPS from an authenticated in-session export receipt.
Video sequences only get this hint when the metadata rate agrees with all
selected decoded picture times within the index's microsecond precision.
Imported files without such a receipt still require confirmation. No sidecar
is created and no frame-rate authority is inferred from filenames.

## INV-WORKSPACE-01 — Non-destructive session edits

Per-media Trim/Crop/Reformat (compatible Resize APIs) and Output settings remain independent; selected
operation differs from enabled operation. Edit/preview never launches processing.
Removal never deletes source; duplicate/reorder preserve independent settings. Crop
mapping uses the original-source content rectangle plus shared Viewer transforms,
excludes letterboxing and preserves last-valid
geometry. Shared normal/fullscreen transport retains state. Output is available;
Noise remains unavailable. Export is explicit and captures an immutable active-item
request; editing, preview toggles and entering Output never launch processing.
Sources: [session](../src/mediaforge/application/state/workspace.py),
[session tests](../tests/unit/test_workspace_session.py),
[QML tests](../tests/presentation/test_workspace_qml.py).

## INV-REFORMAT-01 — Accepted Reformat and unified preview

Scale uses the effective enabled Crop raster, otherwise original coded dimensions.
Calculate decimal products exactly and quantize each axis to the nearest even pixel,
ties upward; validate the result against existing dimension/area limits without clamping.
Display exact effective dimensions and identify quantization. Custom Format dimensions
remain exact; Source restores the original coded canvas without resetting other edits.
Format Fit preserves the whole effective picture/aspect with padding, Fill preserves
aspect with centered cropping, and Stretch changes proportions deliberately. Source
SAR/DAR is respected; standalone results remain SAR 1:1 with the accepted safe envelope.
The authoritative geometry plan governs preview and standalone processing.

One existing live renderer previews enabled Crop→Reformat without proxy media, encoding
or player replacement. Crop Edit shows the upstream original-source picture and exact
source-coordinate handles; Crop Preview/non-edit views show the enabled composite.
Crop Reset restores exact original bounds, turns Preview off and disables Crop while
preserving selection and unrelated settings. Combined Export uses enabled Trim→Crop→
Reformat→final exact SAR→Encode/Mux. Numeric Pixel Aspect belongs to Reformat and is
inert when disabled; 16:9 computes exact SAR from coded dimensions, Reset restores
source SAR. PAR alone does not enlarge the coded canvas. Standalone backend Reformat
remains square-pixel; user export uses unified Output. Exact CFR/VFR filtered PTS and
common A/V origin are preserved without nominal-FPS rounding. Audio Only validates
selected-stream duration/Trim intersection, never video/container duration.

Owner-approved TASK-011 UX replacement, 2026-10-16: Source/Output comparison and W
are removed. Toggle Reformat enabled/disabled to compare preview geometry; preserve
its settings. Crop Edit retains its upstream source-coordinate picture/handles;
Crop Preview applies enabled geometry. Quality controls preview raster only and
never supply Export pixels. None of these actions seeks, replaces the video decoder
or starts processing. Historical TASK-010 acceptance of W remains provenance.
Owner-authorized TASK-011 core conversion uses original file identities/manifests
and frozen video presentation ordinals/timestamps, never Viewer rasters. Image Crop
uses exact positive integer pixels; image Scale rounds to integers, ties upward.
Video even/minimum-16 and nearest-even rules remain unchanged. Transparent image
Reformat is refused unless its preservation is qualified or an opaque matte was
explicitly selected. Sequence directory output is a validated new local-NTFS folder,
published by the TASK-011 TxF/frozen-hard-link exception in INV-PUBLICATION-01 under the shared processing gate;
uncertain child/handle/staging cleanup retains ownership and poisons that gate.
Existing single-file video replacement/TxF behavior is unchanged.
The earlier TASK-011 namespace-rename design is retained as historical rationale in
the task; FIR-E01 supersedes that mechanism because member locks must survive commit.
Sources: task and amendments (private project reference) (original: `../tasks/TASK-009_ACCEPTANCE_DISPOSITION.md`),
[geometry](../src/mediaforge/features/media/reformat.py),
[unified preview](../src/mediaforge/presentation/qml/UnifiedPreview.qml),
[geometry tests](../tests/unit/test_reformat.py),
[final UX tests](../tests/presentation/test_task009_final_ux_qml.py),
TASK-010 contract (private project reference) (original: `../tasks/TASK-010.md`),
[exact Export timestamps](../tests/unit/test_export_timestamps.py),
[selected audio timing](../tests/unit/test_export_audio_timeline.py),
[Crop Edit/Preview input](../tests/presentation/test_crop_comparison.py).

## INV-VIEWPORT-01 — Viewer zoom independent of output

Fit is the minimum zoom and restores centered pan; its readout is 100%, relative to Fit.
F performs this same Viewer Fit in normal/fullscreen, independently of Reformat Fit.
Double-click/the fullscreen button toggle fullscreen; Escape exits. F defers to
editable controls and modal/popup keyboard interaction.
Wheel zoom is cursor-anchored and bounded to 1..32× Fit; middle-button pan is constrained.
Viewer-only zoom/pan never changes output dimensions or launches processing. Crop source
mapping remains correct under zoom/pan, fullscreen and logical/device-pixel scaling.
Source switching resets Viewer zoom/pan independently of per-item Crop/Reformat settings.
Sources: [viewport](../src/mediaforge/presentation/bridge/viewer_viewport.py),
[Viewer tests](../tests/presentation/test_viewer_qml.py).

## INV-LEGACY-01 — Immutable, explicit reference

Never modify, delete, rename or reorganize Legacy. Inspection requires task permission;
migration requires the approved allowlist and characterization, not mass-copying.
SystemForge/VFXForge are excluded; Reduce Noise remains deferred.
Source: [Migration Allowlist](ARCHITECTURE_BASELINE.md#migration_allowlist).


## Owner Product Check correction authority — 2026-10-16

The Owner's explicit TASK-011 Owner Product Check Corrections supersedes the prior
limited FHD-window/96 MiB renderer policy and no-cache/no-proxy restriction for this
candidate. Prior decisions and failed evidence remain provenance, not current
acceptance. Product Check failed; independent review and renewed Product Check remain.

Keep one persistent Viewer, Qt video player ownership and exact original source
presentation timeline. Preview may use native Qt 16-bit premultiplied smooth sampling,
a bounded 96 MiB byte-charged CPU LRU with one adjacent prefetch, and cancellable managed-FFmpeg
video proxies through the shared ProcessingGate. Source identity, source locks,
original-resolution Export, FC-01 result receipts and FCR-01 handle-bound publication
are unchanged. Proxy timing must be independently matched to original presentation
ordinals; unsupported timing must refuse instead of approximate. Auto video uses source.

Preview sampling is display-only: constant alpha must remain exact; reference area
sampling comparisons allow at most 1 code value of alpha and 2 of premultiplied RGB
for qualified integer half/quarter reductions. No hidden-color leakage is allowed.
This explicitly replaces the Python exact-area preview requirement, not source or
Export alpha fidelity. General scales require separate qualification.

Separate the 256 MiB CPU lease candidate (16 MiB control-plane allowance, all
current/prepared/cache leases counted) from a monotonic whole-renderer reservation:
32 MiB base plus eight 4-byte viewport surfaces and eight maximum source
texture surfaces (image preview up to UHD at 4 bytes/pixel, subject to public
QRhi texture limits and the OC-01 qualification below;
video up to UHD at 4 bytes/pixel for 8-bit or 8 bytes/pixel for higher precision),
rounded upward to MiB (maximum qualified physical viewport 3840x2160). Observe process
GPU/CPU pressure and host headroom; missing telemetry fails closed. This is a tested
renderer-pool operating envelope, not per-texture retirement or adapter VRAM proof.
Frame swaps never release GPU charges. Cache owns immutable preview backing only,
checks source bytes under a fresh write/delete-denying lock on reuse, evicts by bytes
and under admission pressure, and releases at shutdown. Requested timeline ordinal,
prepared/published ordinal and painted observation must remain distinguishable.

Qt video CPU decoder retention is separately observed and conservatively scored:
V = (2 * min(16, logical CPU count) + 4) * maximum video texture bytes.
It is monotonic for the Viewer session, including video-to-image transitions.
Parent private growth plus incoming CPU reservations must fit R + V + G;
process GPU dedicated/shared usage plus incoming GPU estimate must fit G.
These are separate admission scores, not a sum of independent physical allocations
or a claim that a Windows Job bounds Qt/GPU allocations. Host physical/commit
floors still apply. On the tested 16-thread desktop, UHD 12-bit video makes
V = 2,388,787,200 bytes; measured process peak was 1,638,715,392 bytes.
The earlier image-only classification of retained Qt video decoder commit
incorrectly refused safe video-to-image transitions; its failed runs are retained.
No universal GPU, arbitrary desktop, 8K or per-texture retirement qualification follows.

Video reduced previews are transient owned MOV files, not a persistent cache: one
current plus one incoming, each at most 128 MiB. Use the same authenticated runtime
and ProcessingGate, suspended Job assignment before work, host-derived whole-Job
CPU commit at most 512 MiB for the qualified UHD/five-minute SDR class, two decoder/
encoder threads and bounded streaming timeline validation. Copy supported audio;
refuse unsupported color/rotation/timing rather than approximate. Original media
locks span active proxy lifetime. Auto/1:1 retain original Qt decoding. No production
Route M still decoder, new dependency or authoritative preview export is introduced.
A preview retirement failure quarantines the shared gate and retains resources.

## TASK-011 Final Media Completion — explicit Owner amendment (2026-10-18)

Current TASK-011's Final Media Completion amendment supersedes the prior 8-bit-only
output and no-proxy-cache restrictions. Requested one-based UI frame/time, inclusive
First/Last UI Trim, decimal FPS presets/custom exact rationals and displayed picture
identity are separate from original source numbering and half-open source intervals.
Current Frame export cannot capture a stale displayed sequence frame while another
logical frame is pending. No original-clock/frame admission invariant is changed.

Native output codecs are pinned project dependencies; isolated, Job-contained child
work stays inside the shared processing/transaction ownership. PNG16/TIFF16/TIFF32F
are source-based file capabilities, explicitly distinct from 8-bit Viewer support.
Format/depth/compression/channels and stored samples are validated under output locks
before publication; uint8 promotion is k*257 or normalized float32 k/255. JPEG 411,
422,444 and WebP lossless/lossy must match their actual offered controls. Alpha
flattening means opaque RGB composite, never black alpha. New image processing T
is capped and enforced at <=512 MiB with host admission, separate from unchanged
256 MiB image R; source/export/publication safety remains mandatory.

Session proxy caching retains two <=128 MiB completed tiers plus one incoming;
source/proxy deny-write/delete ownership and full identities span reuse. At most
two 4 MiB original timing arrays are cached; 16 MiB of image CPU leases covers
cached and incoming timing storage. A stale/canceled result never activates.
Shutdown releases owned cache/locks or fails closed with retained ownership.
No persistent cache, GPU retirement fence, general HDR/8K or dependency fallback.


### Final Media Completion alpha-processing clarification — 2026-10-18

Owner's complete-processing authority qualifies preserved-alpha Crop/Reformat
through an exact rational pixel-area integrator using vectorized native NumPy
operations in the owned codec child. Straight RGB is weighted by alpha; alpha is
integrated independently, with half-up integer rounding and transparent
outside-canvas coverage; float32 normalizes the exact integrals directly. No per-pixel Python loop, preview raster or approximate FFmpeg alpha
recipe supplies Export. Extreme rational coefficients/unsafe int64 integrals or
CPU peaks refuse before publication; ordinary fractional Fit/Fill/Stretch is
checked against independent rational-cell references. Unchanged geometry bypasses
this transformation, preserving stored hidden RGB. WebP may discard hidden RGB
only after its existing explicit acknowledgment. PNG/TIFF stored samples are
exact for the selected representation. Display-only Qt interpolation tolerances
are not Export tolerances. Full-source/intermediate space is charged even when
the output canvas is small. See [codec runtime](../docs/IMAGE_OUTPUT_CODECS.md).


Processed MP4/MOV output explicitly disables muxer-generated tmcd tracks and
clears inherited timecode tags. Only the selected video/audio streams enter the
accepted processed-output contract; strict extra-stream validation is unchanged.
This does not alter Exact Copy stream preservation, source presentation timestamps,
original embedded timecode, FC-01 receipt binding or FCR-01 publication.


New image-processing child admission also charges remaining parent R + V + G
score growth not already reflected in current private growth, before deriving its
host-based quarter-headroom commit allowance. Existing resident allocations are
not subtracted from host availability twice. GPU pressure is observed separately;
this conservative score is not an independent physical allocation total.


## TASK-011 Final Closeout — Owner/PM amendment (2026-10-18)

The FINAL CLOSEOUT assignment supersedes conflicting earlier UI wording only;
TASK-010 source-clock, decoded-frame admission, FC-01 and FCR-01 safety remain binding.
A friendly FPS label is not a stored clock. Unedited confirmation submits its exact
backing rational, including a frozen exported-result receipt. Manual decimal input
is exact base ten (23.976 = 2997/125); explicit fractional presets submit 24000/1001,
30000/1001 or 60000/1001. Fractions remain accepted. The six-decimal custom precision
requires numerator <=120,000,000 and denominator <=1,000,000, with exact rate 1–120;
this includes the reduced ratios of every admitted decimal without a lossy round trip.

Timeline shows requested one-based `Frame X / N` and logical elapsed time immediately
for video and sequences. Original filename numbering belongs to Info/Details/tooltip,
not the permanent label. Video requested ordinal uses indexed PTS presentation
intervals (including VFR), with clamped endpoints; it never authenticates a decoded
picture. Current Frame export and exact stepping require actual admitted identity;
a pending or obsolete same-player seek cannot authorize export or overwrite the
latest paused picture. Earlier clauses saying only admitted frames drive all public
state now apply to displayed-picture identity and Export, not requested transport UI.

Video Tray sessions retain compact requested position, original signature and quality;
Trim/edit settings remain per item. Inactive sessions retain no extra player. Reactivation
re-primes the first PTS correspondence without publishing frame 1, then restores the
request against the original indexed timeline. Playback resumes after the matching
picture, before arming Trim loop. Same-source quality changes may retain one already
charged sink poster; no cross-source poster or new Viewer is introduced.

Routine sequence Play preparation is visually silent. Paused cold loads and explicit
video-tier preparation may show a stable indicator after 750 ms; failure details remain
accessible. Frame swaps are still not retirement certificates.

Reduced video preview keeps the existing <=512 MiB host-admitted whole-Job ceiling.
At the full allowance, native qualification uses four decoder/indexer threads and two
encoder threads; lower allowances use two decode threads. No automatic cap increase,
retry escalation, processing source substitution or weakened timestamp/audio validation.
Source clock scans and cold generation remain finite waits; completed hash-bound
session tiers are reused. No real-time or universal hardware claim follows.

This is Builder correction scope, not Independent Reviewer PASS or Owner acceptance.


## Initial OC-01/02/03 qualification — 2026-10-19 (superseded for image admission below)

The Owner authorizes the directly affected UHD/transition/Tray correction. The
previous 2048-square image limit remains historical evidence; it is superseded
for the measured Direct3D11 vendor 4318/device 10370 desktop by actual UHD
3840×2160 image rasters, including explicit 1:1. Public QRhi TextureSizeMax is
observed on the render thread at scenegraph initialization. An unavailable or
smaller native limit refuses; capability alone does not qualify other hardware.
Physical viewport remains at most 3840×2160. Layer texture size is the admitted
preview raster, independent of Viewer zoom/window DPR. Auto may reduce; explicit
quality never silently downgrades. No source/Export pixels or alpha oracle change.

CPU image cap stays 256 MiB, cache stays 96 MiB with pressure eviction. Existing
peak plan 4×decode-raster + 2×preview-raster + encoded bytes counts alongside all
active/cache/manifest leases; failed admissions preserve the current picture.
The renderer score is still 32 MiB + 8×RGBA physical viewport + 8×maximum admitted
source texture, rounded up to MiB and never decremented by frame swaps. UHD RGB
image/4K viewport therefore calculates 539 MiB, not an arbitrary new fixed cap.
Higher-depth UHD video retains its existing 792 MiB G / 2,388,787,200-byte V score
on the tested 16-thread workstation. Host headroom and process pressure gates remain.
This is bounded renderer-envelope qualification, not per-texture GPU retirement,
adapter-budget telemetry, universal hardware support or an 8K promise.

MOV reactivation may bridge reload using at most two source-locked, already admitted
native CPU QVideoFrames, at most 64 MiB mapped plane bytes each / 128 MiB total.
Read-only native mapping measures stride/plane storage without copying pixel bytes.
These two retained frame backings occupy existing display/pending slots in V;
shared QVideoFrame/timeline backing is not charged twice. No additional player is
retained. Source and ancestor locks deny write/delete for cache lifetime; exact
source signature and requested indexed ordinal must match before reuse. Cache
is LRU-evicted, removed when the final corresponding Tray row is removed, and
closed explicitly at shutdown. Lock retirement failure keeps ownership reachable
and fails shutdown. GPU-only frames are not retained: an already qualified CPU
picture of the same source/ordinal may bridge a proxy reload; absent a valid one,
honest loading remains. Posters are never decoder admission, an origin certificate,
a reliable current-frame receipt, or Export input. New decoder frames still pass
all original generation/PTS/Trim admission. A prior correspondence can label logical
requested position while its independent new decoder prime is pending.

Tray retains keyed delegates with constant selection-independent widths. Snapshot
notifications update roles without resetting the ListView or resetting manual
scrolling. Only explicit keyboard navigation requests a controlled offscreen reveal.
Selection/focus style does not change card geometry. Publication/codec/source safety,
MF-F01 exact FPS and CL-F01 semantic delayed progress remain binding and unchanged.

Builder evidence and qualification: Owner final correction report (private project reference) (original: `../reports/TASK-011_OWNER_FINAL_CORRECTIONS_BUILDER.md`).
Independent review and Owner acceptance remain separate; this amendment does not close TASK-011.


## Owner OC-01 resolution-scalable amendment — 2026-10-19

The later Owner instruction supersedes the preceding UHD-only image admission
and fixed 256 MiB preview CPU policy; their rationale and failed evidence remain
historical. There is no source-dimension maximum for 1:1. Format, validated
payload, available host headroom and decoder capability determine admission.
The current format parser retains its streamed 4 GiB encoded-file safety bound,
8-bit/sRGB/static/single-page qualifications and bounded metadata scan. This is
not universal support for arbitrary files or HDR/color classes.

1:1 retains genuine original-resolution CPU source data. Fit may sample that
source to the physical viewport; at Viewer 100% one source pixel maps to one
physical display pixel. Zoom is distinct from quality. When a full-frame GPU
upload exceeds 32 MiB RGBA or the observed public QRhi TextureSizeMax, the same
persistent VideoOutput presents only the visible source region. Region staging
and upload remain bounded; Fit uses a reduced representation without relabelling
it as a source-resolution uploaded raster. Details expose original, staged and
uploaded dimensions. No tile/picture readback is authoritative Export data.

JPEG sequential reduced tiers can use the installed handler's early reduction.
Full-resolution JPEG, PNG, TIFF and WebP may decode the entire source: viewport
rendering is not partial decoding. Before allocating, reserve a conservative
4 x decoder RGBA + 2 x preview RGBA + encoded-file-bytes peak alongside live
leases. Dynamic image preview CPU capacity is one quarter of the smaller
physical/commit availability after retaining max(2 GiB,15% total physical) and
max(2 GiB,10% total commit), respectively. Re-evaluate current availability and
pressure per request; no automatic retry with a larger cap. Reused full-source
staging is pinned by shared CPU/file leases and reserves its distinct region,
normalization and upload scratch. Full source SHA-256 and metadata checks remain
mandatory on every region preparation, even when decoded backing is reused.
Streaming validation is cancellable. Qt decoder calls themselves are synchronous
inside the sole worker; cancelled results cannot publish, and shutdown joins it.
QImageReader's allocation limit is an additional temporary guard, not a complete
codec/process memory limit. Unknown decoder classes refuse before unsafe work.

Keep the 96 MiB image LRU; a large full-source stage is current/request-owned,
not duplicated into the LRU. One active and one latest pending request remain.
Shared leases survive old-picture retirement until the borrowing worker returns.
Renderer/GPU and video CPU high-water are separate from image CPU accounting;
processing admission includes measured preview CPU high-water as future-parent
reservation. Legacy image processing's 256 MiB per-route policy is unchanged.

The bounded renderer score remains 32 MiB + 8 x physical viewport RGBA +
8 x maximum admitted upload bytes, rounded up to MiB. Existing video high-water
can dominate. GPU textures are viewport-sized or a safely admitted full raster,
never proportional to an unbounded source. The physical desktop envelope remains
<=3840x2160 on the measured D3D11 vendor 4318/device 10370; another hardware/profile
needs qualification. Renderer estimates and process allocation observations are
not per-texture retirement fences or complete adapter-budget telemetry. No charge
is released merely on frameSwapped. Pressure/refusal preserves the current picture.

The source-bound video poster and stable-key Tray rules above remain current.
The Builder report records actual 4K/8K/16K/nonstandard native qualification,
DPR, peak CPU/GPU observations, cancellation and limits. Large-source cold decode
and source hashing need not be real-time. Independent review remains required.

Observed process CPU growth is checked against the same current host-derived
preview capacity plus separately charged video/render envelopes, rather than an
obsolete 256 MiB image-only process-growth baseline. This includes loaded native
codec/control allocations without assigning them a fictitious GPU charge. GPU
pressure limits and preallocation lease checks remain independent and fail closed.


## TASK-011 Owner amendment — Import/Export parity (2026-10-20)

Owner's explicit Complete Import / Export Parity assignment supersedes the earlier
8-bit-only image admission and higher-depth external-Viewer-only restrictions.
Their historical rationale and evidence remain retained above/in prior reports.
This is cumulative unaccepted WIP; independent verification and Owner acceptance
remain separate. See `reports/TASK-011_IMPORT_EXPORT_PARITY_BUILDER.md` for the
executed matrix and qualification limits, not an inference from codec availability.

The original-sample contract distinguishes uint8, uint16 and finite float32 PNG/TIFF
source samples from the 8-bit premultiplied monitor representation. PNG integer
8/16; TIFF integer 8/16 and float32, contiguous single-page RGB/RGBA and normalized
gray/gray-alpha, with None/PackBits/LZW/Deflate, use the pinned existing native codec
libraries when Qt cannot preserve samples. Associated-alpha high-depth, tiled/planar,
multipage, non-finite float, unknown ICC/CICP and HDR/color-management classes are
not silently reinterpreted. No source bytes are rewritten. Float RGB values remain
float32 through identity/crop/export, including finite out-of-display-range values;
float alpha must be [0,1]. Untagged samples use the declared sRGB-code-value class,
not an invented scene-linear/HDR interpretation. Float-to-integer conversion clips
[0,1] and rounds half upward; integer-to-float divides by the integer maximum.
Preview clips/quantizes only for the physical monitor. Viewer/readback/proxy data
never supplies Export pixels. Destination sample depth/compression is independently
validated before the existing locked publication transaction.

PNG cICP takes precedence over lower-priority profile hints, per PNG Third Edition.
Qualified full-range RGB cICP 1/1/0/1 uses BT.709 OETF and shared sRGB primaries:
preview explicitly maps BT.709 code values through linear light to sRGB; original
processing performs the same declared transform in float precision before final
encoding. cICP 1/13/0/1 is sRGB. Unknown combinations refuse; this is not universal
ICC/HDR support. Reference: https://www.w3.org/TR/png-3/ .

OC-R01 remains mandatory for every sample type: only an independently retained,
correct-source full backing pin can admit reduced region reuse. The executor may
not fall back to a cold decoder under that lease. A missing/lost pin refuses or
requires complete cold admission. Typed CPU decoder scores use actual aligned
bytes per pixel (4/8/16), encoded input, normalization and display scratch. The
existing host-derived quarter-headroom admission, bounded worker/pending intent,
96 MiB LRU, region upload and separate renderer high-water remain in force.

Original-image processing now derives a sequential per-route CPU allocation score
from encoded input, source/destination sample widths, transform/composite prefix
scratch, writer and independent decoder overlap, plus codec/control allowance and
25% typed-allocation margin. FFmpeg and native codecs execute in separate
sequential phases; charge their maximum live peak, including transformed raster,
writer and independent validation overlap, rather than summing retired phases.
This supersedes a fixed 512 MiB rejection for precision/transfer processing:
the Windows Job limit is the larger of the established 512 MiB floor and the
complete planned requirement rounded to 4 MiB. It is never an unconditional
larger cap. Both before staging and before each suspended child, require that whole amount
within one quarter of current physical/commit headroom after the established host
margins, future parent CPU/render/video growth and control-plane leases. Failure
refuses before spawn; no automatic cap retry. A Job limit is not GPU telemetry.
No previous member arrays may survive into the next sequential decode. Parent
source locks, ProcessingGate and Job reaping/publication ownership remain intact.
Receipt/control reservations use that same current host-derived CPU gate instead
of the obsolete 256 MiB total-ledger ceiling after a qualified large preview.
The callback weakly references its owning service; a result receipt cannot root
an inactive Viewer/service. This is not permission for an uncharged decoder.
Sequence movies explicitly encode a BT.709 YUV matrix/primaries/range and the
correct declared transfer (sRGB for normalized sRGB samples); full-resolution
original samples, never monitor pixels, feed the codec.

The existing offered WAV PCM16 and MP3 128/192/256/320 kb/s outputs also have explicit
AudioSource admission, one existing Qt audio transport, time-based Trim and verified
Open Exported Result/re-export. They do not acquire a fabricated image/frame clock,
a second player, image quality tiers, Crop or Reformat. PCM RIFF's sample origin is
zero; MP3 uses its own stream timing and encoder delay. Video behavior and exact
source presentation timelines remain separate and unchanged.

AudioSource parity is qualified for one audio stream, PCM16 WAV or MP3, positive
sample rate up to 768 kHz and 1–32 channels (actual encoder/container limits still
apply). Output validation must satisfy the same input profile before publication.
Multi-stream audio-only files and unsupported channel mappings are not certified.


## Owner Product Check UX/VID correction — 2026-10-21

The explicit Owner/PM assignment supersedes earlier UI requirements for hover
helpers, Viewer corner cues and the visible Open Exported Result navigation.
Media has no hover tooltip presentation; accessibility descriptions remain and
error/progress details use explicit click/keyboard activation. Crop handles and
Reformat geometry remain unchanged, with no decorative corner label. Completed
Export exposes only **Open folder**. Validated-result APIs/receipts and manual
import/parity stay mandatory; no auto-import or weakened publication is implied.
Opaque image/sequence destinations requiring a matte expose an explicit Black/White
Background / Matte choice even while validation refuses. No hidden/default
flattening, Viewer-channel change or original-sample substitution is permitted.

VID-01 extends the existing two-entry/128 MiB source-locked CPU poster cache to
qualified hardware NV12 on the tested Qt/D3D11 profile. On source exit only,
read-only public QVideoFrame mapping copies the native YUV planes into a separately
owned NoHandle frame, retaining its color/range/rotation and exact source ordinal.
Unknown GPU layouts refuse this optional bridge. No private texture API, GPU-frame
cache, QImage/readback Export source or extra player is introduced. Conservative
16 x source pixels + 2 MiB temporary overlap passes existing host/process pressure
admission before mapping; actual per-plane strides/bytes and 64 MiB/frame remain
checked. Two retained NV12 frames, one mapped transfer and its destination fit
inside the existing four RGBA display/pending V slots; no CPU/GPU cap is raised.
The prior GPU-only-poster exclusion remains history; no GPU-only frame is retained.
A previously proven, signature-bound timestamp offset may label logical intent
without a poster, but never grants decoder readiness or current-frame Export.
First-open/evicted/unsafe-poster cases show a neutral Loading video state until a
real admitted frame arrives. Generation/PTS/Trim/source locks stay authoritative.

This is cumulative unaccepted WIP pending focused independent review and Owner
Product Check. See reports/TASK-011_OWNER_PRODUCT_CHECK_CORRECTIONS_BUILDER.md.
Historical reports, seals, failed evidence and prior qualification limits remain.


The same correction's required 100-frame UHD RGBA H.264 export exposed an omitted
encoder queue in the sequential per-route T allocation score: the previous 512 MiB
Job failed safely with allocator errors; no output was published. The score now
includes pinned x264 veryfast/medium/slow lookahead (10/40/50), reference pictures
(1/3/5), three B-picture and four current/recon/control slots, capped by selected
picture count. Charge one 64-aligned four-byte raster per queued slot plus the
existing decode/filter scratch, largest encoded member, 64 MiB codec allowance
and 25% margin. Take the maximum with separately retired typed-codec phases.
This corrects a missing allocation term, not a new host/GPU cap or automatic
higher-cap retry: the existing quarter-headroom admission, per-spawn resampling,
4 MiB Job rounding, source locks and whole-Job containment remain unchanged.
The fast 100-frame3840x2160 Owner cases calculated1,164,638,616 B, applied
1,166,016,512-B Jobs and measured942,247,936/928,940,032-B successful Job peaks.
Presets/CRF/pixels/alpha algorithms and encoded recipes were not changed to fit.
No claim of universal peak capacity follows from these measured cases.

## Owner UHD H.264 transition correction — authorized 2026-10-21

An explicit image/sequence intent may phase out the current video decoder only
after a typed, measured GPU admission refusal. Keep compact source/session/time
identity and existing locks; revoke decoded readiness, clear hardware-frame
owners and destroy the concrete player/staging sink. A deliberate neutral
presentation is allowed during this bounded transition. Target activation still
requires complete source validation and fresh CPU/GPU admission before allocation.
One existing image worker has a three-second eligibility window and at most two
post-retirement preparations, each freshly admitted after complete failed-buffer
cleanup. An in-progress admitted decode may finish later; no new preparation may
start after the eligibility deadline. The worker must not debit renderer/video high-water, raise limits,
infer retirement from frameSwapped, or create an unbounded retry/decoder queue.

Canceled/failed handoff restores the old session only after fresh renderer and
host admission of its existing R/V/render future-parent score, subtracting only
already resident process growth. Genuine pressure leaves it unloaded with an
actionable error; no repeated automatic restart. Explicit source retry rechecks
eligibility. Current-frame/processing Export cannot start during this unfinished
handoff. Poster/source authority and original-source Export remain unchanged.
This is unaccepted cumulative WIP; see TASK-011_UHD_H264_TRANSITION_BUILDER.md.
