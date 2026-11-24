from tests.presentation.test_workspace_qml import SETUP, CLEANUP
import pytest


@pytest.mark.integration
@pytest.mark.parametrize('size', [(1440, 900), (1200, 820), (1024, 768), (800, 560)])
def test_operations_and_tray_restore_owner_pointer_and_keyboard_contracts(run_python, size):
    setup = SETUP.replace('use = Open()', '''
from PySide6.QtGui import QFontDatabase
for name in ('segoeui.ttf','seguisb.ttf','seguisym.ttf'):
    assert QFontDatabase.addApplicationFontFromData((Path('C:/Windows/Fonts')/name).read_bytes())>=0
use = Open()
''')
    code = setup + f'\nwindow.resize({size[0]}, {size[1]})\n' + r'''
load(Path.cwd()/'Короткий.mp4')
model.selectOperation('crop')
enable=item('enableTrim');edit=item('selectTrim');pill=item('operationTrim')
assert enable.width()==enable.height()==26 and 44<=edit.height()<=48
assert not window.findChild(QObject,'operationNoise')
tiles=[item('operation'+name) for name in ('Trim','Crop','Reformat','Output')]
row=item('operationsViewport');surface=item('operationSurface')
assert abs(tiles[-1].x()+tiles[-1].width()+row.x()*2-surface.width())<.01
assert min(tile.width() for tile in tiles)>70
for tile in (tiles[0], tiles[2], tiles[3]):
    QTest.mouseMove(window,QPoint(2,2))
    QTest.qWait(10)
    idle=tile.property('color')
    center=tile.mapToScene(QPointF(tile.width()/2,tile.height()/2))
    QTest.mouseMove(window,QPoint(round(center.x()),round(center.y())))
    QTest.qWait(10)
    hover=tile.property('color')
    assert hover!=idle, tile.objectName()
    QTest.qWait(220)
    assert tile.property('color')==hover
for selection in ('trim','resize'):
    model.selectOperation(selection);QTest.qWait(30)
    assert item('selectReformat').property('contentItem').property('truncated') is False
model.selectOperation('crop')
playing=playback.playing
enable.forceActiveFocus();QTest.keyClick(window,Qt.Key_Space);QTest.qWait(25)
assert model.editState['trimEnabled'] and model.selectedOperation=='crop' and playback.playing==playing
edit.forceActiveFocus();QTest.keyClick(window,Qt.Key_Return);QTest.qWait(25)
assert pill.property('selected') and model.editState['trimEnabled']
assert str(pill.property('color').name())!=str(item('operationCrop').property('color').name())
for _ in range(3):
    click(item('operationCrop'),Qt.RightButton)
    assert model.editState['cropEnabled'] and model.selectedOperation=='trim'
    click(item('enableCrop'))
    assert not model.editState['cropEnabled'] and model.selectedOperation=='trim'
click(item('activeOperationToggle'))
assert not model.editState['trimEnabled'] and not pill.property('enabledOperation')
click(enable)
assert model.editState['trimEnabled'] and pill.property('enabledOperation')
model.selectOperation('crop')
blank=pill.mapToScene(QPointF(3,pill.height()/2))
QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(round(blank.x()),round(blank.y())))
assert model.selectedOperation=='trim' and model.editState['trimEnabled']
assert not any(obj.objectName().startswith('operationMenu') for obj in window.findChildren(QObject))
selected=model.selectedOperation
click(item('selectOutput'),Qt.RightButton)
assert model.selectedOperation==selected
label=item('sourceFilename');info=item('metadataInfoButton')
assert abs(info.mapToScene(QPointF()).x()-label.mapToScene(QPointF(label.width(),0)).x()-8)<.01
assert info.x()+info.width()<=info.parentItem().width()
assert label.width()==min(label.property('implicitWidth'),info.parentItem().width()-40)
click(info)
popup=item('metadataInfoPopup')
assert popup.property('visible')
origin=info.mapToScene(QPointF())
x=origin.x()+popup.property('x'); y=origin.y()+popup.property('y')
assert abs(x-min(origin.x(),window.width()-popup.property('width')-8))<1
assert y>origin.y()+info.height() and y+popup.property('height')<=window.height()
QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(140)
assert not popup.property('visible') and info.property('activeFocus')
QTest.keyClick(window,Qt.Key_Return);QTest.qWait(30)
assert popup.property('visible')
QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(140)
add=item('mediaTrayAdd');background=add.property('background')
assert background.width()==add.width() and background.height()==add.height()
corner=add.mapToScene(QPointF(5,add.height()/2))
QTest.mouseMove(window,QPoint(round(corner.x()),round(corner.y())))
QTest.qWait(25);assert add.property('hovered')
QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(round(corner.x()),round(corner.y())))
QTest.qWait(25);dialog=item('mediaFileDialog');assert dialog.property('visible')
QMetaObject.invokeMethod(dialog,'close');window.requestActivate();QTest.qWait(50)
add.forceActiveFocus();QTest.keyClick(window,Qt.Key_Return);QTest.qWait(25)
assert dialog.property('visible'), (window.isActive(), add.property('activeFocus'))
QMetaObject.invokeMethod(dialog,'close');window.requestActivate();QTest.qWait(50)
assert window.grabWindow().save(str(Path.cwd()/f'media-after-{window.width()}x{window.height()}.png'))
assert model.duplicateMedia(model.activeMediaId)
tray=item('mediaTrayList')
card=item('trayItem0');before=card.mapToScene(QPointF(card.width()/2,0))
click(card,Qt.RightButton)
menu=item('mediaContextMenu')
assert menu.property('visible')
assert abs(menu.property('x')-max(8,min(before.x(),window.width()-menu.property('width')-8)))<1
assert abs(menu.property('y')+menu.property('height')+4-before.y())<1
assert item('contextDuplicate').isVisible() and item('contextRemove').isVisible()
QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(25)
assert not menu.property('visible') and item('mediaTray').property('activeFocus')
''' + CLEANUP
    result = run_python(code)
    assert result.returncode == 0, result.stdout + result.stderr
