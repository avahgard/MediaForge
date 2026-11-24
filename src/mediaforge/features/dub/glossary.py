"""Bounded identifier protection over recognized text, never ASR correction."""
import hashlib
import re
from dataclasses import dataclass

POLICY = 'technical-identifiers-en-ru-context-v2'
NAMES = ('Hugging Face', 'Unpremult', 'Premult', 'Griptape', 'ComfyUI', 'Nuke', 'Merge')
CONTEXT = re.compile(r'\b(?:node|nodes|UI|menu|button|command|operator)\b', re.I)
PATTERN = re.compile(
    r'(?<![\w])(?:' + '|'.join(re.escape(x) for x in NAMES) + r')(?![\w])'
    r'|\b(?:Ctrl|Alt|Shift|Win)(?:\+(?:Ctrl|Alt|Shift|Win))*\+(?:[A-Z0-9]|F(?:[1-9]|1[0-2]))\b'
    r'|["`](?:[A-Za-z]:\\|/)[^\r\n"`<>|?*]{1,240}["`]'
    r'|(?<!\w)[A-Za-z]:\\[^\s"<>|?*,;]{1,240}')


@dataclass(frozen=True)
class Identifier:
    start: int
    end: int
    text: str


def protected_spans(text):
    if not isinstance(text, str) or len(text) > 4000 or '\x00' in text:
        raise ValueError('Glossary input exceeds the translation contract.')
    result = []
    for match in PATTERN.finditer(text):
        name = match.group()
        context = text[max(0, match.start()-60):min(len(text), match.end()+40)]
        if name == 'Merge':
            if re.match(r'\s+(?:the|these|those|files|channels|images|everything|them|it|all)\b',text[match.end():],re.I):continue
            if not CONTEXT.search(context):continue
        if name == 'Nuke' and re.match(r'\s+(?:the|them|it|everything)\b', text[match.end():], re.I):
            continue
        result.append(Identifier(match.start(), match.end(), name))
        if len(result) > 64:
            raise ValueError('Too many protected identifiers.')
    return tuple(result)


def identity():
    from pathlib import Path
    return dict(policy=POLICY, sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def preserved(text, identifiers):
    """Do not disturb a correct complete native translation."""
    cursor=0
    for item in identifiers:
        at=text.find(item.text,cursor)
        if at<0:return False
        cursor=at+len(item.text)
    for name in {item.text for item in identifiers}:
        if text.count(name)!=sum(item.text==name for item in identifiers):return False
    return True


def mask_context(text, identifiers):
    pieces=[];markers=[];cursor=0
    for index,item in enumerate(identifiers):
        marker=f'MFTERM{index:03d}X'
        if marker in text:raise ValueError('Source collides with the reserved terminology marker.')
        pieces.extend((text[cursor:item.start],marker));markers.append((marker,item.text));cursor=item.end
    pieces.append(text[cursor:])
    return ''.join(pieces),tuple(markers)


def restore_context(translated, markers, original, reference):
    cursor=0;pieces=[]
    for marker,name in markers:
        if translated.count(marker)!=1:raise ValueError('Argos did not preserve a terminology marker exactly once.')
        at=translated.find(marker,cursor)
        if at<0:raise ValueError('Argos reordered the protected instruction identifiers.')
        pieces.extend((translated[cursor:at],name));cursor=at+len(marker)
    pieces.append(translated[cursor:]);result=''.join(pieces)
    numbers=lambda s:re.findall(r'\d+(?:[.,]\d+)*',s)
    if numbers(result)!=numbers(original):raise ValueError('Protected translation changed ordered numbers.')
    negations=lambda s:len(re.findall(r'\b(?:не|нет)\b',s,re.I))
    if negations(result)!=negations(reference):raise ValueError('Protected translation changed native negation evidence.')
    if 'MFTERM' in result:raise ValueError('Unresolved terminology marker.')
    return result
