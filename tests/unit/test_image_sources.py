from pathlib import Path
import struct
import pytest
from PySide6.QtGui import QColor, QColorSpace, QImage
from mediaforge.application.contracts.image_source import StillSource, VideoSource
from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.services.images import ImageBufferBudget, ImageSourceService
from mediaforge.infrastructure.images.reader import QtImageReader


def image(path, fmt='PNG', color=QColor(200,40,100,128), size=(96,64)):
    source=QImage(*size,QImage.Format_RGBA8888);source.fill(color)
    assert source.save(str(path),fmt)
    return source


@pytest.mark.parametrize('name,fmt', [('x.png','PNG'),('x.jpg','JPEG'),('x.tiff','TIFF'),('x.webp','WEBP')])
def test_typed_ordinary_still_and_resource_lifetime(tmp_path,name,fmt):
    p=tmp_path/name; image(p,fmt)
    service=ImageSourceService(QtImageReader());prepared=service.prepare(str(p))
    assert isinstance(prepared.metadata.source,StillSource)
    assert prepared.metadata.duration_seconds is None and prepared.metadata.frame_rate is None
    assert len(prepared.raster.rgba)==96*64*4
    assert service.budget.reserved==service.budget.overhead+96*64*4*service.budget.raster_reservations+p.stat().st_size
    prepared.release();assert service.budget.reserved==service.budget.overhead


def test_video_dto_default_identity_is_preserved():
    dto=MediaMetadataDto('v','C:/v.mp4','mp4',1.,'h264',96,64,24.)
    assert dto.source==VideoSource('C:/v.mp4')


def test_changed_image_fails_closed(tmp_path):
    p=tmp_path/'путь пробелы.png';image(p)
    service=ImageSourceService(QtImageReader());prepared=service.prepare(str(p))
    expected=prepared.metadata.source;prepared.release()
    image(p,color=QColor(20,40,100,100))
    with pytest.raises(ValueError,match='changed'):service.prepare(str(p),expected)
    assert service.budget.reserved==service.budget.overhead


def test_animation_depth_profile_suffix_and_bounds_rejection(tmp_path):
    reader=QtImageReader();p=tmp_path/'x.png';image(p)
    data=p.read_bytes()
    # acTL injection with CRC-independent header rejection before decoder.
    p.write_bytes(data[:33]+struct.pack('>I4sII',8,b'acTL',2,0)+b'xxxx'+data[33:])
    with pytest.raises(ValueError,match='Animated'):reader.probe(str(p))
    image(p);p.with_suffix('.jpg').write_bytes(p.read_bytes())
    with pytest.raises(ValueError,match='disagree'):reader.probe(str(p.with_suffix('.jpg')))
    high=QImage(32,32,QImage.Format_RGBA64);high.fill(QColor(50,60,70,120));assert high.save(str(p),'PNG')
    # Owner parity amendment: PNG16 is a real typed input, not an 8-bit
    # rejection class. Preview/processing precision has separate regressions.
    assert reader.probe(str(p)).facts.depth==16
    source=QImage(32,32,QImage.Format_RGB32);source.fill(QColor(50,60,70));source.setColorSpace(QColorSpace(QColorSpace.DisplayP3));assert source.save(str(p),'PNG')
    with pytest.raises(ValueError,match='profile'):reader.probe(str(p))
    budget=ImageBufferBudget()
    with pytest.raises(ValueError,match='256 MiB'):budget.reserve(10000,10000)
    first=budget.reserve(1920,1080)
    with pytest.raises(ValueError,match='256 MiB'):budget.reserve(3840,2160)
    budget.release(first)


def test_multipage_tiff_and_unsafe_path_rejection(tmp_path):
    p=tmp_path/'x.tiff';image(p,'TIFF');data=bytearray(p.read_bytes())
    order='<' if data[:2]==b'II' else '>';offset=struct.unpack_from(order+'I',data,4)[0];count=struct.unpack_from(order+'H',data,offset)[0]
    struct.pack_into(order+'I',data,offset+2+count*12,8);p.write_bytes(data)
    with pytest.raises(ValueError,match='Multipage'):QtImageReader().probe(str(p))
    with pytest.raises(ValueError,match='absolute'):QtImageReader().probe('relative.png')


def test_jpeg_orientation_applied_once_and_payload_failure(tmp_path):
    p=tmp_path/'oriented.jpg';image(p,'JPEG',color=QColor(200,40,100,255),size=(96,64))
    asymmetric=QImage(96,64,QImage.Format_RGB32)
    for y in range(64):
        for x in range(96):asymmetric.setPixelColor(x,y,QColor(x*2,y*3,(x+y)%256))
    assert asymmetric.save(str(p),'JPEG')
    coded=QImage(str(p))
    payload=b'Exif\0\0'+b'II'+struct.pack('<HIH',42,8,1)+struct.pack('<HHI',274,3,1)+struct.pack('<H',6)+b'\0\0'+b'\0'*4
    data=p.read_bytes();p.write_bytes(data[:2]+b'\xff\xe1'+struct.pack('>H',len(payload)+2)+payload+data[2:])
    prepared=ImageSourceService(QtImageReader()).prepare(str(p))
    assert prepared.metadata.width==64 and prepared.metadata.height==96
    assert prepared.metadata.source.facts.orientation==6
    oriented=QImage(prepared.raster.rgba,64,96,64*4,QImage.Format_RGBA8888)
    for x,y in ((0,0),(63,0),(0,95),(63,95),(20,30)):
        assert oriented.pixelColor(x,y)==coded.pixelColor(y,63-x)
    prepared.release()
    p.write_bytes(b'\xff\xd8\xff\xc0\0\x03x\xff\xda')
    with pytest.raises(ValueError,match='metadata'):QtImageReader().probe(str(p))


def test_budget_failure_precedes_decoding_and_source_presentation_copy(tmp_path):
    class Counted(QtImageReader):
        calls=0
        def decode(self,source):
            self.calls+=1
            return super().decode(source)
    p=tmp_path/'large.png';image(p,size=(3840,2160));reader=Counted();service=ImageSourceService(reader)
    with pytest.raises(ValueError,match='256 MiB'):service.prepare(str(p))
    assert reader.calls==0 and service.budget.reserved==service.budget.overhead
    small=tmp_path/'small.png';source=image(small)
    prepared=service.prepare(str(small))
    assert prepared.raster.rgba==bytes(source.constBits())
    from mediaforge.presentation.models.media_info import info_rows
    assert 'Video' not in [r['label'] for r in info_rows(prepared.metadata,'unavailable')]
    assert 'Frame rate' not in [r['label'] for r in info_rows(prepared.metadata,'unavailable')]
    prepared.release()


def test_image_payload_and_unsupported_image_routing(tmp_path):
    p=tmp_path/'wrong.data';image(p)
    reader=QtImageReader();assert reader.is_image(str(p))
    with pytest.raises(ValueError,match='disagree'):reader.probe(str(p))
    assert reader.is_image(str(tmp_path/'x.gif'))
    assert reader.is_image(str(tmp_path/'x.ico'))
