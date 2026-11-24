from pathlib import Path
from PySide6.QtCore import QUrl
from mediaforge.application.contracts.source_location import RevealResult
from mediaforge.presentation.bridge.media import MediaViewModel
from test_media_bridge import qt_app,wait_for,ResultUseCase,success_result
from test_playback_controller import controller


def test_reveal_uses_exact_current_selection_and_keeps_bounded_feedback(qt_app,tmp_path):
    first=tmp_path/'первый с пробелами.mp4';second=tmp_path/'second source.mov'
    calls=[]
    class Revealer:
        def reveal(self,path):
            calls.append(path)
            return RevealResult(len(calls)!=3,'Missing source.' if len(calls)==3 else '')
    playback,_,_=controller()
    model=MediaViewModel(ResultUseCase(success_result(first),success_result(second)),playback_controller=playback,source_revealer=Revealer())
    assert not model.revealCurrentSource() and not calls
    assert model.openMedia(QUrl.fromLocalFile(str(first)));wait_for(qt_app,lambda:model.state=='loaded' and model.canOpen)
    first_id=model.activeMediaId
    assert model.revealCurrentSource() and calls==[str(first)]
    assert model.openMedia(QUrl.fromLocalFile(str(second)));wait_for(qt_app,lambda:model.state=='loaded' and model.canOpen)
    assert model.revealCurrentSource() and calls[-1]==str(second)
    assert model.selectMedia(first_id)
    assert not model.revealCurrentSource() and calls[-1]==str(first) and model.revealMessage=='Missing source.'
    frames=next(r['value'] for r in model.mediaInfoRows if r['label']=='Frames')
    assert frames=='Unavailable'
    playback._indexing=True;playback.changed.emit()
    assert next(r['value'] for r in model.mediaInfoRows if r['label']=='Frames')=='Indexing…'
    model.shutdown();before=list(calls);assert not model.revealCurrentSource() and calls==before
