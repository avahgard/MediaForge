"""Typed original samples for qualified PNG/TIFF; no Qt or Viewer authority."""
import numpy as np
import imagecodecs


def read_samples(blob,format,cancel=None):
    if cancel is not None:cancel()
    data={'png':imagecodecs.png_decode,'tiff':imagecodecs.tiff_decode}[format](blob)
    if data.dtype not in (np.dtype('uint8'),np.dtype('uint16'),np.dtype('float32')):
        raise ValueError('Unsupported decoded sample representation.')
    if data.ndim==2:data=np.repeat(data[:,:,None],3,axis=2)
    elif data.ndim==3 and data.shape[2] in (1,2):
        gray=np.repeat(data[:,:,:1],3,axis=2)
        data=np.concatenate((gray,data[:,:,1:]),axis=2) if data.shape[2]==2 else gray
    if data.ndim!=3 or data.shape[2] not in (3,4):raise ValueError('Unsupported decoded channels.')
    validate_floats(data,cancel)
    return data


def validate_floats(data,cancel=None):
    """Source AND transformed float destinations must remain reopenable."""
    if data.dtype==np.float32:
        # Validate one row at a time: no full-frame boolean scratch allocation.
        step=max(1,1024**2//(data.shape[1]*data.shape[2]))
        for start in range(0,len(data),step):
            if cancel is not None:cancel()
            row=data[start:start+step]
            if not np.isfinite(row).all():raise ValueError('Non-finite float samples are unsupported.')
            if data.shape[2]==4 and (np.any(row[:,:,3]<0) or np.any(row[:,:,3]>1)):
                raise ValueError('Float alpha must be in [0,1].')


def orient(data,orientation):
    return {1:lambda a:a,2:lambda a:a[:,::-1],3:lambda a:a[::-1,::-1],
        4:lambda a:a[::-1],5:lambda a:a.transpose(1,0,2),
        6:lambda a:np.rot90(a,-1),7:lambda a:a.transpose(1,0,2)[::-1,::-1],
        8:lambda a:np.rot90(a,1)}[orientation](data)


def convert(data,depth):
    """Explicit normalized integer/float conversion, once at the destination.

    Float-to-integer clips [0,1] and rounds half upward. Float destinations
    preserve finite float32 values exactly; no implicit transfer curve or HDR gain.
    """
    if depth=='float':
        return data if data.dtype==np.float32 else data.astype(np.float32)/np.float32(np.iinfo(data.dtype).max)
    dtype=np.uint16 if depth=='16' else np.uint8;maximum=np.iinfo(dtype).max
    if data.dtype==dtype:return data
    if data.dtype==np.float32:return np.floor(np.clip(data,0,1).astype(np.float64)*maximum+.5).astype(dtype)
    if dtype==np.uint16:return data.astype(np.uint16)*257
    return ((data.astype(np.uint32)+128)//257).astype(np.uint8)


def float_area(data,plan):
    """Alpha-aware pixel-cell areas in float64, no sample-by-sample Python loop."""
    from fractions import Fraction
    h,w,c=data.shape;t=plan['transform'];oh,ow=plan['height'],plan['width']
    def integrate(values,n,out,extent,origin,axis):
        scale=float(Fraction(n)/Fraction(extent));offset=-float(Fraction(origin))*scale
        edges=np.clip(np.arange(out+1,dtype=np.float64)*scale+offset,0,n)
        q=np.floor(edges).astype(np.int64);r=edges-q
        prefix=np.cumsum(values,axis=axis,dtype=np.float64)
        before=np.take(prefix,np.maximum(q-1,0),axis=axis);shape=[1]*values.ndim;shape[axis]=len(q)
        before*=np.reshape(q>0,shape)
        sample=np.take(prefix,np.minimum(q,n-1),axis=axis)-before
        return np.diff(before+sample*np.reshape(r,shape),axis=axis),scale
    def area(values):
        v,sx=integrate(values,w,ow,t['picture_width'],t['x'],1)
        v,sy=integrate(v,h,oh,t['picture_height'],t['y'],0)
        return v,sx*sy
    alpha,cell=area(data[:,:,3].astype(np.float64));out=np.empty((oh,ow,4),np.float32);out[:,:,3]=alpha/cell
    for k in range(3):
        total,_=area(data[:,:,k].astype(np.float64)*data[:,:,3]);channel=np.zeros((oh,ow),np.float64)
        np.divide(total,alpha,out=channel,where=alpha!=0);out[:,:,k]=channel
    return out


def bt709_to_srgb(data):
    """Full-range CICP 1/1/0/1 BT.709 OETF -> sRGB, alpha unchanged.

    RGB primaries match. Work in float64; float32 output avoids an 8-bit
    processing intermediate. Integer quantization happens only at destination.
    """
    work=convert(data,'float').copy();rgb=work[:,:,:3].astype(np.float64)
    linear=np.where(rgb<.081,rgb/4.5,np.power(np.maximum((rgb+.099)/1.099,0),1/.45))
    work[:,:,:3]=np.where(linear<=.0031308,12.92*linear,1.055*np.power(np.maximum(linear,0),1/2.4)-.055)
    return work
