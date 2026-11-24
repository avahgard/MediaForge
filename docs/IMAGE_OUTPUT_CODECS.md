# Image output codec runtime — TASK-011 Final Media Completion

**TASK-011 — CLOSED / OWNER PASS / 2026-10-22.**

Explicit Owner/PM acceptance covers the qualified cumulative Media implementation,
including the final duplicate Fullscreen exit-button correction. Accepted pre-commit
source/test/pyproject fingerprint (113 paths):
`5786be23a5dc8beacf4157cbddc2229771f7d3cd40c48d31408e8586eb332d19`.
The [acceptance checkpoint](CLEANUP_HANDOFF.md#historical-archive) (original: `../reports/TASK-011_OWNER_ACCEPTANCE_CHECKPOINT.md`)
records the complete positive staging manifest, evidence, support limits and Git
preservation gates. Earlier dated WIP/open statements below are historical stage
records superseded by this acceptance; their technical findings are preserved.
No further Media development is authorized. Next: **TASK-012 Legacy Dub Audit**,
not started by this closure.




Owner authorizes native output dependencies in the 2026-10-18 assignment. Preview

remains native Qt; these codecs execute only in the existing source-based Export

worker. No online service or undeclared global executable is used.



## Reproducible Windows x64 / Python 3.12 installation



Use the project `.venv` only. Exact pins are in `pyproject.toml`; the qualified wheels

are listed below. Install these three wheels with `--no-index --no-deps` after

verifying SHA-256, preserving existing PySide6, pytest and managed FFmpeg pins.

Do not use an automatic floating upgrade or install into the global interpreter.



| Wheel | SHA-256 |

|---|---|

| numpy-2.4.3-cp312-cp312-win_amd64.whl | 65f3c2455188f09678355f5cae1f959a06b778bc66d535da07bf2ef20cd319d5 |

| imagecodecs-2026.11.11-cp312-abi3-win_amd64.whl | 7a46ef92db4f3bffafd46abd14edc214198ce04c1db5f9b80a01e33c71eea455 |

| tifffile-2026.9.20-py3-none-any.whl | 9b913167b8f66a57f2e7c0454486c4c4607196d494797461226166bd0755c0e6 |



This environment uses Python 3.12.14. The project minimum is now Python 3.12,

matching the codec packages. Other wheel/Python/platform combinations require

qualification, not a fallback. `numpy` native array operations replace interpreter

pixel loops for exact output resampling; `imagecodecs` provides actual JPEG 4:1:1,

PNG and WebP codecs; `tifffile` writes typed TIFF containers, independently read by

imagecodecs/libtiff and tag/pixel test oracles.



## Distribution notices



NumPy wheel metadata: `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`.

Imagecodecs and tifffile package code: BSD-3-Clause. Used native codecs (libjpeg-

turbo, libpng, libtiff, libwebp, zlib) carry their own permissive notices. Preserve

the complete wheel notices rather than labelling the entire bundled wheel only

BSD: `numpy-*.dist-info/licenses`, `imagecodecs-*.dist-info/licenses`,

`imagecodecs/licenses` (including applicable patents) and

`tifffile-*.dist-info/licenses` must accompany a redistributed application.

Optional bundled codec availability is not product support. No HEIF, EXR, HDR,

OCIO, JPEG XL or other advanced format is enabled by installing this wheel.

Existing Qt/FFmpeg distribution obligations remain unchanged. TASK-015 packaging

must carry the original complete notice material; this task does not publish a

redistributable installer.



## Ownership and rollback



The isolated base-Python child is assigned suspended to a verified Windows Job

before work. It receives only the explicit project site-packages and owned locked

plan/intermediates; one native codec thread, no GUI imports. Package versions and
module paths must match the explicit project site-packages, preventing a global
package fallback. Source/transaction

ownership stays with the parent ProcessingGate. Missing codec/runtime, bad pixels,

containment failure or uncertain cleanup refuses publication; no fallback encoder.



Rollback is a separately authorized restoration of the pre-task source/pyproject

fingerprint and project virtual environment, not an automatic uninstall or Git

reset. Entry custody and exact original source hashes are retained in

`.checks/task011-media-final-20261018-r1/entry.json`. Existing accepted video

publication/decoder code is not replaced by this dependency choice.



Preserved-alpha resampling uses exact integer coverage integrals. Integer output
rounds half upward to its sample type. TIFF float resampling normalizes the exact
rational integrals directly to float32 (half coverage is exactly 0.5), without an
intermediate 16-bit rounding. No implicit gamma-to-linear transform or invented
HDR information is claimed. Codec/transformation peaks and worst-case original
intermediate space are admitted separately from resident Viewer buffers.


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

## TASK-011 WEBP-D01 — lossy decode representation (2026-10-21)

Static 8-bit WebP preview uses the pinned Qt/libwebp RGB decoder. Original-source
WebP conversion uses the authenticated FFmpeg native WebP decoder and libswscale
RGB conversion before the existing precision-aware writer. Export never samples
the Viewer. Lossless WebP RGB and alpha agree in the qualified controls. Lossy
VP8 WebP is YUV 4:2:0: these two RGB reconstruction paths are **not pixel-identical**.
Sharp chroma edges can differ substantially; a q37 31×19 control differs by up to
77/255 (mean absolute channel difference 10.03565). This is not a general error
tolerance or a guarantee for other pictures. No alpha tolerance is relaxed.

An independent BT.601 limited-range, quarter-position bilinear chroma reference
reconstructs libwebp's RGB exactly from the same FFmpeg-decoded YUV planes in the
qualified sharp/gradient/constant controls, including RGBA. Both paths retain the
same geometry and exact alpha. The difference is chroma upsampling/RGB rounding,
not a MediaForge channel swap, source rewrite, preview-driven Export or requested
color transform. No extra matrix/range override or post-hoc color adjustment is
applied to force agreement. libwebp's [upsampling reference](https://github.com/webmproject/libwebp/blob/v1.6.0/src/dsp/upsampling.c)
and [BT.601 conversion](https://github.com/webmproject/libwebp/blob/v1.6.0/src/dsp/yuv.h)
define the independent reference; FFmpeg's [scaler documentation](https://ffmpeg.org/ffmpeg-scaler.html)
describes its separate chroma interpolation controls.

For visual review use the Qt/libwebp reconstruction; for original-source lossy
WebP conversion the FFmpeg reconstruction is authoritative. Do not promise
pixel-identical Viewer-to-PNG conversion for lossy WebP. No universal ICC/transfer,
HDR, animation or non-identity WebP EXIF orientation support follows from this
qualification. Unknown/unsupported profiles remain refused. Evidence, exact pins,
per-pipeline native witnesses and remaining PAR-E01 custody uncertainty are recorded
in `reports/TASK-011_FINAL_REVIEW_CORRECTIONS_BUILDER.md` for independent PM review.
