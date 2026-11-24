"""Fail-closed ordinary 8-bit RGB/RGBA metadata and selected-picture decoding."""
from contextlib import contextmanager
import ctypes
import hashlib
import os
from pathlib import Path
import stat
import struct
import zlib
import time
from PySide6.QtCore import QFile, QIODevice, QSize
from PySide6.QtGui import QColorSpace, QImage, QImageReader
from mediaforge.application.contracts.image_source import ImageFacts, ImageRaster, ImageSignature, StillSource
from mediaforge.application.contracts.image_source import ImagePreviewRaster

from contextvars import ContextVar
_cancel_check = ContextVar('image_cancel_check',default=None)
_metadata_size = ContextVar('image_metadata_size',default=None)

def check_cancel():
    cancel=_cancel_check.get()
    if cancel is not None:
        if callable(cancel):cancel()
        elif cancel.is_set():raise ValueError('Image preparation canceled.')

MAX_FILE_BYTES = 4 * 1024**3
MAX_METADATA_BYTES = 1024 * 1024


def safe_path(value):
    p = Path(os.path.abspath(value))
    if not Path(value).is_absolute() or ':' in str(p)[2:] or str(p).startswith(('\\\\?\\', '\\\\.\\')):
        raise ValueError('Choose an ordinary absolute image file path.')
    for part in (p, *p.parents):
        s = part.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('Image paths containing symlinks or reparse points are unsupported.')
    s = p.stat()
    if not stat.S_ISREG(s.st_mode) or not 0 < s.st_size <= MAX_FILE_BYTES:
        raise ValueError('Image must be a regular file of at most 4 GiB (streamed identity; separate decode admission).')
    return p


