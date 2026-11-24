"""Owned codec child. No publication, user source writes, Qt or network access.

Inputs are locked, independently verified 8/16-bit PNG intermediates from the
source-based FFmpeg plan. The parent retains all transaction/source ownership.
"""
import sys,json,os
from pathlib import Path


def transfer_lookup(precision, source_depth):
    """Quantize every possible integer input through the reference transfer once."""
    import numpy as np

    maximum = 255 if source_depth == 8 else 65535
    ramp = np.arange(maximum + 1, dtype=np.uint16)
    if source_depth == 8:
        ramp *= 257
    rgb = np.broadcast_to(ramp.reshape((1, -1, 1)), (1, len(ramp), 3))
    return precision.convert(precision.bt709_to_srgb(rgb), '8')[0, :, 0].copy()


def jpeg_sampling(data):
    pos=2
    while pos<len(data):
        if data[pos]!=255:raise ValueError('Invalid JPEG marker')
        while data[pos]==255:pos+=1
        marker=data[pos];pos+=1
        if marker in (0xd8,0xd9):continue
        length=int.from_bytes(data[pos:pos+2],'big')
        if marker in (0xc0,0xc1,0xc2):
            block=data[pos+2:pos+length]
            return (block[6],block[7],block[9],block[10],block[12],block[13])
        if not 2<=length<=65535:raise ValueError('Invalid JPEG segment')
        pos+=length
    raise ValueError('JPEG sampling header missing')


