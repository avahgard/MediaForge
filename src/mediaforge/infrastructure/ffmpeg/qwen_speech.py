"""Audio fitting ceiling for overlap with ONE resident CUDA engine."""
from mediaforge.infrastructure.ffmpeg.speech import SpeechAudio


class QwenSpeechAudio(SpeechAudio):
    def source_support(self,source,plan,track,recognition,control):
        from mediaforge.infrastructure.ffmpeg.qwen_support import scan_source
        return scan_source(self,source,plan,track,recognition,control)

    def support_binding(self):
        import hashlib
        from pathlib import Path
        from mediaforge.infrastructure.ffmpeg import qwen_support
        return dict(super().support_binding(),native_quiet=hashlib.sha256(Path(qwen_support.__file__).read_bytes()).hexdigest())

    def admission(self):
        # Preserve accepted pressure accounting; only narrow this phase's Job.
        return min(512*1024**2, super().admission())
