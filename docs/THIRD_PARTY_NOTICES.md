# Third-party notices

MediaForge includes modified Apache-2.0 source from Accelerate 1.12.0,
Transformers 4.57.3 and Qwen3-TTS 0.1.1. Preserve upstream copyright and license
headers and MediaForge's change notices under
[`src/mediaforge/resources/dub/patches`](../src/mediaforge/resources/dub/patches).
The [Apache License 2.0](../licenses/Apache-2.0.txt) applies to those components.

- [Accelerate 1.12.0 source](https://github.com/huggingface/accelerate/tree/v1.12.0)
- [Transformers 4.57.3 source](https://github.com/huggingface/transformers/tree/v4.57.3)
- [Qwen3-TTS upstream source](https://github.com/QwenLM/Qwen3-TTS)

The Portable Bootstrap downloads the original, pinned Qt for Python 6.11.2,
CPython and native wheels directly from their suppliers. The
[Core lock](../packaging/core-lock.json) and
[FFmpeg manifest](../external/manifests/ffmpeg.json) list the exact inputs.
Runtime licenses are retained in the installed wheels, Python distribution
and `Runtime/1.0.0/Core/Notices/Gyan-FFmpeg.txt`.
See [portable setup](PORTABLE_BOOTSTRAP.md) for acquisition and source.

Optional AI models and provider runtimes are separately acquired under their
respective terms. [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html)
and [FFmpeg redistribution guidance](https://ffmpeg.org/legal.html) describe
requirements for their respective binaries.
