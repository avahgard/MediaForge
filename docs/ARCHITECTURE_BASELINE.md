# Architecture

## Current system map

Project State (private project reference) owns current scope and status.
[Project Invariants](PROJECT_INVARIANTS.md) owns binding behavioral constraints.
The dated baseline below retains architecture decisions, migration allowlists and
Media amendments. Its original roadmap and examples describe earlier stages.

| Boundary | Ownership |
| --- | --- |
| Core | Bootstrap composes typed Application contracts and Infrastructure adapters. QML uses Presentation bridges. The compiled launcher validates its private manifest and boots private Python. |
| Media | The workspace owns session source selection and edit recipes. The source Viewer owns authoritative painting and playback. Export uses original input and a separate processing path with validated staging and publication. |
| Dub | The shared workspace passes the physical source. Media recipes and preview assets never become Dub inputs. Dub owns provider/timing/audio orchestration, uses explicit model availability and publishes through the validated result path. |
| Settings | Typed preferences and model services expose appearance, storage, component acquisition/repair and offline verification. QML does not own profile paths or installation decisions. |

Packaged writable state resolves to `Data/v2` beside the validated executable.
Managed FFmpeg and private runtimes authenticate their declared identities.
Optional components install through an explicit confirmation flow. Keep path
containment and user source protection, bounded GPU/child admission and exact
cleanup ownership. An unresolved native resource fails closed and can block
further admission. Engineering limits and supported storage/codec routes are
defined in the invariants and applicable decision records (private project reference).

