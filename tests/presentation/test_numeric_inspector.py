import pytest
from mediaforge.presentation.bridge.numeric import scrub
from tests.presentation.test_workspace_qml import SETUP, CLEANUP


@pytest.mark.parametrize('text,caret,expected', [('1024',3,'1025'),('1024',2,'1034'),
    ('1024',1,'1124'),('1024',0,'2024'),('1.0',0,'2.0'),('1.0',2,'1.1'),
    ('1.0',3,'1.01'),('0.750',4,'0.751'),('-10.0',0,'0.0')])
def test_digit_place_exact(text,caret,expected):
    assert scrub(text,caret,caret,caret,1)['text'] == expected


def test_selection_invalid_and_rational():
    assert scrub('1024',3,1,3,1)['text']=='1124'
    assert scrub('4/3',0,0,0,1)['text']=='5/3'
    assert scrub('4/3',2,2,2,-1)['text']=='4/2'
    for text in ('nan','1e3','2/0','hello'):
        assert not scrub(text,0,0,0,1)
    r=scrub('1.0',3,3,3,1)
    for _ in range(99): r=scrub(r['text'],r['cursor'],r['cursor'],r['cursor'],1,r['place'])
    assert r['text']=='2.00'


@pytest.mark.integration
def test_inspector_module_registers_converter_without_prior_qtquick_import(run_python):
    result=run_python(r'''
import sys
from PySide6.QtCore import QByteArray,QObject,QUrl,QPoint,QPointF,Qt,QCoreApplication
from PySide6.QtGui import QGuiApplication,QWheelEvent
from PySide6.QtQml import QQmlEngine,QQmlComponent
assert 'PySide6.QtQuick' not in sys.modules
from mediaforge.presentation.bridge.inspector_wheel import InspectorWheelRouting
app=QGuiApplication([]);engine=QQmlEngine();component=QQmlComponent(engine)
component.setData(QByteArray(b"""import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Basic
Window {
    width: 240; height: 180; visible: true
    Basic.ScrollView {
        id: scroll; objectName: "scroll"; anchors.fill: parent; contentWidth: availableWidth
        Column {
            width: scroll.availableWidth
            Basic.TextField { objectName: "number"; property bool numericField: true; width: parent.width; text: "1000" }
            Rectangle { width: 100; height: 600; color: "red" }
        }
    }
}"""),QUrl())
window=component.create();assert window is not None,[e.toString() for e in component.errors()]
app.processEvents();scroll=window.findChild(QObject,'scroll');field=window.findChild(QObject,'number')
routing=InspectorWheelRouting(scroll,window,window)
view=scroll.property('contentItem')
assert type(view).__module__=='PySide6.QtQuick'
point=field.mapToScene(QPointF(20,20));event=QWheelEvent(point,point,QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
QCoreApplication.sendEvent(window,event)
app.processEvents();assert field.property('text')=='1000'
window.close()
''')
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('dpi',['1','1.5','2'])
def test_converter_wheel_and_real_gutter(run_python,isolated_environment,dpi):
    isolated_environment['QT_SCALE_FACTOR']=dpi
    result=run_python(SETUP+r'''
from PySide6.QtQuick import QQuickItem
from PySide6.QtGui import QWheelEvent
from PySide6.QtCore import QCoreApplication
load(Path.cwd()/'wheel.mp4')
for size in ((800,560),(1280,800)):
    window.resize(*size)
    for operation in ('trim','crop','resize','output'):
        model.selectOperation(operation); QTest.qWait(240)
        scroll=item('inspectorScroll'); view=scroll.property('contentItem')
        assert isinstance(view,QQuickItem)
        bar=item('inspectorVerticalBar'); column=item('inspectorContent')
        assert column.mapToScene(QPointF(column.width(),0)).x()+5 <= bar.mapToScene(QPointF()).x()
        assert view.property('contentWidth') <= view.width()+.1
model.selectOperation('resize'); QTest.qWait(240)
field=item('reformatScaleInput'); field.forceActiveFocus(); field.setProperty('text','1.0');field.setProperty('cursorPosition',2)
point=field.mapToScene(QPointF(field.width()/2,field.height()/2))
QTest.mouseMove(window,QPoint(round(point.x()),round(point.y())));QTest.qWait(20)
event=QWheelEvent(point,point,QPoint(),QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
QCoreApplication.sendEvent(window,event);QTest.qWait(50)
assert field.property('text')=='1.1',field.property('text')
assert model.editState['reformatScale']=='1.1'
assert model.setReformat('pixel_aspect','4/3')
assert model.editState['pixelAspectText']=='4/3'
assert model.setReformat('pixel_aspect','0.75')
assert not model.setReformat('pixel_aspect','0')
assert model.setReformat('pixel_aspect','1.0')
window.resize(800,560);model.selectOperation('crop');QTest.qWait(240)
field=item('cropWInput');text=field.property('text');item('workspaceKeyboardFocus').forceActiveFocus()
view=item('inspectorScroll').property('contentItem');before=view.property('contentY')
point=field.mapToScene(QPointF(field.width()/2,field.height()/2))
QTest.mouseMove(window,QPoint(round(point.x()),round(point.y())));QTest.qWait(20)
event=QWheelEvent(point,point,QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
QCoreApplication.sendEvent(window,event);QTest.qWait(100)
assert field.property('text')==text and view.property('contentY')>before
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr
