import numpy as np
import pytest

from mediaforge.infrastructure.audio.preview_format import convert, PreviewAudioConverter


def tone(hz,seconds=1):
    return np.rint(12000*np.sin(2*np.pi*hz*np.arange(int(24000*seconds))/24000)).astype('<i2').tobytes()


@pytest.mark.parametrize('rate',[16000,44100,48000,192000])
def test_device_resampling_preserves_duration_pitch_and_stereo_identity(rate):
    raw=tone(997,.7)
    data=convert(raw,24000,rate,2,'Float')
    frames=np.frombuffer(data,dtype='<f4').reshape(-1,2)
    assert abs(len(frames)/rate-.7)<=.5/rate
    assert np.array_equal(frames[:,0],frames[:,1])
    middle=frames[int(.1*rate):int(.6*rate),0]
    crossings=np.count_nonzero((middle[:-1]<0)&(middle[1:]>=0))
    assert abs(crossings/.5-997)<=2
    assert abs(np.sqrt(np.mean(middle**2))-12000/32768/np.sqrt(2))<.002


def test_downsampling_rejects_out_of_band_energy_instead_of_aliasing_it():
    def rms(hz):
        values=np.frombuffer(convert(tone(hz),24000,16000,1,'Float'),dtype='<f4')[100:-100]
        return np.sqrt(np.mean(values**2))
    assert rms(10000)<rms(1000)*.01


@pytest.mark.parametrize('kind,dtype,width',[('Int16','<i2',2),('Int32','<i4',4),('Float','<f4',4),('UInt8','u1',1)])
def test_supported_device_encodings_are_finite_bounded_and_have_no_dc_offset(kind,dtype,width):
    result=convert(tone(1000),24000,44100,1,kind)
    values=np.frombuffer(result,dtype=dtype)
    assert len(result)==44100*width and np.all(np.isfinite(values))
    if kind=='UInt8':assert abs(float(values.mean())-127.5)<.1
    else:assert abs(float(values.mean()))<1


def test_unsupported_device_requests_refuse_before_memory_or_native_work():
    converter=PreviewAudioConverter()
    for args in [(b'\0',24000,44100,2,'Float'),(b'\0\0',24000,44100,6,'Float'),
                 (b'\0\0',24000,384000,2,'Float'),(b'\0\0',22050,44100,2,'Float'),
                 (b'\0\0',24000,44100,2,'Unknown')]:
        with pytest.raises(ValueError,match='unsupported preview format'):converter(*args)
