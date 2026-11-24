"""One fixed NASA prefix gate; no translation, Qwen, pilot or output route.

Run through the authenticated private Core entry. The historical non-fail-fast
controller is never imported or overwritten. All new receipts are exclusive.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace as NS


def source_record(identity):
    value = asdict(identity)
    value['path'] = str(value['path'])
    return value


def prefix_gate(prepare, admit):
    """Propagate the FIRST failure. No partial-progress success flag exists."""
    state = dict(stage='planning', envelope=None)
    try:
        prepared = prepare()
        outputs = []
        for index in range(3):
            envelope = prepared['envelopes'][index]
            state = dict(stage='envelope', envelope=index, first=envelope.first, last=envelope.last,
                         source_range=[str(envelope.start), str(envelope.end)])
            outputs.append(admit(prepared, index))
        return outputs
    except BaseException as error:
        if not hasattr(error, 'admission_context'):
            error.admission_context = state
        raise


def main():
    import argparse
    from mediaforge.application.contracts.local_ai import LocalAiError
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    from mediaforge.application.services.speech_islands import prepare_envelopes, bind_envelope_plan, admit_envelope, binding
    from mediaforge.bootstrap.dub_models import compose_dub_models
    from mediaforge.features.dub.contracts import DubControl, OutputMode
    from mediaforge.features.dub.planning import output_plan
    from mediaforge.features.dub.alignment import supports
    from mediaforge.infrastructure.models.final_speech_providers import FinalSpeechProviders
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
    from mediaforge.infrastructure.ffmpeg.qwen_speech import QwenSpeechAudio
    from probe_dub_qwen import ordinary_token

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    r = args.evidence.resolve()
    p = r.parents[1]
    paths = ProfilePaths.for_test(p)
    paths.activate_temporary()
    if r.parent != paths.data or (r/'native-prefix.json').exists():
        raise ValueError('New receipt within the explicit portable evidence root required.')
    prior = p/'data/audio-admission-correction-20261029'
    source_path = p/'data/one-hour-qualification-20261029/source/nasa-20230801-human-high.mp4'
    ledger_raw = (prior/'private-asr/native-ledger.json').read_bytes()
    if hashlib.sha256(ledger_raw).hexdigest() != '4494efea44b2808ba8b109614c4635cecc396c7bae8735e517e2984a3677f99c':
        raise ValueError('Frozen native test geometry differs; no enrollment.')
    ledger = json.loads(ledger_raw)
    started = time.monotonic()

    class Control(DubControl):
        def check(self):
            super().check()
            if time.monotonic()-started > 900:
                raise LocalAiError('provider_timeout', 'Fixed native prefix exceeded 900 seconds.')

    control = Control()
    gate = MediaProcessingGate()
    token = gate.acquire()
    store = SpeechSessions(paths)
    providers = FinalSpeechProviders.from_local(compose_dub_models(paths, gate).local_ai)
    audio = QwenSpeechAudio(paths, resolve_ffmpeg_runtime(profile=paths))
    result = dict(ok=False, stage='preflight', native_observations=0, token=ordinary_token(),
                  translation=False, qwen=False, full_source_admission=False, pilots=False,
                  independent_reference='NOT AVAILABLE; no content-completeness PASS from ASR alone',
                  geometry_is_test_fixture=True, recognition_policy=binding())
    private = r/'private-prefix'
    private.mkdir()
    evidence_bytes = 0

    def save(name, value):
        nonlocal evidence_bytes
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(raw) > 2*1024**2 or evidence_bytes+len(raw) > 8*1024**2:
            raise LocalAiError('disk_budget', 'Prefix private evidence exceeds its reserved 8 MiB.')
        with (private/name).open('xb') as stream:
            stream.write(raw)
        evidence_bytes += len(raw)

    selected = providers._selected

    def observed_selected(*a, **kw):
        result['native_observations'] += 1
        if result['native_observations'] > 3:
            raise LocalAiError('resource_unavailable', 'Fixed prefix cannot exceed three ASR requests.')
        reply, spec = selected(*a, **kw)
        save(f'native-reply-{result["native_observations"]:02d}.json', reply)
        return reply, spec

    providers._selected = observed_selected
    try:
        if result['token']['integrity'] != 8192 or result['token']['elevated']:
            raise LocalAiError('resource_unavailable', 'Ordinary non-elevated Medium token required.')
        source = FfprobeDubInspector(audio.runtime, paths, timeout=1200, qualified_mux_metadata=True).inspect(source_path, control)
        if source.identity.sha256 != '2099024a9d5f50ebb66f70ab0b09fa1483dfe1eee3266ef1fa6f6e57413a941f':
            raise LocalAiError('source_changed', 'Fixed NASA source differs.')
        plan = output_plan(source, OutputMode.SAME_AS_SOURCE)
        result['source'] = source_record(source.identity)
        workspace = plan.sample_count*4+int(plan.duration*16000)*2+16*1024**2
        with providers.session(control, owner=token), store.admission(control, workspace_bytes=workspace), ProtectedDubSource(source_path, control) as held:
            if held.identity != source.identity:
                raise LocalAiError('source_changed', 'Source lease differs.')
            with providers.selected_models(('whisper-base',), control) as models:
                model_binding = {key: asdict(spec) for key, spec in models.items()}
                job_binding = dict(source=source_record(source.identity), track=plan.audio_index,
                                   origin=str(plan.origin), duration=str(plan.duration), samples=plan.sample_count,
                                   model=model_binding, runtime=providers.binding(), ffmpeg=audio.binding(),
                                   policy=binding(), test_geometry_sha256=hashlib.sha256(ledger_raw).hexdigest())
                with store.open(NS(retry_session=None, retain_private_recovery=True), job_binding, control) as session:
                    result['stage'] = 'extraction'
                    extract = session.name('source.pcm')
                    samples = audio.extract(source_path, plan, extract, control)
                    session.track(extract)
                    audio.verify_extraction(source_path, plan, extract, control)
                    result['complete_correspondence'] = True
                    original = supports(ledger['zeros'], ledger['samples'], ledger['rate'])
                    with extract.open('rb') as stream:
                        def prepare():
                            control.check()
                            value = prepare_envelopes(original, ledger, samples)
                            observed = [(e.first, e.last, e.begin, e.stop) for e in value['envelopes'][:3]]
                            expected = [(0,10,0,403392), (11,17,403392,801152), (18,24,801152,1219328)]
                            if len(value['envelopes']) != 151 or observed != expected:
                                raise LocalAiError('invalid_timing', 'Predetermined NASA envelope geometry differs.')
                            bind_envelope_plan(value, session)
                            result['plan'] = value['fingerprint']
                            result['first_three'] = [e.record() for e in value['envelopes'][:3]]
                            return value

                        def admit(value, index):
                            result.update(stage='envelope', current_envelope=index)
                            first_error = None
                            try:
                                rows, pauses = admit_envelope(value, index, stream, samples, providers, session, control, lambda *a: None)
                                result.setdefault('passed_envelopes', []).append(index)
                                return dict(rows=[dict(segment=asdict(s), lineage=lineage, anchor=anchor) for s,lineage,anchor in rows],
                                            pauses=[[str(a),str(b)] for a,b in pauses])
                            except BaseException as error:
                                first_error = error
                                raise
                            finally:
                                # Retain actual parent evidence before normal owned-session retirement.
                                try:
                                    for part in range(2):
                                        name = f'envelope-native-{index}-{part}.json'
                                        cached = session.get(name)
                                        if cached is not None:
                                            save(name, cached)
                                except BaseException as error:
                                    if first_error is None:
                                        raise
                                    result['evidence_retention_error'] = str(error)

                        qualified = prefix_gate(prepare, admit)
                        save('qualified-prefix.json', qualified)
                        result.update(engineering_prefix=True, stage='reference_completeness')
                        # No independent English boundary/reference annotation exists in
                        # this controller. Do not silently turn numerical PASS into it.
                        raise LocalAiError('reference_unqualified', 'Independent prefix content/boundary reference is not established; no full-source continuation.')
    except BaseException as error:
        result.update(ok=False, code=str(getattr(error, 'code', 'prefix_failed')), detail=str(error),
                      first_failure=getattr(error, 'admission_context', dict(stage=result['stage'])))
    finally:
        providers._selected = selected
        if not gate.failure and not control.cleanup_failed:
            try:
                store.close(DubControl())
                result['owned_sessions_retired'] = not store._tickets
            except BaseException as error:
                result['retirement_error'] = str(error)
                gate.poison(token, 'Prefix session retirement failed.')
        if not gate.failure:
            gate.release(token)
        result.update(seconds=time.monotonic()-started, private_evidence_bytes=evidence_bytes,
                      cpu=providers.statistics if hasattr(providers, 'statistics') else {},
                      native_receipts=providers.receipts, audio_receipts=audio.receipts,
                      gate_busy=gate.busy, gate_failure=gate.failure,
                      controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        with (r/'native-prefix.json').open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: result.get(k) for k in ('ok','code','detail','first_failure','native_observations','seconds','owned_sessions_retired','gate_busy','gate_failure')}), flush=True)
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
