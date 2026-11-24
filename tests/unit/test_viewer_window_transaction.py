import ctypes
from ctypes import wintypes
from mediaforge.presentation.bridge.viewer_window import _MaximizedRestoreRectangle


def test_native_guard_affects_only_owned_maximized_restore_rectangle():
    guard=_MaximizedRestoreRectangle();guard.hwnd=100;guard.target=(-12,-12,3864,2127)
    position=guard.WindowPos(100,0,75,75,2160,1350,3)
    message=wintypes.MSG();message.hWnd=100;message.message=0x0046
    message.lParam=ctypes.addressof(position)
    assert guard.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(message))==(False,0)
    assert (position.x,position.y,position.cx,position.cy)==guard.target and not position.flags&3
    message.message=0x0005;message.wParam=0
    assert guard.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(message))==(True,0)
    message.wParam=2
    assert guard.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(message))==(False,0)
    message.hWnd=101;message.wParam=0
    assert guard.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(message))==(False,0)
    message.hWnd=100;guard.target=None
    assert guard.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(message))==(False,0)


def test_retaining_normal_placement_does_not_request_a_normal_state(monkeypatch):
    guard=_MaximizedRestoreRectangle()
    saved=guard.Placement();saved.normalPosition=wintypes.RECT(50,50,1490,950)
    current=guard.Placement();current.length=ctypes.sizeof(current)
    current.showCmd=3;current.flags=2
    current.normalPosition=wintypes.RECT(0,0,2560,1369)
    calls=[]
    monkeypatch.setattr(guard,'normal_placement',lambda hwnd:current)
    def set_placement(hwnd, pointer):
        value=ctypes.cast(pointer,ctypes.POINTER(guard.Placement)).contents
        calls.append((hwnd,value.showCmd,value.flags,
                      (value.normalPosition.left,value.normalPosition.top,
                       value.normalPosition.right,value.normalPosition.bottom)))
        return 1
    monkeypatch.setattr(ctypes.windll.user32,'SetWindowPlacement',set_placement)
    guard.retain_normal_placement(100,saved)
    assert calls==[(100,3,2,(50,50,1490,950))]
