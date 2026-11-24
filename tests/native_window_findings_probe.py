"""C1-F01/F02 native regressions; topology changes are discovery-only simulations."""
import argparse
import importlib
import json
import logging
from pathlib import Path
import sys

import shiboken6
from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtTest import QTest
import native_window_policy as nw


HARNESSES = ("pc2_dispatch_native", "native_workspace_regressions", "pc2_frame_native",
             "pc2_index_native", "pc2_workspace_native", "pc2_origin_shutdown_native",
             "pc3_closure_native", "trim_admission_native", "check_workspace_runtime")


def save(output, facts):
    (output / "results.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
    print(json.dumps(facts), flush=True)


def placement_cleanup(output, name, force_error):
    harness = importlib.import_module(name)
    captured, geometry_methods = {}, []
    build, load, resize = harness.build_media_view_model, harness.load_shell, harness.resize_native_window
    def capture_build(logger):
        model = captured["model"] = build(logger)
        captured["player"], captured["audio"] = model.playback._player, model.playback._audio
        return model
    def shrink_after_load(engine, source, logger):
        captured["engine"] = engine
        window = captured["window"] = load(engine, source, logger)
        assert window.isVisible() and shiboken6.isValid(captured["player"])
        if not force_error:
            for screen in QGuiApplication.instance().screens():
                original = screen.availableGeometry
                geometry_methods.append((screen, original))
                g = original()
                screen.availableGeometry = lambda g=g: QRect(g.x(), g.y(), 800, 700)
        return window
    def fail_resize(*_):
        raise AssertionError("C1 forced placement error")
    harness.build_media_view_model, harness.load_shell = capture_build, shrink_after_load
    if force_error:
        harness.resize_native_window = fail_resize
    argv = sys.argv
    try:
        try:
            if name == "native_workspace_regressions":
                sys.argv = [name, "--mode", "icons", "--fixtures", str(output), "--output", str(output)]
                harness.main()
            elif name == "check_workspace_runtime":
                # Existing argparse only requires file existence; failure must
                # happen before media use. No fixture is generated or opened.
                sys.argv = [name, "--output", str(output)]
                for flag in ("landscape", "portrait", "cfr", "spaces", "unicode", "vfr"):
                    sys.argv += ["--" + flag, __file__]
                harness.main()
            else:
                harness.run(output, output)
        except BaseException as error:
            if force_error:
                assert type(error) is AssertionError and str(error) == "C1 forced placement error", repr(error)
            else:
                assert isinstance(error, nw.NativeScreenUnavailable) and error.code == 77, repr(error)
            facts = {"harness": name, "outcome": "error" if force_error else "skip",
                     "player_valid": shiboken6.isValid(captured["player"]),
                     "audio_valid": shiboken6.isValid(captured["audio"]),
                     "engine_valid": shiboken6.isValid(captured["engine"]),
                     "window_visible": shiboken6.isValid(captured["window"]) and captured["window"].isVisible()}
            save(output, facts)
            assert not any(facts[key] for key in ("player_valid", "audio_valid", "engine_valid", "window_visible"))
        else:
            raise AssertionError("Requested-size failure was not propagated")
    finally:
        for screen, original in geometry_methods:
            screen.availableGeometry = original
        harness.build_media_view_model, harness.load_shell, harness.resize_native_window = build, load, resize
        sys.argv = argv
        # Probe-owned fallback cleanup does not establish the assertions above.
        model = captured.get("model")
        if model is not None and shiboken6.isValid(model):
            model.shutdown()
        window = captured.get("window")
        if window is not None:
            nw.close_native_windows(window)
        for key in ("engine", "audio"):
            obj = captured.get(key)
            if obj is not None and shiboken6.isValid(obj):
                shiboken6.delete(obj)


def owner_relocation(output, transient):
    app = QGuiApplication([])
    manager = nw.policy()
    primary = app.primaryScreen()
    secondary = next((s for s in app.screens() if s != primary), None)
    if secondary is None:
        raise nw.NativeScreenUnavailable("owner relocation regression requires two real QScreens")
    original = secondary.availableGeometry
    owner, follower = QWindow(), QWindow()
    try:
        owner.resize(1440, 900)
        manager.place(owner, screen=secondary)
        owner.show()
        follower.resize(180, 100)
        if transient:
            follower.setTransientParent(owner)
        else:
            nw.place_companion(follower, owner)
        follower.show()
        assert QTest.qWaitForWindowExposed(follower, 3000)
        assert follower.screen() == owner.screen() == secondary
        assert follower.transientParent() == (owner if transient else None)
        if transient:
            follower.hide()
        # Registration order cannot dictate which owner is relocated first.
        manager.windows.reverse()
        g = original()
        secondary.availableGeometry = lambda: QRect(g.x(), g.y(), 800, 700)
        manager.reassess()
        assert manager.failure is None and owner.screen() == primary
        if transient:
            follower.show()
        assert QTest.qWaitForWindowExposed(follower, 3000)
        assert follower.screen() == owner.screen()
        assert follower.screen().availableGeometry().contains(follower.frameGeometry())
        # Also exercise re-Show of an already registered follower after moving
        # its hidden native handle back to the former screen.
        follower.hide()
        follower.setScreen(secondary)
        follower.setPosition(secondary.availableGeometry().topLeft())
        follower.show()
        assert QTest.qWaitForWindowExposed(follower, 3000)
        assert follower.screen() == owner.screen() == primary
        assert follower.transientParent() == (owner if transient else None)
        save(output, {"kind": "transient" if transient else "independent-companion",
                      "owner": owner.screen().name(), "follower": follower.screen().name(),
                      "ownership_preserved": True, "repeated_show": True,
                      "real_disconnect": "not physically exercised"})
    finally:
        secondary.availableGeometry = original
        manager.close_windows()
        shiboken6.delete(follower)
        shiboken6.delete(owner)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("resize-skip", "resize-error", "companion", "transient"))
    parser.add_argument("--harness", choices=HARNESSES, default="pc2_dispatch_native")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode.startswith("resize"):
        placement_cleanup(args.output.resolve(), args.harness, args.mode == "resize-error")
    else:
        owner_relocation(args.output.resolve(), args.mode == "transient")
