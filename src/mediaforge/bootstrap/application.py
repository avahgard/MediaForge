"""Sole composition root for the foundation application."""

from importlib.metadata import version
import os
from pathlib import Path
import sys
from time import perf_counter

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from mediaforge.application.services.source_timeline import SourceTimelineService
from mediaforge.infrastructure.ffmpeg.source_timeline import FfprobeTimelineIndexer
from mediaforge.application.services.media import OpenMediaService
from mediaforge.application.services.images import ImageSourceService
from mediaforge.infrastructure.images.reader import QtImageReader
from mediaforge.infrastructure.images.resources import WindowsPreviewResources
from mediaforge.application.services.trim import TrimService
from mediaforge.application.services.crop import CropService
from mediaforge.application.services.resize import ResizeService
from mediaforge.application.services.export import ExportService
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.infrastructure.ffmpeg.crop import FfmpegCropExecutor
from mediaforge.infrastructure.ffmpeg.resize import FfmpegResizeExecutor
from mediaforge.infrastructure.ffmpeg.trim import FfmpegTrimExecutor
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.source_location import WindowsSourceLocationRevealer
from mediaforge.infrastructure.ffmpeg import FfprobeMediaMetadataProbe
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntimeError, resolve_ffmpeg_runtime
from mediaforge.infrastructure.logging.startup import close_logging, configure_logging
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.presentation.bridge.media import MediaViewModel
from mediaforge.presentation.bridge.playback import PlaybackController


RESOURCE_SHUTDOWN_FAILED = 2  # Portable launcher diagnostic.


def load_shell(engine: QQmlApplicationEngine, source: Path, logger) -> QWindow:
    engine.warnings.connect(
        lambda errors: [logger.error("QML: %s", error.toString()) for error in errors]
    )
    if not source.is_file():
        raise RuntimeError(f"Root QML is missing: {source}")
    resolved = source.resolve()
    if os.name == "nt" and not resolved.name.isascii():
        # Qt 6.11.2 on Windows can crash retiring a scene whose URL has a
        # non-ASCII filename. Keep the real directory for relative imports.
        base = QUrl.fromLocalFile(str(resolved.with_name("MediaForgeShell.qml")))
        engine.loadData(resolved.read_bytes(), base)
    else:
        engine.load(QUrl.fromLocalFile(str(resolved)))
    roots = engine.rootObjects()
    if not roots or not isinstance(roots[0], QWindow):
        raise RuntimeError(f"Root QML did not create an application window: {source}")
    return roots[0]


_UNRESOLVED = object()


def _resolve_runtime(profile, logger):
    try:
        return resolve_ffmpeg_runtime(logger=logger, profile=profile)
    except FfmpegRuntimeError as error:
        logger.warning("FFmpeg runtime unavailable (%s): %s", error.code.value, error)
        return None


def build_media_view_model(logger, *, profile=None, gate=None, runtime=_UNRESOLVED) -> MediaViewModel:
    if runtime is _UNRESOLVED:
        runtime = _resolve_runtime(profile or ProfilePaths.from_environment(), logger)
    probe = FfprobeMediaMetadataProbe(runtime.ffprobe if runtime else None, logger=logger)
    gate = gate or MediaProcessingGate()
    trim_service = TrimService(FfmpegTrimExecutor(runtime, logger=logger), logger=logger, gate=gate)
    crop_service = CropService(FfmpegCropExecutor(runtime, logger=logger), logger=logger, gate=gate)
    resize_service = ResizeService(FfmpegResizeExecutor(runtime, logger=logger), logger=logger, gate=gate)
    images=ImageSourceService(QtImageReader(), resources=WindowsPreviewResources())
    export_service = ExportService(FfmpegExportExecutor(runtime, logger=logger,image_service=images), logger=logger, gate=gate)
    images.result_preparer=export_service
    service=OpenMediaService(probe,logger=logger,result_preparer=export_service)
    from mediaforge.infrastructure.filesystem.image_output import ReadLocks
    from mediaforge.presentation.bridge.video_posters import VideoPosters
    def poster_source(path):
        owner=ReadLocks()
        try:
            owner.parents(Path(path).parent)
            owner.add(path)
            return owner
        except BaseException:
            owner.close()
            raise
    playback = PlaybackController(
        QMediaPlayer(), QAudioOutput(), logger=logger, player_factory=QMediaPlayer,
        timeline_service=SourceTimelineService(FfprobeTimelineIndexer(runtime.ffprobe if runtime else None, decoder_threads=4)),
        posters=VideoPosters(poster_source,lambda peak:images.check_pressure(incoming_cpu=peak))
    )
    return MediaViewModel(service, logger=logger, trim_use_case=trim_service,
                          crop_use_case=crop_service, resize_use_case=resize_service,
                          playback_controller=playback, source_revealer=WindowsSourceLocationRevealer(),
                          export_use_case=export_service,
                          image_service=images,processing_gate=gate)