@contextmanager
def locked_file(path):
    """Deny write/delete during reading; recheck path identity after decoding."""
    p = safe_path(path)
    before = p.stat()
    if os.name == 'nt':
        import msvcrt
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        k.CreateFileW.restype = ctypes.c_void_p
        handle = k.CreateFileW(str(p), 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            k.CloseHandle.argtypes = [ctypes.c_void_p]; k.CloseHandle(handle)
            raise
        stream = os.fdopen(fd, 'rb')
    else:
        stream = p.open('rb')
    with stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('Image changed during opening.')
        yield p, stream
        after = safe_path(str(p)).stat()
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Image changed during decoding.')


class SharedImageGuard:
    """Reference-counted already-entered source lock, not a pathname receipt."""
    def __init__(self,guard,state=None):
        from threading import RLock
        self.state=state or dict(guard=guard,count=1,lock=RLock())
        self.closed=False
    def retain(self):
        with self.state['lock']:
            if self.closed:raise ValueError('Image source lease is already retired.')
            self.state['count']+=1
            return SharedImageGuard(None,self.state)
    def __exit__(self,*args):
        with self.state['lock']:
            if self.closed:return
            if self.state['count']==1:self.state['guard'].__exit__(*args)
            self.state['count']-=1;self.closed=True


def file_size(f):
    cached=_metadata_size.get()
    return cached[1] if cached is not None and cached[0]==id(f) else os.fstat(f.fileno()).st_size


def read_at(f, offset, size):
    if size < 0 or size > MAX_METADATA_BYTES or offset < 0 or offset + size > file_size(f):
        raise ValueError('Invalid or excessive image metadata.')
    f.seek(offset); data = f.read(size)
    if len(data) != size:
        raise ValueError('Truncated image metadata.')
    return data


def srgb_profile(data):
    color = QColorSpace.fromIccProfile(data)
    if not color.isValid() or color != QColorSpace(QColorSpace.NamedColorSpace.SRgb):
        raise ValueError('Only ordinary sRGB profiles are supported; no implicit color conversion.')


def tiff_tags(f, base=0, *, exif=False):
    header = read_at(f, base, 8)
    if header[:2] not in (b'II', b'MM'):
        raise ValueError('Unsupported TIFF byte order.')
    order = '<' if header[:2] == b'II' else '>'
    if struct.unpack(order+'H', header[2:4])[0] != 42:
        raise ValueError('BigTIFF and unknown TIFF variants are unsupported.')
    offset = base + struct.unpack(order+'I', header[4:8])[0]
    count = struct.unpack(order+'H', read_at(f, offset, 2))[0]
    if count > 512: raise ValueError('Excessive TIFF tag count.')
    data = read_at(f, offset+2, count*12+4); tags = {}
    sizes = {1:1, 2:1, 3:2, 4:4, 5:8, 7:1}
    retained = 0
    wanted = {274} if exif else {256,257,258,259,262,273,274,277,278,279,284,317,322,323,324,325,338,339,34675}
    for i in range(count):
        row = data[i*12:(i+1)*12]; tag, kind, n = struct.unpack(order+'HHI',row[:8])
        if tag not in wanted:continue
        if tag in tags: raise ValueError('Duplicate TIFF metadata tag.')
        if kind not in sizes:raise ValueError('Unsupported required TIFF tag type.')
        length = n*sizes[kind]
        retained += length
        if retained > MAX_METADATA_BYTES or (tag not in (273,279,34675) and n > 16): raise ValueError('Excessive TIFF tag payload.')
        payload = row[8:8+length] if length<=4 else read_at(f,base+struct.unpack(order+'I',row[8:12])[0],length)
        if kind in (1,3,4):
            tags[tag] = struct.unpack(order+({1:'B',3:'H',4:'I'}[kind])*n,payload)
        else: tags[tag] = payload
    if not exif and struct.unpack(order+'I',data[-4:])[0]:
        raise ValueError('Multipage TIFF is unsupported.')
    return tags


def metadata(f):
    before=os.fstat(f.fileno())
    token=_metadata_size.set((id(f),before.st_size))
    try:value=_metadata(f)
    finally:_metadata_size.reset(token)
    after=os.fstat(f.fileno())
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('Image identity changed during metadata validation.')
    return value


def _metadata(f):
    check_cancel()
    head = read_at(f, 0, min(12, file_size(f)))
    orientation = 1; alpha = False; width = height = 0; variant = '';depth=8;tagged=False;sample_type='uint'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        fmt='png'; offset=8; found=False;png_profile=None;png_cicp=None;png_gamma=None;png_chrm=None
        for _ in range(65536):
            check_cancel()
            length,tag=struct.unpack('>I4s',read_at(f,offset,8))
            end=offset+12+length
            if end>file_size(f):raise ValueError('Truncated PNG chunk.')
            if tag==b'IHDR':
                if found or offset!=8 or length!=13:raise ValueError('Invalid PNG header.')
                width,height,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',read_at(f,offset+8,13))
                valid={0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}
                if color not in valid or depth not in valid[color] or compression or filtering or interlace not in (0,1):
                    raise ValueError('Unsupported PNG pixel layout.')
                variant={0:'grayscale',2:'rgb',3:'indexed',4:'grayscale-alpha',6:'rgba'}[color]
                alpha=color in (4,6);found=True
            elif tag in (b'acTL',b'fcTL',b'fdAT'):
                raise ValueError('Animated PNG is unsupported.')
            elif tag==b'iCCP':
                payload=read_at(f,offset+8,length);name,rest=payload.split(b'\0',1)
                if not rest or rest[0]:raise ValueError('Invalid PNG color profile.')
                dec=zlib.decompressobj();profile=dec.decompress(rest[1:],MAX_METADATA_BYTES+1)
                if len(profile)>MAX_METADATA_BYTES or not dec.eof:raise ValueError('Excessive or invalid ICC profile.')
                png_profile=profile;tagged=True
            elif tag==b'sRGB':
                if read_at(f,offset+8,length) not in (b'\0',b'\1',b'\2',b'\3'):raise ValueError('Invalid PNG sRGB intent.')
                tagged=True
            elif tag==b'cICP':
                if png_cicp is not None:raise ValueError('Duplicate PNG color identity.')
                png_cicp=read_at(f,offset+8,length)
                if png_cicp not in (bytes([1,1,0,1]),bytes([1,13,0,1])):raise ValueError('PNG CICP color class is not qualified.')
                if struct.unpack('>I',read_at(f,offset+8+length,4))[0] != zlib.crc32(tag+png_cicp)&0xffffffff:
                    raise ValueError('Invalid PNG color-identity CRC.')
            elif tag==b'gAMA':png_gamma=read_at(f,offset+8,length)
            elif tag==b'cHRM':png_chrm=read_at(f,offset+8,length)
            elif tag==b'tRNS':
                alpha=True
            elif tag==b'eXIf':
                if tiff_tags(f,offset+8,exif=True).get(274,(1,))[0]!=1:
                    raise ValueError('Non-identity PNG EXIF orientation is not qualified; no implicit rotation is performed.')
            elif tag==b'IEND':
                if not found or length or end!=file_size(f):raise ValueError('Invalid PNG termination.')
                break
            offset=end
        else:raise ValueError('Excessive PNG chunk count.')
        # PNG3: a recognized cICP has precedence over ICC/sRGB/gamma hints.
        # Recognize only the exact full-range RGB BT.709/sRGB classes we handle.
        if png_cicp is not None:tagged=True
        elif png_profile is not None:srgb_profile(png_profile)
        elif not tagged:
            if png_gamma not in (None,struct.pack('>I',45455)):raise ValueError('PNG gamma is outside ordinary sRGB.')
            if png_chrm not in (None,struct.pack('>8I',31270,32900,64000,33000,30000,60000,15000,6000)):raise ValueError('PNG chromaticities are outside ordinary sRGB.')
    elif head[:2]==b'\xff\xd8':
        fmt='jpeg'; offset=2; profiles={}; profile_count=0
        for _ in range(4096):
            if read_at(f,offset,1)!=b'\xff':raise ValueError('Invalid JPEG marker.')
            while read_at(f,offset,1)==b'\xff':offset+=1
            marker=read_at(f,offset,1)[0];offset+=1
            if marker in (0xd9,0xda):break
            if marker==1 or 0xd0<=marker<=0xd7:continue
            length=struct.unpack('>H',read_at(f,offset,2))[0]
            if length<2:raise ValueError('Invalid JPEG segment.')
            data=read_at(f,offset+2,length-2)
            if marker in (0xc0,0xc1,0xc2):
                if width or height: raise ValueError('Multiple JPEG frame headers are unsupported.')
                variant = 'progressive' if marker == 0xc2 else 'sequential'
                depth,height,width,channels=struct.unpack('>BHHB',data[:6])
                if depth!=8 or channels!=3:raise ValueError('Only ordinary 8-bit RGB JPEG is supported.')
            elif marker==0xe1 and data.startswith(b'Exif\0\0'):
                tags=tiff_tags(f,offset+8,exif=True);orientation=tags.get(274,(1,))[0]
            elif marker==0xe2 and data.startswith(b'ICC_PROFILE\0'):
                if len(data)<14:raise ValueError('Invalid JPEG ICC segment.')
                n,total=data[12:14]
                if not 1<=n<=total or n in profiles or profile_count not in (0,total):raise ValueError('Ambiguous JPEG ICC segments.')
                profiles[n]=data[14:];profile_count=total
                if sum(map(len,profiles.values()))>MAX_METADATA_BYTES:raise ValueError('Excessive JPEG color profile.')
            offset+=length
        else:raise ValueError('Excessive JPEG segment count.')
        if profiles:
            if len(profiles)!=profile_count:raise ValueError('Incomplete JPEG color profile.')
            profile=b''.join(profiles[i] for i in range(1,profile_count+1))
            if len(profile)>MAX_METADATA_BYTES:raise ValueError('Excessive JPEG color profile.')
            srgb_profile(profile);tagged=True
    elif head[:2] in (b'II',b'MM'):
        fmt='tiff';tags=tiff_tags(f)
        width=tags.get(256,(0,))[0];height=tags.get(257,(0,))[0]
        channels=tags.get(277,(0,))[0];bits=tags.get(258,())
        sample=tags.get(339,(1,)*channels)
        if len(sample)==1:sample=sample*channels
        if (channels not in (1,2,3,4) or len(set(bits))!=1 or len(bits) not in (1,channels)
                or tags.get(262)!=(1 if channels<3 else 2,)
                or tags.get(259,(1,))[0] not in (1,5,8,32946,32773)):
            raise ValueError('Unsupported TIFF channels, compression or sample layout.')
        depth=bits[0]
        if sample==(3,)*channels and depth==32:sample_type='float'
        elif sample!=(1,)*channels or depth not in (8,16):
            raise ValueError('TIFF requires uint8/uint16 or finite float32 samples.')
        if any(t in tags for t in (322,323,324,325)) or tags.get(284,(1,))!=(1,):
            raise ValueError('Tiled or planar-separated TIFF is not qualified.')
        if channels in (2,4) and tags.get(338) not in ((1,),(2,)):raise ValueError('Unsupported TIFF alpha classification.')
        if channels in (2,4):variant='associated-alpha' if tags.get(338)==(1,) else 'straight-alpha'
        if 34675 in tags:srgb_profile(tags[34675]);tagged=True
        orientation=tags.get(274,(1,))[0];alpha=channels in (2,4)
        if depth>8 and variant=='associated-alpha':raise ValueError('High-depth associated-alpha TIFF is not a supported writer class; use straight alpha.')
    elif head[:4]==b'RIFF' and head[8:12]==b'WEBP':
        fmt='webp';size=struct.unpack('<I',head[4:8])[0]+8
        if size!=file_size(f):raise ValueError('Invalid WebP container size.')
        offset=12
        for _ in range(4096):
            if offset==size:break
            tag,length=struct.unpack('<4sI',read_at(f,offset,8));end=offset+8+length+(length&1)
            if end>size:raise ValueError('Truncated WebP chunk.')
            if tag in (b'ANIM',b'ANMF'):raise ValueError('Animated WebP is unsupported.')
            if tag==b'VP8X':
                data=read_at(f,offset+8,length)
                if len(data)!=10 or data[0]&2:raise ValueError('Animated/invalid WebP is unsupported.')
                width=int.from_bytes(data[4:7],'little')+1;height=int.from_bytes(data[7:10],'little')+1;alpha=bool(data[0]&16)
            elif tag==b'VP8L':
                data=read_at(f,offset+8,5)
                if data[0]!=0x2f:raise ValueError('Invalid lossless WebP header.')
                value=int.from_bytes(data[1:],'little');width=(value&0x3fff)+1;height=((value>>14)&0x3fff)+1;alpha=bool(value&(1<<28))
            elif tag==b'VP8 ' and not width:
                data=read_at(f,offset+8,10)
                if data[3:6]!=b'\x9d\x01\x2a':raise ValueError('Invalid lossy WebP header.')
                width=int.from_bytes(data[6:8],'little')&0x3fff;height=int.from_bytes(data[8:10],'little')&0x3fff
            elif tag==b'ICCP':srgb_profile(read_at(f,offset+8,length));tagged=True
            elif tag==b'EXIF':
                base=offset+8+(6 if read_at(f,offset+8,min(length,6))==b'Exif\0\0' else 0)
                orientation=tiff_tags(f,base,exif=True).get(274,(1,))[0]
            offset=end
        else:raise ValueError('Excessive WebP chunk count.')
    else:raise ValueError('Unsupported image payload. PNG/JPEG/TIFF/static WebP only.')
    if width<=0 or height<=0 or orientation not in range(1,9):raise ValueError('Invalid image dimensions/orientation.')
    if fmt=='webp' and orientation!=1:raise ValueError('Non-identity WebP EXIF orientation is not qualified.')
    if orientation in (5,6,7,8):width,height=height,width
    return ImageFacts(fmt,width,height,orientation,alpha,depth=depth,variant=variant,sample_type=sample_type,color_class='BT.709' if fmt=='png' and png_cicp==bytes([1,1,0,1]) else 'Tagged sRGB' if tagged else 'Assumed sRGB')


def signature(p, f):
    s=os.fstat(f.fileno());f.seek(0);digest=hashlib.sha256();buffer=bytearray(1024*1024)
    while True:
        check_cancel();count=f.readinto(buffer)
        if not count:break
        digest.update(memoryview(buffer)[:count])
    return ImageSignature(str(p),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,digest.hexdigest())


class QtImageReader:
    @contextmanager
    def cancellation(self,cancel):
        token=_cancel_check.set(cancel)
        try:yield
        finally:_cancel_check.reset(token)

    def validate_sequence(self, manifest, cancel):
        for source in manifest.members:
            if cancel.is_set(): raise ValueError('Sequence validation cancelled.')
            current = safe_path(source.path).stat()
            sig = source.signature
            if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns) != (sig.device,sig.inode,sig.size,sig.mtime_ns):
                raise ValueError('Sequence member changed. Explicit reimport is required.')

    def discover_sequence(self, path, cancel, *, automatic=False):
        from mediaforge.application.contracts.sequence import numbered_name, SequenceManifest
        selected = safe_path(path)
        prefix, _, padding, suffix, extension = numbered_name(selected.name)

        def listing():
            found = []
            path_bytes = 0
            with os.scandir(selected.parent) as entries:
                for scanned, entry in enumerate(entries, 1):
                    if cancel.is_set(): raise ValueError('Sequence import cancelled.')
                    if scanned > 20000: raise ValueError('Sequence directory exceeds the bounded 20,000-entry scan.')
                    try: p, number, width, s, ext = numbered_name(entry.name,allow_unsupported=True)
                    except ValueError: continue
                    if (p.casefold(), s.casefold()) != (prefix.casefold(), suffix.casefold()): continue
                    if (p, s, ext) != (prefix, suffix, extension):
                        if automatic:
                            continue
                        raise ValueError('Ambiguous mixed extension or case in sequence members.')
                    if width>9: raise ValueError('Sequence has an invalid frame number exceeding 9 digits.')
                    encoded_path = len(entry.path.encode('utf-8'))
                    path_bytes += encoded_path
                    if encoded_path > 2048 or path_bytes > 4*1024*1024:
                        raise ValueError('Sequence path metadata exceeds the bounded 2 KiB/member or 4 MiB aggregate allowance.')
                    found.append((number, entry.path, width))
                    if len(found) > 10000: raise ValueError('Sequence exceeds 10,000 slots.')
            return sorted(found)

        names = listing()
        if not names: raise ValueError('No numbered sequence members found.')
        padded = {width for number,_,width in names if width > len(str(number))}
        if len(padded) > 1: raise ValueError('Ambiguous mixed padding in sequence members.')
        padding = next(iter(padded)) if padded else min(width for _,_,width in names)
        if any(width != max(padding,len(str(number))) for number,_,width in names):
            raise ValueError('Ambiguous mixed padding in sequence members.')
        numbers = tuple(n for n, _, _ in names)
        if any(b != a+1 for a,b in zip(numbers,numbers[1:])):
            raise ValueError('Missing or duplicate numbered members. Gap Policy: Error.')
        members = []
        identities = set()
        for _, member_path, _ in names:
            if cancel.is_set(): raise ValueError('Sequence import cancelled.')
            with self.cancellation(cancel):source = self.probe(member_path)
            identity = (source.signature.device,source.signature.inode)
            if identity in identities: raise ValueError('Duplicate aliased sequence member.')
            identities.add(identity)
            if members and source.facts != members[0].facts:
                raise ValueError('Sequence members have mixed resolution, orientation, color or alpha class.')
            members.append(source)
        if names != listing(): raise ValueError('Sequence directory changed during discovery.')
        for source in members:
            current = safe_path(source.path).stat()
            sig = source.signature
            if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns) != (sig.device,sig.inode,sig.size,sig.mtime_ns):
                raise ValueError('Sequence member changed during discovery.')
        digest = hashlib.sha256()
        for member in members:
            digest.update(repr(member).encode('utf-8'));digest.update(b'\0')
        identity = digest.hexdigest()
        return SequenceManifest(tuple(members), numbers, prefix, suffix, padding, extension, identity)

    def decode_preview(self, source, plan, cancel, reuse=None):
        from mediaforge.application.contracts.image_source import preview_reuse_eligible
        if plan.reuse_source and (not plan.full_source or not preview_reuse_eligible(reuse,source)):
            raise ValueError('Admitted preview source pin is unavailable; a cold decode requires new admission.')
        # Keep the source write/delete-denied until GUI admission finishes.
        guard = locked_file(source.path)
        p, f = guard.__enter__()
        qfile = QFile()
        try:
            stat=os.fstat(f.fileno())
            same=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)==(
                source.signature.device,source.signature.inode,source.signature.size,source.signature.mtime_ns)
            if cancel.is_set() or not same or (signature(p,f)!=source.signature) or metadata(f)!=source.facts:
                raise ValueError('Image source changed or preparation canceled.')
            native_precision = source.facts.depth>8 or source.facts.format=='tiff' and source.facts.variant!='associated-alpha'
            reader=None
            coded = (source.facts.width,source.facts.height)
            if source.facts.orientation in (5,6,7,8): coded = coded[::-1]
            reduce_alpha = native_precision or source.facts.alpha and plan.divisor != 1
            if not native_precision:
                # Never attach QFile to a descriptor also consumed by the native
                # typed reader: Qt seeks would invalidate Python's buffered cursor.
                if not qfile.open(f.fileno(),QIODevice.OpenModeFlag.ReadOnly,QFile.FileHandleFlag.DontCloseHandle) or not qfile.seek(0):
                    raise ValueError('Could not read the locked image file.')
                reader=QImageReader(qfile,source.facts.format.encode());reader.setAutoTransform(True)
                if (reader.size().width(),reader.size().height())!=coded:
                    raise ValueError('Decoder source dimensions disagree with validated metadata.')
                if not plan.full_source and plan.divisor!=1 and not reduce_alpha:
                    size=(plan.width,plan.height)
                    if source.facts.orientation in (5,6,7,8):size=size[::-1]
                    reader.setScaledSize(QSize(*size))
            start = time.perf_counter()
            previous_limit=QImageReader.allocationLimit()
            try:
                QImageReader.setAllocationLimit(max(1,(plan.peak_bytes+1024**2-1)//1024**2))
                if plan.reuse_source:
                    # Hash/metadata validation can observe cancellation or retirement.
                    # An admitted reuse plan is never permission to allocate a decoder.
                    if not preview_reuse_eligible(reuse,source):
                        raise ValueError('Admitted preview source pin retired; cold decoding was refused.')
                    image=reuse.raster.straight
                elif native_precision:
                    from mediaforge.infrastructure.images.precision import read_samples, orient
                    import numpy as np
                    f.seek(0);data=orient(read_samples(f.read(),source.facts.format,check_cancel),source.facts.orientation)
                    if data.shape[:2]!=(source.facts.height,source.facts.width) or data.dtype!=np.dtype('float32' if source.facts.sample_type=='float' else 'uint16' if source.facts.depth>8 else 'uint8'):
                        raise ValueError('Native source dimensions/sample type disagree with admitted metadata.')
                    rgba=np.empty((*data.shape[:2],4),dtype=data.dtype);rgba[:,:,:3]=data[:,:,:3]
                    rgba[:,:,3]=data[:,:,3] if data.shape[2]==4 else (1 if data.dtype.kind=='f' else np.iinfo(data.dtype).max)
                    formats={('u',1):(QImage.Format_RGBX8888,QImage.Format_RGBA8888),('u',2):(QImage.Format_RGBX64,QImage.Format_RGBA64),('f',4):(QImage.Format_RGBX32FPx4,QImage.Format_RGBA32FPx4)}
                    image=QImage(rgba.data,rgba.shape[1],rgba.shape[0],rgba.strides[0],formats[(rgba.dtype.kind,rgba.dtype.itemsize)][int(source.facts.alpha)]).copy()
                    del data,rgba
                else:
                    image=reader.read()
            finally:QImageReader.setAllocationLimit(previous_limit)
            decoded = time.perf_counter()
            if image.isNull(): raise ValueError('Image decoding failed: '+(reader.errorString() if reader is not None else 'Native image allocation failed.'))
            expected = (source.facts.width,source.facts.height) if reduce_alpha or plan.full_source else (plan.width,plan.height)
            if (image.width(),image.height()) != expected or image.depth()>source.facts.decoded_bytes_per_pixel*8:
                raise ValueError('Decoded preview dimensions/depth/orientation disagree with the admitted plan.')
            if image.hasAlphaChannel() != source.facts.alpha:
                # Qt's scaled monochrome/grayscale handler can return an alpha
                # capable raster even though the PNG has no transparency. Prove
                # every actual alpha byte is opaque before explicit conversion.
                if source.facts.alpha:raise ValueError('Decoder lost source transparency.')
                rgba=image.convertToFormat(QImage.Format_RGBA8888)
                import numpy as np
                alpha=np.frombuffer(rgba.constBits(),dtype=np.uint8).reshape(rgba.height(),rgba.bytesPerLine())[:,3:rgba.width()*4:4]
                if int(alpha.min())!=255:
                    raise ValueError('Decoded alpha pixels disagree with opaque source metadata.')
                image=rgba.convertToFormat(QImage.Format_RGB32)
            if (image.colorSpace().isValid() and image.colorSpace()!=QColorSpace(QColorSpace.NamedColorSpace.SRgb)
                    and source.facts.color_class not in ('BT.709','Tagged sRGB')):
                raise ValueError('Unsupported decoded image color profile.')
            if cancel.is_set(): raise ValueError('Image preparation canceled.')
            from mediaforge.infrastructure.images.preview_channels import display_pixels, source_display
            sample=image
            if plan.region is not None and plan.region!=(0,0,image.width(),image.height()):
                from PySide6.QtCore import QRect
                sample=image.copy(QRect(*plan.region))
            display = display_pixels(source_display(sample,source.facts),plan.width,plan.height)
            if display.isNull() or display.bytesPerLine()!=plan.width*4:
                raise ValueError('Invalid premultiplied image preview backing.')
            return ImagePreviewRaster(source,plan,display,(decoded-start)*1000,
                                      (time.perf_counter()-decoded)*1000,
                                      image if source.facts.alpha or plan.full_source or source.facts.color_class=='BT.709' else None), SharedImageGuard(guard) if plan.full_source else guard
        except BaseException:
            guard.__exit__(*__import__('sys').exc_info())
            raise
        finally:
            qfile.close()

    def preview_guard(self,source,cancel):
        guard=locked_file(source.path);p,f=guard.__enter__()
        try:
            if cancel.is_set() or signature(p,f)!=source.signature:
                raise ValueError('Cached image source changed. Reimport is required.')
            return guard
        except BaseException:
            guard.__exit__(*__import__('sys').exc_info());raise

    def channel_preview(self,raster,channel,cancel):
        from dataclasses import replace
        if cancel.is_set():raise ValueError('Image preparation canceled.')
        if channel=='RGBA':return raster
        from mediaforge.infrastructure.images.preview_channels import display_pixels, source_display
        start=time.perf_counter()
        image=raster.straight if raster.straight is not None else raster.display
        if (channel=='RGB' and raster.straight is not None
                and image.format().name.endswith('Premultiplied')):
            # Qt's TIFF handler has already quantized/unpremultiplied away
            # hidden straight RGB. Never label reconstructed values as stored RGB.
            raise ValueError('Stored RGB is unavailable through this image decoder. RGBA and Alpha remain available.')
        if raster.plan.region is not None:
            from PySide6.QtCore import QRect
            image=image.copy(QRect(*raster.plan.region))
        pixels=display_pixels(source_display(image,raster.source.facts,channel),raster.plan.width,raster.plan.height,channel)
        return replace(raster,display=pixels,channel=channel,decode_ms=0,
                       normalize_ms=(time.perf_counter()-start)*1000)

    def is_image(self, path):
        p=Path(path)
        if p.suffix.lower() in ('.png','.jpg','.jpeg','.tif','.tiff','.webp','.gif','.ico','.exr','.apng'):
            return True
        try:
            with p.open('rb') as f:head=f.read(12)
        except OSError:return False
        return (head.startswith((b'\x89PNG',b'\xff\xd8',b'II*\0',b'MM\0*',b'GIF8',b'\0\0\x01\0',b'\x76\x2f\x31\x01'))
                or head[:4]==b'RIFF' and head[8:12]==b'WEBP')

    def probe(self, path):
        try:
            with locked_file(path) as (p,f):
                facts=metadata(f)
                if p.suffix.lower() not in {'png':('.png',),'jpeg':('.jpg','.jpeg'),'tiff':('.tif','.tiff'),'webp':('.webp',)}[facts.format]:
                    raise ValueError('Image filename and actual payload disagree.')
                return StillSource(signature(p,f),facts)
        except (struct.error, IndexError, KeyError, zlib.error, OverflowError) as error:
            raise ValueError('Invalid or unsupported image metadata.') from error

    def decode(self, source):
        if source.facts.depth>8:
            raise ValueError('Use the typed precision preview/processing path for high-depth sources; RGBA8 decode is not authoritative.')
        with locked_file(source.path) as (p,f):
            if signature(p,f)!=source.signature or metadata(f)!=source.facts:
                raise ValueError('Image source is missing or changed. Explicit reimport is required.')
            f.seek(0);qfile=QFile()
            if not qfile.open(f.fileno(),QIODevice.OpenModeFlag.ReadOnly,QFile.FileHandleFlag.DontCloseHandle):
                raise ValueError('Could not read the locked image file.')
            try:
                # Python's buffered seek can leave the shared OS descriptor at
                # its read-ahead position. Qt must seek the actual descriptor.
                if not qfile.seek(0):raise ValueError('Could not seek the locked image file.')
                reader=QImageReader(qfile,source.facts.format.encode());reader.setAutoTransform(True)
                image=reader.read()
                if image.isNull():raise ValueError('Image decoding failed: '+reader.errorString())
                if image.width()!=source.facts.width or image.height()!=source.facts.height or image.depth()>32:
                    raise ValueError('Decoded image disagrees with validated dimensions/depth/orientation.')
                if image.colorSpace().isValid() and image.colorSpace()!=QColorSpace(QColorSpace.NamedColorSpace.SRgb):
                    raise ValueError('Unsupported decoded image color profile.')
                rgba=image.convertToFormat(QImage.Format_RGBA8888)
                data=bytes(rgba.constBits())
                if len(data)!=source.facts.width*source.facts.height*4:raise ValueError('Unexpected image row padding.')
                return ImageRaster(source,data)
            finally:qfile.close()
