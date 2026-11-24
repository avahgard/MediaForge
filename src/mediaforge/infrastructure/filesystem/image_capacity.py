"""Conservative disk admission; no success inferred from a free-space sample."""
import shutil
from mediaforge.features.media.export import ExportError


def required_bytes(request):
    # Includes higher-depth outputs and authoritative PNG codec intermediates.
    # Sixteen RGBA8-equivalent rasters per
    # picture plus 1 MiB framing/codec metadata and 64 KiB filesystem allocation.
    # Include both hash manifests, input concat/control files, and 25%/64MiB margin.
    # Deliberately overestimates compressed output; never assumes compression gains.
    if request.container not in ('png','jpeg','tiff','webp','mp4','mov'):
        raise ExportError('capacity_unknown','No staging capacity bound for this format.')
    framing=8192 if request.mode=='sequence' else 1024**2
    amount=request.count*(max(request.width*request.height,request.source_width*request.source_height)*64+framing+65536)+16*1024**2
    return amount+max(64*1024**2,(amount+3)//4)


def free_bytes(folder):
    try:
        free=shutil.disk_usage(folder).free
        if type(free)is not int or free<0:raise ValueError('Invalid available space')
        return free
    except (OSError,ValueError) as error:
        raise ExportError('capacity_unknown','Destination available space could not be established; nothing was published.') from error


def check(request,output=None):
    used=0
    if output is not None:
        paths=[*output.auxiliary,*(output.members if output.is_sequence else [output.staged])]
        for path in paths:
            if path is None:continue
            try:s=path.lstat()
            except FileNotFoundError:continue
            if s.st_file_attributes&0x400:raise ExportError('unsafe_path','Reparse staging member refused.')
            used+=s.st_size
    remaining=max(16*1024**2,required_bytes(request)-used)
    if free_bytes(request.destination.parent)<remaining:
        raise ExportError('insufficient_space',f'Destination needs at least {remaining:,} additional bytes for staged output and validation. Choose a volume with more free space.')


def check_floor(request):
    if free_bytes(request.destination.parent)<16*1024**2:
        raise ExportError('insufficient_space','Destination ran below the staging safety reserve. Nothing was published.')
