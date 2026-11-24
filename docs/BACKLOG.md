# Backlog

PM owns priority and Architect approves promotion. Entries are deferred until an
approved bounded task assigns them. [Project State](PROJECT_STATE.md) owns release
work. [Release Evidence](RELEASE_EVIDENCE.md) owns historical acceptance pointers.

| ID | Work to consider | Before promotion |
| --- | --- | --- |
| BL-VIEW-01 / 02 / 03 / 04 | High-resolution responsiveness, heavy-source frame-step latency, EOF looping and Trim return latency | Measure a representative baseline and preserve exact painted-frame and exclusive-Out contracts. |
| BL-INGEST-01 | Multi-file Add and Drop | Define batch admission, ownership and partial failures. One file per action remains the accepted baseline. |
| BL-TIMELINE-01 | Long-source indexing and cache | Preserve authoritatively indexed picture identity. Qualify source fingerprints and storage and memory limits. |
| BL-UI-01 | Broader localization and UI polish | Define text/token ownership and scope beyond accepted appearance settings. |
| BL-UI-02 | Light Choose file label contrast | Confirm the nonblocking S21-IR-N01 observation on the intended build and qualify all button states. [Settings review](CLEANUP_HANDOFF.md#historical-archive) (original: `../reports/TASK-021_SETTINGS_INDEPENDENT_REVIEW.md`) |
| BL-NOISE-01 | Reduce Noise | Approve a separate provider/runtime architecture. |
| BL-UPDATE-01 | GitHub Releases version check, optional daily online checks and update availability notification | Define opt-in behavior and authenticate release metadata. |
| BL-UPDATE-02 | Settings-based manual update, transactional installation and rollback, reuse of unchanged runtime dependencies | Preserve Data and compatible models across rollback. Verify cached dependencies against the new release pins. |

Closed baseline entries remain historical: BL-INFO-01, BL-INGEST-02/03,
BL-OUTPUT-01, BL-REFORMAT-01, BL-CONVERT-01, BL-PIXEL-ASPECT-01 and BL-COMPRESS-01
within their qualified scope. BL-DUB-01 and BL-SETTINGS-01 no longer represent
unimplemented subsystems. BL-RELEASE-01 is tracked in Project State.

## MediaForge 2.0

Owner direction of 2026-10-22 deferred deeper work on import and first-frame
latency, sequence playback, timeline scrubbing, cold video proxies, export
performance, hardware/driver coverage and format/precision performance. Benchmark
before changing resource or quality contracts. EXR/DPX remain outside mandatory 1.0
scope. Use concrete reproductions for future user-reported defects.
