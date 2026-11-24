import logging

from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.logging.protected import ProtectedLogs


class ProtectedRotatingHandler(logging.Handler):
    """Publish immutable log versions; never append into an existing object."""
    def __init__(self, profile):
        super().__init__()
        self.owner = ProtectedLogs(profile.logs)
        self.maxBytes = 1_000_000

    def doRollover(self):
        self.owner.refresh()
        active, first = self.owner.read(0), self.owner.read(1)
        self.owner.publish(2, first)
        self.owner.publish(1, active)
        self.owner.publish(0, b'')
        self.owner.refresh()

    def emit(self, record):
        # Deliberately propagate write failures: logging must not silently vanish.
        message = (self.format(record) + '\n').encode('utf-8')
        self.owner.refresh()
        active = self.owner.read(0)
        if active and len(active) + len(message) >= self.maxBytes:
            self.doRollover()
            active = b''
        self.owner.publish(0, active + message)
        self.owner.refresh()

    def close(self):
        try:
            if hasattr(self, 'owner'):
                self.owner.close()
        finally:
            super().close()


def configure_logging(profile: ProfilePaths) -> logging.Logger:
    # Direct callers receive the same guarded directory preparation as Bootstrap;
    # never mkdir through an unchecked portable ancestor.
    profile.prepare()
    logger = logging.getLogger("mediaforge")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    handler = ProtectedRotatingHandler(profile)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def close_logging(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
