# MediaForge 1.0.1

- Restored the original MediaForge icon in the Windows launcher and application window.
- Updated the GitHub README with the original logo and screenshots of Media, Dub and Settings.

For an existing installation, follow the [manual update instructions](docs/PORTABLE_BOOTSTRAP.md#manual-update). Copy `Data/v2` to retain preferences and compatible AI models, and copy `Runtime/Cache` to reuse verified runtime downloads.

## Supported workflows and limits

- Open a video, still image or image sequence in Media. Preview the original source, then trim, crop, reformat and export to a new destination.
- Use the timeline for frame navigation on eligible video sources. MediaForge keeps the source files intact when processing and exporting.
- Dub supported English video into Russian with the optional local Qwen3-TTS runtime and Aiden voice. Manage optional components in Settings.
- The Windows x64 portable bootstrap downloads pinned Python, Qt and FFmpeg directly from upstream after first-launch confirmation. Extract the whole ZIP to a writable local folder. Subsequent Media editing and export work offline.
- Still-image processing supports PNG, JPEG, TIFF and static WebP within the application's format and color limits. Optional AI components have separate storage, availability and hardware requirements.

See the [README](README.md), [GPL-3.0-only license](LICENSE) and [third-party notices](docs/THIRD_PARTY_NOTICES.md).
