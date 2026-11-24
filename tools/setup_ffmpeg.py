"""Provision or verify MediaForge's pinned development FFmpeg runtime."""

import argparse
import logging

from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.provisioning import provision_ffmpeg
from mediaforge.infrastructure.ffmpeg.runtime import (
    FfmpegManifest, FfmpegRuntimeError, FfmpegRuntimeResolver,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Verify managed runtime without network access")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger = logging.getLogger("mediaforge")
    try:
        profile = ProfilePaths.from_environment()
        manifest = FfmpegManifest.load()
        if args.verify:
            runtime = FfmpegRuntimeResolver(manifest, logger=logger).resolve(profile)
        else:
            runtime = provision_ffmpeg(profile, manifest=manifest, logger=logger)
        print(f"ffmpeg: {runtime.ffmpeg}\nffprobe: {runtime.ffprobe}")
        return 0
    except FfmpegRuntimeError as error:
        logger.error("%s: %s", error.code.value, error)
        return 1
    except ValueError as error:
        logger.error("configuration_invalid: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
