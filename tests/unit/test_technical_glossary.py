from mediaforge.features.dub.glossary import protected_spans, identity,mask_context,restore_context
from mediaforge.application.contracts.local_ai import TranslationRequest
from mediaforge.infrastructure.models.providers import LocalProviders
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('text,names', [
    ('Open Hugging Face and ComfyUI.', ['Hugging Face','ComfyUI']),
    ('Use the Unpremult and Premult nodes in Nuke.', ['Unpremult','Premult','Nuke']),
    ('Select the Merge node, then Merge again.', ['Merge','Merge']),
    ('Merge the files. Do not nuke the folder.', []),
    ('Merge the files in Nuke.', ['Nuke']),
    ('Nuke the files.', []),
    ('Use Griptape, not Griptapes or preComfyUI.', ['Griptape']),
    ('Press Ctrl+Shift+S in ComfyUI.', ['Ctrl+Shift+S','ComfyUI']),
    ('Open "D:\\Курс VFX\\file.nk" in Nuke.', ['"D:\\Курс VFX\\file.nk"','Nuke']),
])
def test_recognized_identifiers_only(text,names):
    spans=protected_spans(text)
    assert [x.text for x in spans]==names
    assert all(text[x.start:x.end]==x.text for x in spans)
    assert all(a.end<=b.start for a,b in zip(spans,spans[1:]))


def test_glossary_keeps_original_argos_and_contiguous_effective_character_lineage():
    provider=object.__new__(LocalProviders)
    def selected(key,action,payload,control,**options):
        text=payload['text']
        result={'Open Hugging Face.':'Откройте Обнимающее лицо.',
                'Open MFTERM000X.':'Откройте MFTERM000X.'}[text]
        return dict(spans=[dict(start=0,end=len(text),source=text,russian=result)]),SimpleNamespace(revision='fixture')
    provider._selected=selected
    result=provider.translate(TranslationRequest('Open Hugging Face.'),None)
    assert result.text=='Откройте Hugging Face.'
    assert result.reference_spans[0].russian=='Откройте Обнимающее лицо.'
    assert ''.join(x.source for x in result.spans)=='Open Hugging Face.'
    assert result.spans[0].start==0 and result.spans[-1].end==len('Open Hugging Face.')
    assert result.glossary_policy==identity()['policy']


def test_bounded_glossary_refuses_unbounded_input():
    with pytest.raises(ValueError):protected_spans('Hugging Face '*65)
    with pytest.raises(ValueError):protected_spans('a'*4001)


def test_correct_native_sentence_is_kept_without_fragment_translation():
    provider=object.__new__(LocalProviders);calls=[]
    text='Press Ctrl+Shift+S in ComfyUI.'
    def selected(key,action,payload,control,**options):
        calls.append(payload['text'])
        return dict(spans=[dict(start=0,end=len(text),source=text,russian='Нажмите Ctrl+Shift+S в ComfyUI.')]),SimpleNamespace(revision='fixture')
    provider._selected=selected
    result=provider.translate(TranslationRequest(text),None)
    assert result.text=='Нажмите Ctrl+Shift+S в ComfyUI.' and calls==[text]
    assert result.spans==result.reference_spans


@pytest.mark.parametrize('translated',[
    'MFTERM001X затем MFTERM000X 24 не',
    'MFTERM000X MFTERM000X MFTERM001X 24 не',
    'MFTERM000X MFTERM001X 25 не',
    'MFTERM000X MFTERM001X 24',
    'изменённый маркер 24 не',
])
def test_mask_refuses_missing_duplicate_reordered_numbers_or_negations(translated):
    source='Do not open Nuke and ComfyUI 24.'
    _,markers=mask_context(source,protected_spans(source))
    with pytest.raises(ValueError):restore_context(translated,markers,source,'Не открывайте приложения 24.')
