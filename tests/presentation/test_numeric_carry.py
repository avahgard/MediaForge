"""Decimal-place selection survives carries, borrows, hover reset and replacement."""
import pytest
from mediaforge.presentation.bridge.numeric import scrub
from tests.presentation.test_export_qml import EXPORT_SETUP
from tests.presentation.test_workspace_qml import CLEANUP


@pytest.mark.parametrize('text,start,end,direction,expected,selection', [
    ('9.99',3,4,1,'10.00',(4,5)),('10.00',4,5,-1,'9.99',(3,4)),
    ('-9.99',4,5,-1,'-10.00',(5,6)),('-10.00',5,6,1,'-9.99',(4,5)),
    ('-0.01',4,5,1,'0.00',(3,4)),('0.00',0,1,-1,'-1.00',(1,2)),
    ('0999',3,4,1,'1000',(3,4)),('999',2,3,1,'1000',(3,4)),
    ('9/3',0,1,1,'10/3',(1,2)),('3/99',3,4,1,'3/100',(4,5)),
])
def test_selection_maps_to_numeric_place(text,start,end,direction,expected,selection):
    actual=scrub(text,end,start,end,direction)
    assert actual['text']==expected and (actual['start'],actual['end'])==selection
    after_reset=scrub(actual['text'],actual['cursor'],actual['start'],actual['end'],direction)
    assert after_reset['place']==actual['place']


def test_fractional_caret_follows_anchor_and_repeated_cache_reset():
    actual=scrub('9.99',3,3,3,1)
    assert (actual['text'],actual['cursor'])==('10.00',4)
    for _ in range(10):actual=scrub(actual['text'],actual['cursor'],actual['cursor'],actual['cursor'],1)
    assert actual['text']=='10.10' and actual['cursor']==4 and actual['place']=='-2'
    actual=scrub('1.0',3,3,3,1)
    assert (actual['text'],actual['cursor'])==('1.01',3)
    assert scrub(actual['text'],3,3,3,1)['text']=='1.02'


@pytest.mark.integration
def test_pixel_and_crop_carry_hover_reset_and_manual_digit_replacement(run_python):
    result=run_python(EXPORT_SETUP+r'''
from PySide6.QtGui import QWheelEvent
load(Path.cwd()/'carry.mp4');model.selectOperation('resize');model.setOperationEnabled('resize',True);QTest.qWait(240)
field=item('pixelAspectInput');field.forceActiveFocus();field.setProperty('text','9.99');field.select(3,4)
def wheel(field,direction=1):
    p=field.mapToScene(QPointF(field.width()/2,field.height()/2))
    QTest.mouseMove(window,QPoint(round(p.x()),round(p.y())));QTest.qWait(20)
    event=QWheelEvent(p,p,QPoint(),QPoint(0,120*direction),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
    app.sendEvent(window,event);QTest.qWait(30)
def leave(field):
    QTest.mouseMove(window,QPoint(20,20));QTest.qWait(30)
    assert field.property('wheelPlace')==''
wheel(field)
assert field.property('text')=='10.00' and (field.property('selectionStart'),field.property('selectionEnd'))==(4,5)
leave(field);wheel(field)
assert field.property('text')=='10.01' and field.property('selectedText')=='1'
QTest.keyClick(window,Qt.Key_7);assert field.property('text')=='10.07'
QTest.keyClick(window,Qt.Key_Return);assert model.output.summary['sar']=='1007:100'
field.forceActiveFocus();field.setProperty('text','10.00');field.select(4,5);wheel(field,-1)
assert field.property('text')=='9.99' and (field.property('selectionStart'),field.property('selectionEnd'))==(3,4)
leave(field);wheel(field,-1);assert field.property('text')=='9.98'
field.setProperty('text','0');QTest.keyClick(window,Qt.Key_Return)
assert not model.output.summary['valid'] and not model.output.canExport
assert model.setReformat('pixel_aspect','1.0')
model.selectOperation('crop');model.setOperationEnabled('crop',True);QTest.qWait(240)
assert model.setCropRect(0,0,998,800)
crop=item('cropWInput');crop.forceActiveFocus();crop.setProperty('text','999');crop.select(2,3)
wheel(crop);assert crop.property('text')=='1000' and crop.property('selectedText')=='0'
assert model.editState['cropWidth']==1000
leave(crop);wheel(crop);assert crop.property('text')=='1001' and crop.property('invalid')
assert model.editState['cropWidth']==1000  # No silent rounding or publication of invalid input.
assert not exports.requests
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr
