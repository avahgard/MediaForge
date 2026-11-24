"""Give synthetic wheel input the same observed clock as native QTest input."""
import time
from PySide6.QtCore import QObject,QEvent


class NativeInputClock(QObject):
    def __init__(self,window):
        super().__init__(window)
        self._timestamp=0
        self._wall=time.monotonic()
        window.installEventFilter(self)

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.MouseButtonPress,QEvent.MouseButtonRelease,QEvent.MouseMove,QEvent.KeyPress,QEvent.KeyRelease):
            if event.timestamp()>0:
                self._timestamp=event.timestamp()
                self._wall=time.monotonic()
        return False

    def stamp(self,event):
        # Zero would switch Flickable to a different internal elapsed timer,
        # making the delta negative after preceding timestamped QTest events.
        now=time.monotonic()
        self._timestamp+=max(1,round((now-self._wall)*1000))
        self._wall=now
        event.setTimestamp(max(1,self._timestamp))
        return event
