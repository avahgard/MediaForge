import array
from fractions import Fraction as F
import pytest

from mediaforge.features.dub.alignment import Support
from mediaforge.features.dub.practical_timing import admit_hard_island
from mediaforge.features.dub.speech import TimingError


def fixture(*,pause=False):
    end=24576;p=4096;q=20480
    values=array.array('h',[0]*p+[1000]*(q-p)+[0]*(end-q))
    grids=[]
    for offset in (0,256):
        scores=[.01]*((end-offset)//512)
        for i in range(8,40):scores[i]=.95
        for i in range(20,26 if pause else 24):scores[i]=.01 if pause else .5
        grids.append(dict(offset=offset,scores=scores))
    if pause:
        values[10240:13312]=array.array('h',[2]*(13312-10240))
        words=[dict(start=p,end=10240,text=' Keep',probability=.95),
               dict(start=13312,end=16384,text=' every',probability=.95),
               dict(start=16384,end=q,text=' word.',probability=.95)]
    else:
        words=[dict(start=p,end=12288,text=' Keep',probability=.95),
               dict(start=12288,end=16384,text=' every',probability=.95),
               dict(start=16384,end=q,text=' word.',probability=.95)]
    return Support(F(p),F(q),p,q,16000),grids,[(F(0),F(p)),(F(q),F(end))],(0,end),words,'Keep every word.',values.tobytes()


def test_weak_high_core_gap_needs_word_and_waveform_corroboration():
    parts,pauses=admit_hard_island(*fixture())
    assert len(parts)==1 and parts[0].estimated and parts[0].text=='Keep every word.'
    assert parts[0].start==4096 and parts[0].end==20480 and pauses==()


def test_quiet_asr_vad_pause_partition_keeps_every_complete_word_and_source_clock():
    parts,pauses=admit_hard_island(*fixture(pause=True))
    assert [p.text for p in parts]==['Keep','every word.']
    assert pauses==((F(10496),F(13056)),)
    assert parts[0].start==4096 and parts[0].end==pauses[0][0]
    assert parts[1].start==pauses[0][1] and parts[1].end==20480
    assert len(parts[0].words)+len(parts[1].words)==3


def test_low_region_inside_one_word_without_a_plausible_boundary_refuses():
    values=list(fixture(pause=True));words=values[4]
    words[0]['end']=18000;words[1]['start']=18000;words[1]['end']=19000;words[2]['start']=19000
    with pytest.raises(TimingError,match='complete-word pause'):
        admit_hard_island(*values)


def test_activity_cannot_declare_loud_wordform_to_be_silence():
    values=list(fixture(pause=True));raw=array.array('h',values[-1]);raw[10240:13312]=array.array('h',[1000]*3072);values[-1]=raw.tobytes()
    with pytest.raises(TimingError,match='disagree'):
        admit_hard_island(*values)


def test_estimated_admission_refuses_low_boundary_confidence_and_grid_disagreement():
    values=list(fixture());values[4][0]['probability']=.1
    with pytest.raises(TimingError,match='confidence'):admit_hard_island(*values)
    values=list(fixture());values[1][1]['offset']=128
    with pytest.raises(TimingError,match='grids'):admit_hard_island(*values)


def test_incomplete_word_coverage_is_never_repaired_or_dropped():
    values=list(fixture());values[4][1]['text']=' changed'
    with pytest.raises(TimingError,match='complete ordered'):admit_hard_island(*values)


def test_corrobated_quiet_near_an_estimated_word_boundary_is_excluded_never_filled():
    values=list(fixture());raw=array.array('h',values[-1]);raw[10240:12544]=array.array('h',[2]*(12544-10240));values[-1]=raw.tobytes()
    parts,pauses=admit_hard_island(*values)
    assert pauses==((F(10496),F(12288)),)
    assert [p.text for p in parts]==['Keep','every word.']
    assert parts[0].end==pauses[0][0] and parts[1].start==pauses[0][1]


def test_quieter_complete_word_is_not_reclassified_as_silence_by_loud_vowel():
    values=list(fixture());raw=array.array('h',values[-1])
    raw[10240:12544]=array.array('h',[40]*(12544-10240));values[-1]=raw.tobytes()
    parts,pauses=admit_hard_island(*values)
    assert len(parts)==1 and not pauses and parts[0].estimated


def test_bracket_connected_low_run_is_bounded_outer_uncertainty_not_internal_pause():
    values=list(fixture())
    for row in values[1]:row['scores'][8:11]=[.01]*3
    parts,pauses=admit_hard_island(*values)
    assert parts[0].start==4096 and not pauses


def test_outer_uncertainty_over_200ms_refuses_without_moving_source_clock():
    values=list(fixture())
    for row in values[1]:row['scores'][8:16]=[.01]*8
    with pytest.raises(TimingError,match='200 ms'):admit_hard_island(*values)


def test_exact_file_origin_retains_entire_clock_without_inventing_prior_silence():
    values=list(fixture());s=values[0]
    values[0]=Support(F(0),s.end,0,s.native_end,s.native_rate)
    values[2]=values[2][1:];values[4][0]['start']=0
    raw=array.array('h',values[-1]);raw[512:4096]=array.array('h',[1000]*(4096-512));values[-1]=raw.tobytes()
    for row,start in zip(values[1],(3,1)):
        row['scores'][start:8]=[.95]*(8-start)
    with pytest.raises(TimingError,match='bracket'):admit_hard_island(*values,estimated_outer=True)
    parts,_=admit_hard_island(*values,estimated_outer=True,file_samples=values[3][1])
    assert parts[0].start==0 and parts[0].end==s.end and parts[0].estimated
    assert parts[0].evidence['exact_file_edges']==dict(start=True,end=False)
    assert len(parts[0].words)==3 and parts[0].text==values[5]


def test_file_edge_is_not_permission_to_admit_unbracketed_background_or_late_speech():
    values=list(fixture());s=values[0];values[0]=Support(F(0),s.end,0,s.native_end,s.native_rate)
    values[2]=[]
    with pytest.raises(TimingError,match='bracket'):admit_hard_island(*values,estimated_outer=True,file_samples=values[3][1])
    values[2]=[(s.end,F(values[3][1]))];values[4][0]['start']=4096
    with pytest.raises(TimingError,match='bracket'):admit_hard_island(*values,estimated_outer=True,file_samples=values[3][1])
