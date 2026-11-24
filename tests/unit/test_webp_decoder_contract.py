"""Native WebP display and original-source processing have explicit references."""
from pathlib import Path
from threading import Event
import subprocess
import imagecodecs
import numpy as np
import pytest
from PySide6.QtGui import QImage
from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.state.workspace import MediaItem
from tests.unit.test_image_export import executor
from tests.unit.test_import_export_parity import execute

pytestmark=[pytest.mark.native,pytest.mark.integration]


@pytest.mark.parametrize('lossless',[False,True])
@pytest.mark.parametrize('alpha',[False,True])
def test_webp_display_and_original_export_match_declared_decoders(tmp_path,lossless,alpha):
    y,x=np.indices((19,31))
    data=np.stack(((x*73+y*21)%256,(x*19+y*67)%256,(x*113+y*31)%256),axis=2).astype(np.uint8)
    if alpha:data=np.concatenate((data,((x*17+y*11)%256).astype(np.uint8)[:,:,None]),axis=2)
    path=tmp_path/'source.webp';encoded=imagecodecs.webp_encode(data,lossless=lossless,level=37);path.write_bytes(encoded)
    reference=imagecodecs.webp_decode(encoded,hasalpha=True)
    runner=executor();images=runner._image_service;source=images.reader.probe(str(path))
    prepared=images.prepare_preview(str(path),'1:1',Event(),source,(100,100))
    try:
        straight=prepared.raster.straight
        if straight is not None:
            straight=straight.convertToFormat(QImage.Format_RGBA8888)
            stored=np.frombuffer(straight.constBits(),np.uint8).reshape(straight.height(),straight.bytesPerLine())[:,:straight.width()*4].reshape(19,31,4)
            assert np.array_equal(stored,reference)
        display=prepared.raster.display.convertToFormat(QImage.Format_RGBA8888_Premultiplied)
        shown=np.frombuffer(display.constBits(),np.uint8).reshape(display.height(),display.bytesPerLine())[:,:display.width()*4].reshape(19,31,4)
        expected_display=reference.copy();expected_display[:,:,:3]=(reference[:,:,:3].astype(np.uint16)*reference[:,:,3:]+127)//255
        # Existing monitor contract: RGBA64 premultiply then 8-bit display may
        # round RGB by one code value; alpha and straight source bytes are exact.
        assert np.array_equal(shown[:,:,3],expected_display[:,:,3])
        assert np.max(np.abs(shown[:,:,:3].astype(np.int16)-expected_display[:,:,:3]))<=1
        item=MediaItem.create(prepared.metadata);item.output.folder=str(tmp_path/'out');Path(item.output.folder).mkdir();item.output.container='png';item.output.filename='original';item.output.image_depth='8'
        service,result=execute(runner,item)
        try:
            actual=imagecodecs.png_decode(Path(result.path).read_bytes())
            # Tiny independent raw original decoder oracle, never Viewer/readback pixels.
            from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
            cmd=[str(resolve_ffmpeg_runtime().ffmpeg),'-nostdin','-v','error','-threads','1','-i',str(path),'-frames:v','1','-pix_fmt','rgba','-threads','1','-f','rawvideo','-']
            decoded=subprocess.run(cmd,capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
            assert decoded.returncode==0,decoded.stderr
            assert len(decoded.stdout)==19*31*4
            original=np.frombuffer(decoded.stdout,np.uint8).reshape(19,31,4)
            expected=original if alpha else original[:,:,:3]
            assert np.array_equal(actual,expected)
            assert np.array_equal(original[:,:,3],reference[:,:,3])
            if lossless:assert np.array_equal(original,reference)
            assert path.read_bytes()==encoded and not service._processing_gate.busy
        finally:service.shutdown()
    finally:prepared.release();images.close_cache()
