"""Windows Explorer exact-source selection with separated program/arguments."""
import os
from pathlib import Path
import subprocess
from mediaforge.application.contracts.source_location import RevealResult


class WindowsSourceLocationRevealer:
    def __init__(self, *, process_factory=subprocess.Popen):
        self._factory = process_factory
        self._explorer = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'explorer.exe')

    def reveal(self, source_path):
        try:
            source = Path(source_path)
            if not source.is_absolute() or not source.is_file():
                return RevealResult(False, 'Source file is unavailable.')
            source = source.resolve(strict=True)
            self._factory([self._explorer, '/select,', str(source)], shell=False)
            return RevealResult(True)
        except (OSError, ValueError, TypeError):
            return RevealResult(False, 'Source location could not be opened.')

    def reveal_output(self, output_path):
        """Open the result's folder; source-file selection stays unchanged."""
        try:
            path=Path(output_path)
            if not path.is_absolute():return RevealResult(False,'Output folder is unavailable.')
            folder=path if path.is_dir() else path.parent
            if not folder.is_dir():return RevealResult(False,'Output folder is unavailable.')
            self._factory([self._explorer,str(folder.resolve(strict=True))],shell=False)
            return RevealResult(True)
        except (OSError,ValueError,TypeError):return RevealResult(False,'Output location could not be opened.')
