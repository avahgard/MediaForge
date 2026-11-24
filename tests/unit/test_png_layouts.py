"""Independent encoded PNG layouts and exact display normalization."""
import struct,zlib,threading
import pytest
from mediaforge.infrastructure.images.reader import QtImageReader
from mediaforge.application.services.images import ImageSourceService


def png(path,width,height,depth,color,rows,extra=()):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,depth,color,0,0,0))+
        b''.join(chunk(k,v) for k,v in extra)+chunk(b'IDAT',zlib.compress(b''.join(b'\0'+r for r in rows)))+chunk(b'IEND',b''))


@pytest.mark.parametrize('depth,color,row,extra,expected',[
    (8,0,b'\x80\x80',(),(128,128,128,255)),
    (1,0,b'\xc0',(),(255,255,255,255)),
    (2,0,b'\xa0',(),(170,170,170,255)),
    (4,0,b'\x88',(),(136,136,136,255)),
    (8,2,bytes((200,30,90))*2,(),(200,30,90,255)),
    (8,4,bytes((180,128))*2,(),(90,90,90,128)),
    (8,6,bytes((200,30,90,128))*2,(),(100,15,45,128)),
    (1,3,b'\xc0',((b'PLTE',bytes((0,255,0,200,30,90))),(b'tRNS',bytes((0,128)))),(100,15,45,128)),
    (2,3,b'\x50',((b'PLTE',bytes((0,255,0,200,30,90))),(b'tRNS',bytes((0,128)))),(100,15,45,128)),
    (4,3,b'\x11',((b'PLTE',bytes((0,255,0,200,30,90))),(b'tRNS',bytes((0,128)))),(100,15,45,128)),
    (8,3,b'\x01\x01',((b'PLTE',bytes((0,255,0,200,30,90))),(b'tRNS',bytes((0,128)))),(100,15,45,128)),
])
def test_common_png_layouts(tmp_path,depth,color,row,extra,expected):
    path=tmp_path/'layout.png';png(path,2,2,depth,color,[row,row],extra)
    reader=QtImageReader();source=reader.probe(str(path))
    plan=ImageSourceService(reader).preview_plan(source,'1:2')
    raster,guard=reader.decode_preview(source,plan,threading.Event())
    try:
        # Native premultiplied BGRA backing, independent of QImage unpremultiply.
        actual=bytes(raster.display.constBits())[:4]
        assert actual==bytes((expected[2],expected[1],expected[0],expected[3]))
        assert source.facts.depth==depth
    finally:guard.__exit__(None,None,None)


@pytest.mark.parametrize('color,channels',[(0,1),(2,3),(4,2),(6,4)])
def test_png16_typed_precision_class(tmp_path,color,channels):
    path=tmp_path/'precision.png';png(path,2,2,16,color,[b'\x80\0'*2*channels]*2)
    from tests.unit.test_image_preview import Resources
    service=ImageSourceService(QtImageReader(),resources=Resources())
    source=service.reader.probe(str(path));assert source.facts.depth==16
    value=service.prepare_preview(str(path),'1:1',threading.Event(),viewport=(10,10))
    try:
        pixel=value.raster.display.pixel(0,0);alpha=128 if color in (4,6) else 255
        channel=64 if alpha==128 else 128
        assert ((pixel>>24)&255,(pixel>>16)&255,(pixel>>8)&255,pixel&255)==(alpha,channel,channel,channel)
    finally:value.release();service.close_cache()


def test_png_exif_nonidentity_is_explicitly_unqualified(tmp_path):
    path=tmp_path/'orientation.png'
    exif=b'II'+struct.pack('<HIH',42,8,1)+struct.pack('<HHI',274,3,1)+struct.pack('<H',6)+b'\0'*6
    png(path,2,2,8,2,[bytes((200,30,90))*2]*2,[(b'eXIf',exif)])
    with pytest.raises(ValueError,match='PNG EXIF orientation'):QtImageReader().probe(str(path))
