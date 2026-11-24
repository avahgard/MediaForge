import ast
import importlib.util
from pathlib import Path

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.contracts.resize import ResizeJob
from mediaforge.presentation.bridge.resize import ResizeViewModel


def _resolved_import_modules(source_text, package):
    modules = []
    for node in ast.walk(ast.parse(source_text)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative_name = "." * node.level + (node.module or "")
                module = importlib.util.resolve_name(relative_name, package)
            else:
                module = node.module or ""
            if module:
                modules.append(module)
                modules.extend(f"{module}.{alias.name}" for alias in node.names
                               if alias.name != "*")
    return modules


def _feature_imports(source_text, package):
    return [module for module in _resolved_import_modules(source_text, package)
            if module == "mediaforge.features" or module.startswith("mediaforge.features.")]


def source(width=641, height=361):
    return MediaMetadataDto("a.mp4", str(Path("C:/a.mp4")), "mp4", 2.0, "h264", width, height, 25)


class UseCase:
    def __init__(self):
        self.busy = False
        self.job = ResizeJob()
        self.starts = []
        self.cancelled = False
        self.sources = []

    def set_source(self, value): self.sources.append(value); return True
    def start(self, width, height): self.starts.append((width, height)); return True
    def cancel(self): self.cancelled = True; return True
    def snapshot(self): return self.job
    def shutdown(self): pass


def test_no_source_disables_resize():
    vm = ResizeViewModel(UseCase())
    assert not vm.canResize and not vm.canEdit


def test_source_initializes_largest_even_dimensions():
    vm = ResizeViewModel(UseCase()); vm.set_source(source())
    assert vm.sourceDimensions == "641 × 361"
    assert (vm.widthText, vm.heightText) == ("640", "360") and vm.canResize


def test_width_and_height_are_independent_and_validate_exact_limits():
    vm = ResizeViewModel(UseCase()); vm.set_source(source(640, 360))
    vm.widthText = "320"
    assert vm.heightText == "360"
    vm.heightText = "321"; assert not vm.canResize
    vm.heightText = "320"; assert vm.canResize
    assert vm.startResize() and vm._use_case.starts == [(320, 320)]


def test_running_cancelling_and_terminal_retry_state():
    use = UseCase(); vm = ResizeViewModel(use); vm.set_source(source())
    use.busy = True; use.job = ResizeJob(state="Running", message="Resize running…"); vm.refresh()
    assert vm.busy and vm.canCancel and not vm.canEdit
    use.job = ResizeJob(state="Cancelling", message="Cancelling Resize…"); vm.refresh()
    assert not vm.canCancel
    use.busy = False; use.job = ResizeJob(state="Cancelled", message="Resize cancelled."); vm.refresh()
    assert vm.canResize


def test_success_exposes_path_and_exact_dimensions():
    use = UseCase(); vm = ResizeViewModel(use); vm.set_source(source())
    use.job = ResizeJob(state="Completed", result="C:/a_resize.mp4", width=320, height=180)
    vm.refresh()
    assert vm.outputPath == "C:/a_resize.mp4" and vm.resultDimensions == "320 × 180"


def test_new_source_resets_target_and_terminal_state():
    use = UseCase(); vm = ResizeViewModel(use); vm.set_source(source())
    vm.widthText = "320"; use.job = ResizeJob(state="Failed", error="process_failed"); vm.refresh()
    vm.set_source(source(1281, 721))
    assert (vm.widthText, vm.heightText, vm.state) == ("1280", "720", "Pending")


def test_resize_presentation_has_no_direct_feature_dependency():
    offenders = []
    source_root = Path("src")
    for path in (source_root / "mediaforge/presentation").rglob("*.py"):
        module_parts = path.relative_to(source_root).with_suffix("").parts
        package = ".".join(module_parts[:-1])
        imports = _feature_imports(path.read_text(encoding="utf-8"), package)
        if imports:
            offenders.append((path, imports))
    assert offenders == []


def test_boundary_detector_rejects_absolute_presentation_to_feature_import():
    imports = _feature_imports(
        "from mediaforge.features.media.resize import X",
        "mediaforge.presentation.bridge",
    )
    assert "mediaforge.features.media.resize" in imports


def test_boundary_detector_rejects_relative_presentation_to_feature_import():
    imports = _feature_imports(
        "from ...features.media.resize import X",
        "mediaforge.presentation.bridge",
    )
    assert "mediaforge.features.media.resize" in imports


def test_boundary_detector_allows_application_contract_import():
    imports = _feature_imports(
        "from mediaforge.application.contracts.resize import X",
        "mediaforge.presentation.bridge",
    )
    assert imports == []
