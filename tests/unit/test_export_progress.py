from mediaforge.features.media.export import ExportControl
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor


def test_progress_uses_actual_rational_pts_with_b_frame_reordering():
    records=[]
    update=FfmpegExportExecutor._progress(ExportControl(on_progress=lambda *r:records.append(r)),.4)
    update('out_time_us=0\nspeed=0x\n')
    assert not records  # copyts FFmpeg status is not a reliable clock.
    update('mediaforge_pts=3000|1/100000|0\n')
    update('mediaforge_pts=11000|1/100000|1\n')
    update('mediaforge_pts=7000|1/100000|2\n')
    assert [r[0] for r in records]==[.075,.275,.275]
    assert all(r[1].endswith('x') and float(r[2])>0 for r in records)
    update('mediaforge_pts=9223372036854775807|1/1000|3\n')
    assert len(records)==3
