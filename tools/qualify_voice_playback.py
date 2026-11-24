"""Bounded real QAudioSink preview check; device delivery is not a listening verdict."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import wave


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--wav',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if (not args.wav.is_absolute() or not args.wav.is_relative_to(paths.root)
            or not args.receipt.is_absolute() or not args.receipt.is_relative_to(paths.root)
            or args.receipt.exists()):parser.error('Explicit portable input and new receipt paths required.')
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtMultimedia import QAudio
    from mediaforge.application.contracts.local_ai import PcmFragment
    from mediaforge.infrastructure.audio.preview_format import PreviewAudioConverter
    from mediaforge.presentation.bridge.voice_preview import VoicePreviewPlayer
    app=QGuiApplication([])
    observations=[]
    class ObservedPlayer(VoicePreviewPlayer):
        def _state_changed(self,state):
            observations.append(dict(state=state.name,
                bytes_read=self._buffer.pos() if self._buffer else None,
                size=self._buffer.size() if self._buffer else None,
                error=self._sink.error().name if self._sink else None,
                processed_us=self._sink.processedUSecs() if self._sink else None))
            super()._state_changed(state)
    result=dict(ok=False,observation='actual default-device buffer consumption; audible quality NOT ASSESSED')
    player=ObservedPlayer(converter=PreviewAudioConverter())
    try:
        if args.wav.stat().st_size>8*24000*2+4096:raise ValueError('Preview input too large.')
        with wave.open(str(args.wav),'rb') as wav:
            if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth())!=(24000,1,2):
                raise ValueError('Expected actual Qwen 24 kHz mono PCM16 fixture.')
            raw=wav.readframes(8*24000+1)
        result['source_pcm_sha256']=hashlib.sha256(raw).hexdigest()
        started=player.play(PcmFragment(raw,24000,'fixed-native-fixture','aiden'))
        if not started:raise RuntimeError(player.message)
        result['format']=player._playback_format
        end=time.monotonic()+10
        while player.playing and time.monotonic()<end:
            app.processEvents();time.sleep(.005)
        app.processEvents()
        if player.playing:raise RuntimeError('Preview did not stop within its bounded lifetime.')
        complete=any(o['state']=='IdleState' and o['bytes_read']==o['size'] and o['size'] for o in observations)
        active=any(o['state']=='ActiveState' for o in observations)
        errors=[o for o in observations if o['error'] not in ('NoError','UnderrunError',None)]
        if not active or not complete or errors or player.message:
            raise RuntimeError('Native playback did not establish complete healthy buffer delivery. '+player.message)
        result['ok']=True
    except Exception as error:result['detail']=str(error)
    finally:
        player.shutdown();app.processEvents()
        result.update(observations=observations,retired=player._sink is None and player._buffer is None)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    if not result['ok'] or not result['retired']:raise SystemExit(2)


if __name__=='__main__':main()
