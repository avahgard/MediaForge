"""Explicit destination specialization of the existing safe staging contract."""
import os
from uuid import uuid4
from mediaforge.features.media.export import ExportError
from mediaforge.infrastructure.filesystem.staged_output import StagedOutput
from mediaforge.infrastructure.filesystem.confirmed_output import ConfirmedOutput


class ExportOutput(StagedOutput):
    def __init__(self, source, destination, logger=None, *, replacement=None, protected_sources=(), **kwargs):
        # Stage beside the destination, with its effective suffix, on its filesystem.
        super().__init__(destination, "export", ExportError, logger=logger, **kwargs)
        self.input = source
        self.destination = destination
        self.replacement = replacement
        self.protected_sources = protected_sources or (source,)
        self._guard = None
        self._validated = None
        self.staging = None
        self.cleanup_errors = []

    def __enter__(self):
        try:
            if self.replacement is not None:
                # Keep the owner even if acquisition or its failure cleanup raises.
                self._guard = ConfirmedOutput(self.destination,self.replacement)
                self._guard.__enter__()
            self.directory = self.destination.parent / f'.mediaforge-export-{uuid4().hex}'
            self.staged = self.directory / ('partial' + self.destination.suffix)
            from mediaforge.infrastructure.filesystem.export_staging import ExportStaging
            self.staging = ExportStaging(self.directory, self.staged)
            self.staging.create()
            return self
        except (OSError, AttributeError) as error:
            acquiring_staging = self.directory is not None
            self._cleanup()
            if acquiring_staging:
                raise ExportError('output_unavailable','Cannot acquire protected Export staging. Use a writable local folder.') from error
            raise ExportError('output_unavailable','Cannot lock replacement destination using Windows NTFS transactions. Use a new filename or a writable local NTFS folder; close programs using this file.') from error
        except Exception:
            self._cleanup()
            raise

    def __exit__(self,*args):
        self._cleanup()

    def _cleanup(self):
        # Resolve rollback AND raw-handle ownership before touching staging.
        if self._guard is not None and not self._guard.close():
            self._retain('Replacement transaction/handle cleanup is unresolved')
        if self._validated is not None:
            try:self._validated.close()
            except Exception:self._retain('Validated publication handle cleanup is unresolved')
        if not self.cleanup_guard():
            self._retain('Owned process termination is unresolved')
        try:
            if self.staging is not None:
                self.staging.close()
                self.directory = None
        except (OSError, ExportError) as error:
            self.cleanup_errors.append(str(error))
            self._retain(f'Owned Export staging cleanup failed: {error}')

    def _retain(self, reason):
        published = (self.staging is not None and self.staging.published) or (
            self._guard is not None and self._guard.committed)
        if published:
            outcome = f'Output was published to {self.destination} before cleanup failed.'
        elif self._guard is not None:
            outcome = f'Replacement transaction outcome: {self._guard.outcome}.'
        else:
            outcome = 'Output was not published.'
        raise ExportError('lifecycle_failed', f'{reason}. {outcome} '
                          f'Resources retained. Original staging path {self.directory}. Processing is disabled. '
                          'Close the application and resolve retained resources before exporting again.',
                          retained_resources=(self,), reported_destination=self.destination if published else None)

    def bind_validated(self,identity,control,budget=None):
        from mediaforge.infrastructure.filesystem.validated_file import ValidatedFile
        if len(identity.files)!=1 or identity.path!=str(self.destination):
            raise ExportError('unsafe_staging','Publication receipt disagrees with the captured destination.')
        transaction=self._guard.transaction if self._guard else None
        self._validated=ValidatedFile(self.staged,self.destination,identity.files[0],transaction,budget)
        self._validated.prepare(control)

    def publish(self, control):
        target = self.destination
        try:
            def publish():
                for source in self.protected_sources:
                    if (os.path.normcase(str(target.resolve())) == os.path.normcase(str(source.resolve()))
                            or (target.exists() and source.exists() and os.path.samefile(target, source))):
                        raise ExportError("source_collision", "Destination aliases a loaded source. Choose a new filename.")
                if self.staging is not None:
                    self.staging.finish_writing()
                if self._validated is not None:
                    result = (self._guard.publish(self.staged,self._validated) if self._guard
                              else self._validated.rename(target))
                else:
                    result = self._guard.publish(self.staged) if self._guard else self._publisher(self.staged,target)
                if self.staging is not None:
                    self.staging.published = True
                return result
            return control.publish(publish)
        except FileExistsError as error:
            raise ExportError("destination_exists", "Destination appeared during Export. Choose a new filename; no file was replaced.") from error
        except OSError as error:
            raise ExportError("publication_failed", "Cannot publish output. Check destination permissions and free space.") from error
