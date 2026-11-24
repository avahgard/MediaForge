"""Exercise the production Settings route using targeted Qt window input."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
import traceback

from PySide6.QtCore import QMimeData, QPoint, QPointF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAccessible, QColor, QGuiApplication, QWheelEvent
from PySide6.QtTest import QTest

from mediaforge.bootstrap import application
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.settings.preferences import ThemePreference
from native_window_policy import load_native_shell, resize_native_window


def wait_for(predicate, description, seconds=5):
    deadline = monotonic() + seconds
    while not predicate() and monotonic() < deadline:
        # Service workers must get the GIL while QTest pumps the GUI thread.
        sleep(0.01)
        QTest.qWait(10)
    assert predicate(), description


def check_settings(window, engine, evidence, *, compact_only):
    app = QGuiApplication.instance()
    native = app.platformName() == 'windows'
    nav = engine.rootContext().contextProperty('appNavigation')
    models = engine.rootContext().contextProperty('modelsViewModel')
    settings = engine.rootContext().contextProperty('settingsViewModel')
    media = engine.rootContext().contextProperty('mediaViewModel')
    dub = engine.rootContext().contextProperty('dubViewModel')
    assert settings.theme == 'System'
    assert [row['status'] for row in models.components] == ['Not installed'] * 6
    assert models.operation['generation'] == 0

    if native:
        window.requestActivate()
        wait_for(window.isActive, 'The test window needs activation for the keyboard focus check.')

    def find(name, start=None):
        def children(item):
            if item.objectName() == name:
                return item
            return next((result for child in item.childItems() if (result := children(child)) is not None), None)
        return children(start or window.contentItem())

    def item(name):
        result = find(name)
        assert result is not None, name
        return result

    def point(control):
        return control.mapToScene(QPointF(control.width() / 2, control.height() / 2)).toPoint()

    def click(control):
        assert control.isVisible() and control.isEnabled(), control.objectName()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point(control))
        QTest.qWait(30)

    def key(value, modifiers=Qt.NoModifier):
        QTest.keyClick(window, value, modifiers)
        QTest.qWait(30)

    def focused(name):
        focus = window.activeFocusItem()
        assert focus and focus.objectName() == name, (name, focus.objectName() if focus else None)

    def wheel(control, delta):
        local = point(control)
        event = QWheelEvent(QPointF(local), QPointF(window.mapToGlobal(local)), QPoint(), QPoint(0, delta),
                            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        QGuiApplication.sendEvent(window, event)
        QTest.qWait(80)

    def capture(name):
        if native:
            image = window.grabWindow()
            assert not image.isNull() and image.save(str(evidence / f'{name}.png'))

    click(item('navigationSettings'))
    page = item('settingsPage')
    assert page.property('selectedTab') == 1
    focused('settingsTabDubModels')
    assert models.operation['generation'] == 0
    sizes = [(800, 560)] if compact_only else [(1440, 900), (1280, 720), (1024, 768), (800, 560)]
    measured = []
    for width, height in sizes:
        if native:
            resize_native_window(window, width, height)
        else:
            window.resize(width, height)
        QTest.qWait(80)
        tab = item('settingsTabDubModels')
        assert (window.width(), window.height()) == (width, height)
        assert tab.height() == 36
        viewport = item('settingsModelInventory')
        assert viewport.height() >= 88 and viewport.y() + viewport.height() <= page.height()
        capture(f'models-{width}x{height}')
        measured.append(dict(width=width, height=height, dpr=window.devicePixelRatio()))

    viewport = item('settingsModelInventory')
    assert viewport.property('contentY') == 0
    wheel(viewport, -360)
    wait_for(lambda: viewport.property('contentY') > 0, 'model inventory mouse wheel')
    wait_for(lambda: not viewport.property('moving'), 'model inventory settles')
    offset = viewport.property('contentY')
    click(item('navigationMedia'))
    click(item('navigationSettings'))
    assert abs(viewport.property('contentY') - offset) < 1, (offset, viewport.property('contentY'))
    key(Qt.Key_Tab)
    focused('installDubModelsButton')
    key(Qt.Key_Tab)
    focused('settingsVerifyOffline')
    key(Qt.Key_Tab)
    focused('settingsModel-dub-cpu-runtime')
    key(Qt.Key_End)
    wait_for(lambda: viewport.property('contentY') == viewport.property('end'), 'inventory End key')
    last = item('settingsModel-qwen3-tts-tokenizer-12hz')
    click(last)
    assert last.property('text') == 'Qwen 12 Hz Tokenizer'
    assert viewport.property('contentHeight') > 6 * 44
    interface = QAccessible.queryAccessibleInterface(last)
    assert interface is not None and interface.role() == QAccessible.Role.Button
    assert 'Expanded' in interface.text(QAccessible.Text.Description)

    click(item('installDubModelsButton'))
    wait_for(lambda: not models.busy and models.operation['state'] != 'running', 'plan worker settled')
    assert models.plan.get('figures'), (models.operation, models.message)
    wait_for(lambda: find('settingsConsentCancel') is not None
             and find('settingsConsentCancel').isVisible(), 'plan popup visible')
    wait_for(lambda: window.activeFocusItem().objectName() == 'settingsConsentCancel', 'Cancel-first focus')
    QTest.qWait(40)
    capture('consent-cancel-focus')
    body, button = item('settingsConsentBody'), item('settingsConsentConfirm')
    assert len(models.plan['steps']) == 6 and 'Hugging Face' in models.plan['network']
    wait_for(lambda: body.property('end') > 0, 'consent details lay out')
    footer = point(button)
    wheel(body, -480)
    wait_for(lambda: body.property('contentY') > 0,
             f"consent mouse wheel at {point(body)} with end {body.property('end')}")
    body.forceActiveFocus(Qt.TabFocusReason)
    key(Qt.Key_End)
    assert body.property('contentY') == body.property('end')
    assert point(button) == footer and button.isVisible()
    capture('consent-scrolled')
    key(Qt.Key_Escape)
    wait_for(lambda: not models.plan and not button.isVisible(), 'Escape dismisses consent')
    focused('installDubModelsButton')

    click(item('settingsVerifyOffline'))
    wait_for(lambda: not models.busy and models.operation['state'] == 'completed', 'offline verification')
    assert models.message == 'Offline check complete. 0 of 6 components verified.'
    assert not any(ProfilePaths.from_environment().downloads.iterdir())
    click(item('settingsTabDubModels'))
    key(Qt.Key_Right)
    focused('settingsTabStorage')
    assert page.property('selectedTab') == 1
    key(Qt.Key_Return)
    wait_for(lambda: bool(settings.storage), 'portable storage measurement')
    path_field = find('settingsPathText', item('settingsDataPath'))
    path_field.forceActiveFocus(Qt.TabFocusReason)
    clipboard = app.clipboard()
    previous = None
    if native:
        previous = QMimeData()
        offered = clipboard.mimeData()
        if offered is not None:
            for representation in offered.formats():
                previous.setData(representation, offered.data(representation))
    try:
        key(Qt.Key_A, Qt.ControlModifier)
        key(Qt.Key_C, Qt.ControlModifier)
        assert clipboard.text() == settings.dataPath
    finally:
        if previous is not None and clipboard.text() == settings.dataPath:
            clipboard.setMimeData(previous)
    assert path_field.property('readOnly')
    capture('storage')

    click(item('settingsTabAbout'))
    assert item('settingsLogPath').property('value') == settings.logPath
    assert settings.about['build'] == 'Development checkout' and not settings.about['project']
    capture('about')
    click(item('settingsTabAppearance'))
    click(item('settingsThemeSystem'))
    key(Qt.Key_Right)
    assert settings.theme == 'Dark' and window.color() == QColor('#0B0D11')
    click(item('settingsThemeLight'))
    assert settings.theme == 'Light' and window.color() == QColor('#F3F5F8')
    capture('appearance-light')
    if native:
        # Exercise reversible application-local delivery of a scheme change.
        # No Windows preference or registry mutation is made by the harness.
        hints = app.styleHints()
        click(item('settingsThemeSystem'))
        hints.setColorScheme(Qt.ColorScheme.Dark)
        wait_for(lambda: window.color() == QColor('#0B0D11'), 'System follows Qt dark scheme')
        hints.setColorScheme(Qt.ColorScheme.Light)
        wait_for(lambda: window.color() == QColor('#F3F5F8'), 'System follows Qt light scheme')
        hints.setColorScheme(Qt.ColorScheme.Unknown)
        click(item('settingsThemeLight'))
    assert ThemePreference(ProfilePaths.from_environment()).load() == {'theme': 'Light'}

    if not compact_only and (ffprobe := os.environ.get('MEDIAFORGE_FFPROBE_PATH')):
        source = evidence / 'Media and Dub fixture.mp4'
        ffmpeg = Path(ffprobe).with_name('ffmpeg.exe')
        subprocess.run([str(ffmpeg), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=30',
                        '-f', 'lavfi', '-i', 'sine=frequency=1000', '-t', '1', '-c:v', 'libx264',
                        '-c:a', 'aac', str(source)], check=True, timeout=15)
        click(item('navigationMedia'))
        assert nav.openSource(QUrl.fromLocalFile(str(source)))
        wait_for(lambda: media.state == 'loaded', 'Media import and Viewer', seconds=12)
        media.selectOperation('output')
        export = item('exportStart')
        assert export.property('background').property('color') in (QColor('#2565C5'), QColor('#DDE2EA'))
        click(item('navigationDub'))
        wait_for(lambda: find('dubPrimary') is not None, 'shared source appears in Dub')
        wait_for(lambda: not dub.busy, 'Dub source inspection', seconds=12)
        dub_action = item('dubPrimary')
        assert not dub_action.isEnabled()
        assert dub_action.property('background').property('color') == QColor('#DDE2EA')
        click(item('navigationSettings'))
        assert page.property('selectedTab') == 0
        click(item('settingsThemeDark'))
        click(item('navigationMedia'))
        assert window.color() == QColor('#0B0D11')
        click(item('navigationDub'))
        assert item('dubPrimary').property('background').property('color') == QColor('#5AA9FF')
        click(item('navigationMedia'))
        signatures = []
        for index in (1, 2):
            stem = f'Repeat Export {index}'
            assert media.output.setSetting('folder', str(evidence))
            assert media.output.setSetting('filename', stem)
            wait_for(lambda: media.output.canExport, 'Export is available')
            click(item('exportStart'))
            wait_for(lambda: media.output.state == 'Completed', media.output.message, seconds=12)
            result = Path(media.output.outputPath)
            assert result.parent == evidence and result.stem == stem
            signatures.append(sha256(result.read_bytes()).hexdigest())
        assert len(set(signatures)) == 1 and signatures[0] == sha256(source.read_bytes()).hexdigest()
        assert not list(evidence.glob('.mediaforge-export-*'))

    (evidence / 'result.json').write_text(json.dumps(dict(platform=app.platformName(), geometry=measured,
        inventory_rows=6, offline_messages=models.message, theme=settings.theme), indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--compact-only', action='store_true')
    parser.add_argument('--restart', choices=['Dark', 'Light'])
    args = parser.parse_args()
    args.evidence.mkdir()
    failures = []
    loader = application.load_shell

    def load(engine, source, logger):
        use_native = QGuiApplication.instance().platformName() == 'windows'
        # The accepted placement helper calls the production loader; bypass this
        # wrapper for that inner call.
        application.load_shell = loader
        window = load_native_shell(engine, source, logger, size=QSize(800, 560)) if use_native else loader(engine, source, logger)
        def run():
            try:
                if args.restart:
                    settings = engine.rootContext().contextProperty('settingsViewModel')
                    assert settings.theme == args.restart
                    assert window.color() == QColor('#0B0D11' if args.restart == 'Dark' else '#F3F5F8')
                    (args.evidence / 'restart.json').write_text(json.dumps({'theme': settings.theme}), encoding='utf-8')
                else:
                    check_settings(window, engine, args.evidence, compact_only=args.compact_only)
            except Exception as error:
                failures.append(error)
                traceback.print_exc()
            finally:
                window.close()
        QTimer.singleShot(0, run)
        return window

    application.load_shell = load
    result = application.main(['mediaforge-settings-test'])
    return result or bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