def build_dub_view_models(profile,gate,logger,*,runtime=_UNRESOLVED):
    from mediaforge.bootstrap.dub_models import compose_dub_models
    from mediaforge.bootstrap.speech import compose_speech
    from mediaforge.infrastructure.models.qwen_tts import QwenTts
    from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
    from mediaforge.infrastructure.ffmpeg.dub_export import DubExportExecutor
    from mediaforge.application.services.dub import DubService
    from mediaforge.infrastructure.filesystem.dub_source import destination_plan
    from mediaforge.application.services.voice_preview import VoicePreviewService
    from mediaforge.presentation.bridge.dub import DubViewModel
    from mediaforge.presentation.bridge.models import ModelsViewModel
    from mediaforge.infrastructure.settings.preferences import PortablePreferences
    from mediaforge.infrastructure.audio.preview_format import PreviewAudioConverter
    from mediaforge.presentation.bridge.voice_preview import VoicePreviewPlayer
    if runtime is _UNRESOLVED:
        runtime = _resolve_runtime(profile, logger)
    components=compose_dub_models(profile,gate)
    tts=QwenTts(profile,components.requirements.qwen)
    speech=compose_speech(components.local_ai,runtime,tts=tts)
    service=DubService(FfprobeDubInspector(runtime,profile,timeout=3600,qualified_mux_metadata=True),speech,
        DubExportExecutor(profile,runtime),gate,tts.voices,destination_planner=destination_plan)
    preview=VoicePreviewService(tts.open,tts.binding,tts.voices,gate)
    models=ModelsViewModel(components.operations)
    return DubViewModel(service,preview,models,preferences=PortablePreferences(profile),
                       player=VoicePreviewPlayer(converter=PreviewAudioConverter())),models