def alpha_area(data, plan):
    """Exact straight-alpha area integration, vectorized in native NumPy.

    Integer prefix integrals represent rational pixel-cell coverage. Only four
    channels/two axes are iterated in Python; no per-pixel interpreter loops.
    Round half upward once, after integration; transparent padding contributes 0.
    """
    import numpy as np
    from fractions import Fraction
    from math import lcm
    h,w,channels=data.shape
    oh,ow=plan['height'],plan['width'];t=plan['transform']
    if channels!=4:raise ValueError('Alpha transform requires straight RGBA')
    def axis(n, out, extent, origin):
        scale=Fraction(n)/Fraction(extent);offset=-Fraction(origin)*scale
        denominator=lcm(scale.denominator,offset.denominator)
        a=int(scale*denominator);b=int(offset*denominator)
        # Fail before any prefix allocation if integer intermediates cannot fit.
        if max(n*denominator,abs(b),abs(out*a+b))>2**50:raise ValueError('Transform precision bound exceeded')
        edges=np.clip(np.arange(out+1,dtype=np.int64)*a+b,0,n*denominator)
        return edges//denominator,edges%denominator,denominator,a
    axes=(axis(w,ow,t['picture_width'],t['x']),axis(h,oh,t['picture_height'],t['y']))
    if 65535**2*w*h*axes[0][2]*axes[1][2]>np.iinfo(np.int64).max//2:
        raise ValueError('Exact alpha integral exceeds safe integer range')
    def integrate(values, spec, dimension):
        q,r,d,a=spec;n=values.shape[dimension]
        np.cumsum(values,axis=dimension,dtype=np.int64,out=values)
        before=np.take(values,np.maximum(q-1,0),axis=dimension)
        shape=[1]*values.ndim;shape[dimension]=len(q)
        before*=np.reshape(q>0,shape)
        sample=np.take(values,np.minimum(q,n-1),axis=dimension)-before
        before*=d;before+=sample*np.reshape(r,shape)
        return np.diff(before,axis=dimension)
    def sum_area(values):
        return integrate(integrate(values,axes[0],1),axes[1],0)
    alpha=sum_area(data[:,:,3].astype(np.int64))
    area=axes[0][3]*axes[1][3]
    floating=plan.get('depth')=='float'
    output=np.empty((oh,ow,4),dtype=np.float32 if floating else np.uint16)
    output[:,:,3]=alpha/(area*65535) if floating else (alpha+area//2)//area
    for channel in range(3):
        color=data[:,:,channel].astype(np.int64)*data[:,:,3]
        total=sum_area(color)
        if floating:
            normalized=np.zeros((oh,ow),dtype=np.float64)
            np.divide(total,alpha,out=normalized,where=alpha!=0)
            output[:,:,channel]=normalized/65535
        else:
            np.floor_divide(total+alpha//2,alpha,out=total,where=alpha!=0)
            total[alpha==0]=0;output[:,:,channel]=total
    return output


def main(plan_path,encode=True):
    # This codec child does no BLAS work. Avoid host-sized OpenBLAS thread
    # stacks consuming its explicit Job commit before pixel work begins.
    os.environ['OPENBLAS_NUM_THREADS']='1'
    os.environ['OMP_NUM_THREADS']='1'
    import numpy as np
    import imagecodecs as codecs
    import tifffile
    site=Path(sys.argv[1]).resolve()
    for module,version in [(np,'2.4.3'),(codecs,'2026.11.11'),(tifffile,'2026.9.20')]:
        if module.__version__!=version or not Path(module.__file__).resolve().is_relative_to(site):
            raise ValueError('Codec dependency pin/project ownership mismatch; no global fallback')
    with Path(plan_path).open('rb') as file:
        payload=file.read(16*1024**2+1)
    if len(payload)>16*1024**2:raise ValueError('Codec plan exceeds its byte budget')
    plan=json.loads(payload)
    numbered=plan.get('numbered')
    if numbered is None:
        members=plan['members']
        count=len(members)
    else:
        count=numbered['count']
        members=((Path(numbered['input'])/f'codec-source_{i:06d}.png',
                  Path(numbered['output'])/(numbered['prefix']+str(numbered['start']+i).zfill(numbered['padding'])+numbered['extension']))
                 for i in range(count))
    if count<=0:raise ValueError('Invalid image plan')
    import importlib.util
    spec=importlib.util.spec_from_file_location('codec_precision',Path(__file__).with_name('precision.py'))
    precision=importlib.util.module_from_spec(spec);spec.loader.exec_module(precision)
    lookup = None
    if (not plan.get('native_source') and not plan.get('transform') and plan['channels'] == 3
            and plan['depth'] == '8' and plan.get('source_color') == 'BT.709'):
        lookup = transfer_lookup(precision, plan['source_depth'])
    for index,(source,target) in enumerate(members,1):
        if plan.get('native_source'):
            data=precision.orient(precision.read_samples(Path(source).read_bytes(),plan['source_format']),plan['orientation'])
            expected_type=np.dtype('float32' if plan['source_type']=='float' else 'uint16')
            if data.dtype!=expected_type or data.shape[:2]!=(plan['input_height'],plan['input_width']):
                raise ValueError('Original typed source no longer matches its validated plan')
            if plan.get('crop'):
                x,y,w,h=plan['crop'];data=data[y:y+h,x:x+w]
            if plan.get('source_color')=='BT.709':data=precision.bt709_to_srgb(data)
            if plan.get('transform'):
                if data.shape[2]==3:
                    alpha=np.full((*data.shape[:2],1),1 if data.dtype.kind=='f' else 65535,dtype=data.dtype)
                    data=np.concatenate((data,alpha),axis=2)
                data=precision.float_area(data,plan) if data.dtype.kind=='f' else alpha_area(data,plan)
            if plan.get('matte') and data.shape[2]==4:
                maximum=1 if data.dtype.kind=='f' else 65535
                matte=maximum if plan['matte']=='white' else 0
                if data.dtype.kind=='f':data=(data[:,:,:3].astype(np.float64)*data[:,:,3:]+matte*(1-data[:,:,3:])).astype(np.float32)
                else:data=((data[:,:,:3].astype(np.uint64)*data[:,:,3:]+matte*(maximum-data[:,:,3:])+maximum//2)//maximum).astype(np.uint16)
            if plan['channels']==3:data=data[:,:,:3]
        else:
            data=codecs.png_decode(Path(source).read_bytes())
            expected_type=np.uint16 if plan['source_depth']==16 else np.uint8
            if data.dtype!=expected_type or data.shape!=(plan.get('input_height',plan['height']),plan.get('input_width',plan['width']),plan['channels']):
                raise ValueError('Invalid authoritative intermediate raster')
            if lookup is not None:
                data = lookup[data]
            else:
                if data.dtype==np.uint8:data=data.astype(np.uint16)*257
                if plan.get('transform'):data=alpha_area(data,plan)
                if plan.get('source_color')=='BT.709':data=precision.bt709_to_srgb(data)
        if plan['format']=='webp':
            expected=precision.convert(data,'8')
            if encode:
                with Path(target).open('xb') as f:f.write(codecs.webp_encode(expected,lossless=plan['lossless'],level=plan['quality'],method=4,numthreads=1))
            actual=codecs.webp_decode(Path(target).read_bytes(),hasalpha=plan['channels']==4)
            if actual.shape!=expected.shape or plan['channels']==4 and not np.array_equal(actual[:,:,3],expected[:,:,3]):raise ValueError('WebP alpha/geometry mismatch')
            if plan['lossless'] and not np.array_equal(actual[:,:,:3][expected[:,:,3]!=0] if plan['channels']==4 else actual,expected[:,:,:3][expected[:,:,3]!=0] if plan['channels']==4 else expected):raise ValueError('WebP visible RGB mismatch')
        elif plan['format']=='jpeg':
            expected=precision.convert(data,'8')
            if encode:
                blob=codecs.jpeg_encode(expected,level=plan['quality'],subsampling=plan['sampling'],optimize=True)
                with Path(target).open('xb') as f:f.write(blob)
            blob=Path(target).read_bytes();actual=codecs.jpeg_decode(blob)
            sampling={'411':0x41,'422':0x21,'444':0x11}[plan['sampling']]
            factors=jpeg_sampling(blob)
            if factors!=(1,sampling,2,0x11,3,0x11) or actual.shape!=expected.shape:raise ValueError('JPEG chroma/geometry mismatch')
        else:
            depth=plan['depth']
            expected=precision.convert(data,depth)
            precision.validate_floats(expected)
            if encode:
                with Path(target).open('xb') as f:
                    if plan['format']=='png':f.write(codecs.png_encode(expected,level=6))
                    else:
                        tifffile.imwrite(f,expected,photometric='rgb',metadata=None,
                            extrasamples=['unassalpha'] if plan['channels']==4 else None,
                            compression={'raw':None,'deflate':'adobe_deflate','lzw':'lzw','packbits':'packbits'}[plan['compression']],
                            maxworkers=1,extratags=[(274,'H',1,1,False)])
            # Container writer is tifffile; independent container/pixel reader
            # is imagecodecs/libtiff. Neither uses Qt's premultiplied TIFF path.
            actual=(codecs.png_decode if plan['format']=='png' else codecs.tiff_decode)(Path(target).read_bytes())
            if actual.dtype!=expected.dtype or actual.shape!=expected.shape or not np.array_equal(actual,expected):
                raise ValueError('TIFF stored samples differ from source processing')
            if plan['format']=='png':
                del data,expected,actual
                if not encode and index%128==0:print(f'mediaforge_validated={index}',flush=True)
                continue
            with tifffile.TiffFile(target) as t:
                page=t.pages[0];tags=page.tags
                compression={'raw':1,'deflate':8,'lzw':5,'packbits':32773}[plan['compression']]
                if len(t.pages)!=1 or int(page.compression)!=compression or page.bitspersample!=expected.dtype.itemsize*8:
                    raise ValueError('TIFF compression/depth mismatch')
                if int(page.sampleformat)!=(3 if depth=='float' else 1) or int(tags[274].value)!=1:
                    raise ValueError('TIFF sample type/orientation mismatch')
                if plan['channels']==4 and tuple(map(int,page.extrasamples))!=(2,):raise ValueError('TIFF alpha association mismatch')
        # No previous member's native arrays survive into the next decode.
        del data,expected,actual
        if 'blob' in locals():del blob
        if not encode and index%128==0:print(f'mediaforge_validated={index}',flush=True)
    print(json.dumps(dict(validated=count,format=plan['format'],depth=plan['depth'])))


if __name__=='__main__':
    # Use the base interpreter (no venv launcher subprocess) with only the
    # explicitly installed project site-packages added by the parent.
    sys.path.insert(0,sys.argv[1])
    main(sys.argv[2],sys.argv[3]=='encode')
