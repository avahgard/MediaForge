"""Fixed Architect gates; synthetic success is not native speech qualification."""
import array,math,json
from fractions import Fraction as F
from types import SimpleNamespace as NS
import pytest
from mediaforge.features.dub.alignment import Support,supports,qualify,qualify_core,activity_runs,anchored_fit,corrected_tempo
from mediaforge.features.dub.speech import TimingError
from mediaforge.infrastructure.ffmpeg.speech_support import SupportScan,verify_protected_zeros
from mediaforge.application.contracts.local_ai import LocalAiError

def frames(grid=0):return dict(offset=grid,scores=[.1]*4+[.9]*7+[.1]*5)

def test_both_fixed_grids_and_exact_waveform_support():
    s=Support(F(2048),F(5632),2048,5632,16000)
    assert qualify(s,[frames(),frames(256)])==((2048,5632),(2304,5888))

@pytest.mark.parametrize('change',['quiet-onset','disagree','uncertain','nonfinite','missing-grid'])
def test_boundary_gates_refuse_without_erasing_waveform(change):
    s=Support(F(2048),F(5632),2048,5632,16000);rows=[frames(),frames(256)]
    if change=='quiet-onset':s=Support(F(800),F(5632),800,5632,16000)
    if change=='disagree':rows[1]['scores']=[.1]*6+[.9]*5+[.1]*5
    if change=='uncertain':rows[0]['scores']=[.79]*16
    if change=='nonfinite':rows[0]['scores'][0]=math.nan
    if change=='missing-grid':rows[1]['offset']=0
    with pytest.raises(TimingError) as error:qualify(s,rows)
    assert error.value.code=='speech_boundary_uncertain'

def test_three_frames_are_required_without_padding():
    assert not activity_runs([.1,.8,.8,.1],0,speech=True)
    assert activity_runs([.1,.8,.8,.8],256,speech=True)==((768,2304),)

def scan(values,channels=1,rate=16000):
    raw=array.array('d',values).tobytes();scanner=SupportScan(rate,channels,len(values)//channels,NS(check=lambda:None))
    for i in range(0,len(raw),137):scanner.consume(raw[i:i+137])
    return scanner.finish()

def test_antiphase_and_sub_pcm16_nonzero_are_not_silence():
    ledger=scan([0.,0.]*1600+[1e-12,-1e-12]*16+[0.,0.]*1600,2)
    assert ledger['zeros']==[(0,1600),(1616,3216)]
    islands=supports(ledger['zeros'],3216,16000)
    assert [(s.start,s.end) for s in islands]==[(1600,1616)]

def test_exact_rational_native_clock_and_96ms_interior_zero():
    ledger=scan([1.]+[0.]*4233+[1.]+[0.]*4234+[1.],rate=44100)
    assert ledger['zeros']==[(4235,8469)] # 4233 < 96 ms; 4234 >= 96 ms
    units=supports(ledger['zeros'],8470,44100)
    assert units[0].end==F(4235*16000,44100) and units[1].start==F(8469*16000,44100)

def test_incomplete_nonfinite_and_cancelled_reference_refuse():
    with pytest.raises(LocalAiError):scan([float('nan')])
    s=SupportScan(16000,1,1,NS(check=lambda:None));s.consume(b'\0')
    with pytest.raises(LocalAiError):s.finish()
    def cancelled():raise LocalAiError('cancelled','Cancelled scan')
    with pytest.raises(LocalAiError,match='Cancelled'):SupportScan(16000,1,1,NS(check=cancelled)).consume(bytes(8))

def test_fit_counts_both_guards_inside_real_source_slot():
    fit=anchored_fit(0,48000,48000-1920-1920)
    assert fit.ratio==1
    with pytest.raises(TimingError) as error:anchored_fit(0,48000,60000)
    assert error.value.code=='timing_unfit'

def test_protected_pause_requires_exact_zero_without_mask(tmp_path):
    p=tmp_path/'output.pcm';raw=bytearray(4800*2);raw[3*100*2]=1;p.write_bytes(raw)
    ledger=dict(rate=16000,zeros=[(0,100)])
    verify_protected_zeros(p,ledger,4800,NS(check=lambda:None))
    ledger['zeros']=[(0,101)]
    with pytest.raises(LocalAiError) as error:verify_protected_zeros(p,ledger,4800,NS(check=lambda:None))
    assert error.value.code=='timing_unfit' and p.read_bytes()==raw

def core_rows():return [frames(),dict(offset=256,scores=frames(256)['scores'][:-1])]
def core_support():return Support(F(2048),F(5632),2048,5632,16000)
def test_amended_interior_core_keeps_full_waveform_support():
    s=core_support();receipt=qualify_core(s,core_rows(),[(F(0),s.start),(s.end,F(8192))],window=(0,8192))
    assert receipt['support']==['2048','5632'] and receipt['common_core']==['2304','5632']
    assert receipt['grids'][1]['envelope']==[2304,5888] # no crop or boundary shift
    assert json.loads(json.dumps(receipt))==receipt # same-process sealed journal uses this exact representation

@pytest.mark.parametrize('bad',['one-grid','padded','missing-bracket','low-inside','wide-dip','wrong-offset','no-core','grid-conflict'])
def test_amended_core_topology_refuses(bad):
    s=core_support();rows=core_rows();zeros=[(F(0),s.start),(s.end,F(8192))]
    if bad=='one-grid':rows=rows[:1]
    if bad=='padded':rows[1]['scores'].append(.1)
    if bad=='missing-bracket':zeros=zeros[:1]
    if bad=='low-inside':rows[0]['scores'][4:7]=[.1]*3
    if bad=='wide-dip':rows[0]['scores'][6:9]=[.5]*3
    if bad=='wrong-offset':rows[1]['offset']=512
    if bad=='no-core':rows[1]['scores']=[.1]*15
    if bad=='grid-conflict':rows[1]['scores']=[.1]*6+[.9]*5+[.1]*4
    with pytest.raises(TimingError) as error:qualify_core(s,rows,zeros,window=(0,8192))
    assert error.value.code=='speech_boundary_uncertain'

def test_one_correction_can_use_cap_without_exceeding_it_or_repeating_cap():
    correction,proposed=corrected_tempo(F(123,100),55000,53739)
    assert proposed>F(5,4) and correction==F(5,4)
    with pytest.raises(TimingError) as error:corrected_tempo(F(5,4),55000,53739)
    assert error.value.code=='timing_unfit'

@pytest.mark.parametrize('malformed',[None,{},[{},{}],[dict(offset=0,scores=1),dict(offset=256,scores=[])]])
def test_malformed_activity_receipt_has_a_typed_uncertainty(malformed):
    with pytest.raises(TimingError) as refused:qualify_core(core_support(),malformed,[],window=(0,8192))
    assert refused.value.code=='speech_boundary_uncertain'

def test_single_high_component_cannot_bridge_a_protected_source_island_gap():
    s=core_support()
    rows=[dict(offset=0,scores=[.1]*4+[.9]*20),dict(offset=256,scores=[.1]*4+[.9]*19)]
    with pytest.raises(TimingError) as refused:
        qualify_core(s,rows,[(F(0),s.start),(s.end,F(8192))],window=(0,12288))
    assert refused.value.code=='speech_boundary_uncertain'