For the current Crop, Resize, Viewer, Settings and native publication decisions,
start with Release Evidence (private project reference). See
[Development and recovery](DEVELOPMENT.md#recovery) to replay a preserved original
with its relative path and historic hashes intact.

## Detailed contracts

The [invariants](PROJECT_INVARIANTS.md) define mandatory runtime, resource,
publication and data ownership. Release Evidence (private project reference) traces
accepted decisions and the scope of qualification. Earlier architecture editions
are preserved in the historical archive (private project reference).

# MIGRATION_ALLOWLIST

Основа: `LEGACY_AUDIT.md`. Legacy переносится только allowlist-подходом. Наличие файла в Legacy само по себе не означает право на миграцию.

## REUSE

Переносится с максимальным сохранением поведения; structural cleanup выполняется после characterization.

| Legacy | Target |
|---|---|
| `core/engines/clipper.py` | `features/media/export/clipper.py` |
| `core/export_plan.py` | `features/media/export/plan.py` |
| `core/image_sequences.py` | `features/media/sequences/` |
| `core/media_metadata.py` | `features/media/metadata/` |
| `core/video_color.py` | `features/media/metadata/color.py` |
| `core/file_safety.py` | `infrastructure/filesystem/publication.py` |
| `core/dubbing_audio.py` | `features/dub/audio/` |
| `core/dubbing_cache.py` | `infrastructure/cache/` или Dub-specific cache adapter |

FFmpeg command-building, temporary staging и non-replacing publication должны сохранять Legacy semantics. Audit подтверждает их текущую роль в Media pipeline.

---

## REFACTOR

Legacy logic используется, но старые архитектурные зависимости не переносятся.

| Legacy | Target |
|---|---|
| `core/engines/dubbing.py` | `features/dub/pipeline/` |
| `core/thread_workers.py` | разделить между `application/jobs/` и presentation executor adapter |
| `core/local_translation.py` | `features/dub/providers/translation/local.py` + infrastructure model adapter |
| `core/local_voice.py` | `features/dub/providers/voice/piper.py` |
| Edge TTS logic из Dub stack | `features/dub/providers/voice/edge_tts.py` |
| `core/model_manager.py` | `infrastructure/models/manager.py` |
| `core/model_removal.py` | `infrastructure/models/manager.py` |
| `core/dubbing_log.py` | `infrastructure/logging/` + Dub diagnostics |
| `core/settings.py` | `infrastructure/settings/` |
| `core/paths.py` | `infrastructure/resources/paths.py` |
| `core/i18n.py` | presentation/application localization service |
| `main.py` | только reference для bootstrap/self-check behaviour; новый `bootstrap/` создаётся отдельно |

Ключевые обязательные refactor:

- убрать `QThread` из backend contracts;
- отделить TranslationProvider/VoiceProvider;
- убрать SystemForge/VFXForge settings;
- создать новый profile namespace;
- убрать UI ownership FFmpeg paths;
- не переносить старый global application shell.

---

## REFERENCE ONLY

Не копируются как foundation. Разрешено изучать behaviour/UX/edge cases.

```text
ui/main_window.py
ui/settings_dialog.py
ui/tabs/clipper_tab.py
ui/tabs/dubbing_tab.py
ui/widgets/*
core/frame_buffer.py
core/version.py
core/shortcuts.py
MediaForge.spec
tools/package_release.py
tools/release_layout.py
tools/fetch_sources.py
tools/fetch_upstream_sources.py
tools/fetch_qt_sources.py
tools/prepare_public_source.py
.github/workflows/ffmpeg-candidate.yml
requirements.txt
requirements-dubbing.txt
requirements-dev.txt
requirements-lock.txt
assets/locales/ru.json
assets/locales/en.json
styles/icons/resources из Legacy
```

`core/frame_buffer.py` остаётся reference до проверки применимости к новому Qt Quick video presentation.

Legacy packaging используется для понимания hidden imports, self-check, notices и release layout, но новый package configuration собирается под новую repository structure.

---

## DEFER — REDUCE NOISE

Это MediaForge functionality, но не первая migration wave.

Не мигрировать сейчас:

```text
core/noise_session.py
core/noise_components.py
core/ofx_connection.py
```

Также не мигрируются сейчас связанные Natron/Neat scripts, probes и dependencies.

Файлы сохраняются как reference для будущей Reduce Noise phase.

---

## DO NOT MIGRATE — SYSTEMFORGE

```text
core/disk_cleaning.py
core/installed_apps.py
core/app_remnants.py
core/uninstall_analysis.py
core/uninstall_history.py

ui/tabs/disk_tools_tab.py
ui/tabs/disk_cleaning_tab.py
ui/tabs/uninstaller_tab.py
ui/widgets/cleanup_review.py
```

Также не мигрируются SystemForge-specific tests/settings/resources.

---

## DO NOT MIGRATE — VFXFORGE

```text
core/shot_setup.py
ui/tabs/shot_setup_tab.py
Nuke Templates/
```

Не переносится `core.paths.nuke_templates_dir()` или любая Nuke-specific configuration.

Также не мигрируются Shot Setup/Nuke tests и acceptance probes.

---

## DO NOT MIGRATE — LEGACY / DEAD-CODE CANDIDATES

Audit отдельно выделяет:

```text
ui/tabs/disk_clean_tab.py
ui/tabs/nuke_tools_tab.py
ui/tabs/settings_dialog.py
core/engines/dubbing — копия.py
```

Эти файлы не используются как migration source без отдельного доказательства необходимости.

Дополнительно:

```text
core/translation.py
```

не переносится в Dub v1: Audit указывает его как legacy Google-web translator, тогда как active Dub использует `LocalTranslator`.

---

## TEST ALLOWLIST — FIRST WAVE

Legacy Audit подтверждает наличие 30 `tests/test_*.py` modules, но **не приводит их точные filenames**. Поэтому конкретные имена нельзя достоверно зафиксировать из предоставленного документа.

После просмотра реального `tests/` в Legacy в REUSE/REFACTOR test allowlist должны попасть только модули, покрывающие:

### REUSE / ADAPT

```text
clipping / export planning
image sequences
media metadata
frame buffer — только если используется новым presentation
dubbing pipeline
dubbing audio
dubbing cache/logging
model management
local translation
local voice
settings
i18n
launcher/self-check — адаптировать
release preparation — адаптировать
```

Эти tests не копируются механически: imports/paths/UI assumptions адаптируются к новой architecture.

### REFERENCE ONLY / LATER

```text
noise
OFX
Natron
```

Они сохраняются до Reduce Noise phase.

### DO NOT MIGRATE

```text
disk cleanup
uninstaller
installed applications/remnants
Shot Setup
Nuke-related acceptance/probes
```

Audit также упоминает отдельные `tests/natron_*` и `shot_setup_*`; первые откладываются, вторые относятся к VFXForge.

---

## First-Wave Migration Rule

Первая wave не должна одновременно менять поведение и архитектуру reusable modules.

Для каждого REUSE-модуля порядок:

```text
Legacy behaviour identified
→ relevant tests identified
→ module copied into target ownership
→ imports/paths minimally adapted
→ characterization passes
→ architectural refactor separately
```

Любой Legacy-файл, которого нет в этом allowlist, по умолчанию считается **NOT APPROVED FOR MIGRATION** до отдельного Architect decision.

Ready for Builder?
Да — информации уже достаточно для создания TASK-001.
Единственный технический пробел — точные filenames Legacy pytest-модулей. Он не блокирует TASK-001, если первый task начинает новую foundation/repository architecture и перед переносом tests требует инвентаризировать фактическое содержимое Legacy tests/ и сопоставить его с test allowlist выше.
То есть архитектурных решений больше не не хватает. С этого момента TASK-001 уже можно делать узким и исполнимым, не перекладывая архитектурные решения на Builder.