def main(argv: list[str] | None = None, *, prepared_profile=None, startup=None) -> int:
    # Avoid Qt's default persistent QML cache outside the isolated v2 profile.
    # This small foundation does not need a disk compilation cache.
    marks = [] if startup is None else startup
    def mark(stage):
        marks.append((stage, perf_counter()))
    mark('bootstrap entered')
    try:
        profile = prepared_profile
        if profile is None:
            profile = ProfilePaths.from_environment()
            profile.activate_temporary()
        # Owned Media children inherit this explicit portable working directory.
        # All resources and selected sources are already absolute/cwd-independent.
        os.chdir(profile.sessions)
        mark('profile ready')
    except ValueError as error:
        print(f'MediaForge startup failed: {error}', file=sys.stderr)
        return 1
    os.environ["QML_DISABLE_DISK_CACHE"] = "1"
    app = QGuiApplication(sys.argv if argv is None else argv)
    mark('Qt application created')
    app.setApplicationName("MediaForge")
    app.setOrganizationName("MediaForge")
    app.setApplicationVersion(version("mediaforge"))
    logger = None
    engine = None
    media_view_model = None
    dub_view_model = models_view_model = None
    result = 1
    try:
        logger = configure_logging(profile)
        logger.info("Starting MediaForge %s", app.applicationVersion())
        engine = QQmlApplicationEngine()
        gate=MediaProcessingGate()
        runtime = _resolve_runtime(profile, logger)
        mark('managed FFmpeg verified')
        media_view_model = build_media_view_model(logger, profile=profile,gate=gate,runtime=runtime)
        mark('Media constructed')
        dub_view_model,models_view_model=build_dub_view_models(profile,gate,logger,runtime=runtime)
        mark('Dub and Models constructed')
        from mediaforge.presentation.bridge.navigation import Navigation
        from mediaforge.presentation.bridge.settings import SettingsViewModel
        from mediaforge.infrastructure.settings.build_identity import build_identity
        from mediaforge.infrastructure.settings.preferences import ThemePreference
        navigation=Navigation(media_view_model,dub_view_model,models_view_model)
        core_guard = getattr(sys, 'mediaforge_core_guard', None)
        settings_view_model = SettingsViewModel(profile, ThemePreference(profile), build_identity(core_guard),
                                                app.applicationVersion(), portable=core_guard is not None, parent=app)
        def shutdown_features():
            # Cancel all owners first, then bounded retirement. Do not dispose a
            # shared native/temporary owner while another feature still runs.
            dub_view_model.cancel();models_view_model.cancel()
            errors=[]
            for model in (models_view_model,dub_view_model,media_view_model):
                try:model.shutdown()
                except Exception as error:errors.append(error)
            if errors:raise RuntimeError('Application resource retirement failed.') from errors[0]
        def shutdown_from_qt():
            try:
                shutdown_features()
            except Exception:
                # Qt slot exceptions do not propagate out of app.exec(). The
                # model retains this failure for the top-level exit decision.
                logger.exception("Application shutdown failed in aboutToQuit")
                app.setProperty('resourceShutdownFailed',True)
        app.aboutToQuit.connect(shutdown_from_qt)
        engine.rootContext().setContextProperty("mediaViewModel", media_view_model)
        engine.rootContext().setContextProperty("dubViewModel", dub_view_model)
        engine.rootContext().setContextProperty("modelsViewModel", models_view_model)
        engine.rootContext().setContextProperty("settingsViewModel", settings_view_model)
        engine.rootContext().setContextProperty("appNavigation", navigation)
        window = load_shell(engine, qml_root(), logger)
        mark('QML loaded')
        logger.info("QML shell loaded")
        def report_startup():
            mark('GUI event loop serviced')
            origin = marks[0][1]
            logger.info("Startup QPC %.6f %s", origin,
                        ", ".join(f'{stage}={(stamp-origin)*1000:.1f}ms' for stage,stamp in marks))
        QTimer.singleShot(0, report_startup)
        if hasattr(window, 'frameSwapped'):
            swap_observed = False
            def first_swap():
                nonlocal swap_observed
                if not swap_observed:
                    swap_observed = True
                    window.frameSwapped.disconnect(first_swap)
                    logger.info("Startup first scene swap QPC %.6f", perf_counter())
            window.frameSwapped.connect(first_swap, Qt.ConnectionType.QueuedConnection)
        result = app.exec()
        if media_view_model.shutdown_error is not None or app.property('resourceShutdownFailed'):
            result = RESOURCE_SHUTDOWN_FAILED
    except Exception as error:
        if logger is not None:
            logger.exception("Fatal startup error")
        else:
            print(f"MediaForge startup failed: {error}", file=sys.stderr)
        result = 1
    finally:
        for model in (models_view_model,dub_view_model):
            if model is not None:
                try:model.shutdown()
                except Exception:
                    result=RESOURCE_SHUTDOWN_FAILED
                    if logger is not None:logger.exception('Dub/Models shutdown failed')
        if media_view_model is not None:
            try:
                media_view_model.shutdown()
            except Exception:
                result = RESOURCE_SHUTDOWN_FAILED
                if logger is not None:
                    logger.exception("Application shutdown failed")
        if engine is not None:
            # QML bindings must retire while their Python context objects
            # still exist, even if a window wrapper outlives this function.
            import shiboken6
            if shiboken6.isValid(engine):
                shiboken6.delete(engine)
        if logger is not None:
            if media_view_model is None or media_view_model.shutdown_error is None:
                logger.info("Application shutdown: %s", result)
            close_logging(logger)
    return result
