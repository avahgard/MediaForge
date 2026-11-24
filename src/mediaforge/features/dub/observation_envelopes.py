"""Fixed ASR envelopes, never enlarged speech-fitting slots.

All clocks are source-relative 16 kHz samples. No recognition outcome participates
in planning; every original support and every complete word retains its identity.
"""
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from types import MappingProxyType

from mediaforge.features.dub.alignment import ceil, floor
from mediaforge.features.dub.speech import normalized, TimingError

POLICY = 'observation-envelopes-v2-greedy26-single52-context2-estimatedword-unique-acoustic-owner500'


def refuse(detail, **facts):
    error = TimingError('speech_boundary_uncertain', detail)
    error.word_facts = facts
    raise error


@dataclass(frozen=True)
class Envelope:
    first: int
    last: int
    start: Fraction
    end: Fraction
    begin: int
    stop: int

    def record(self):
        return dict(first=self.first, last=self.last, start=str(self.start),
                    end=str(self.end), window=[self.begin, self.stop])


def plan(supports, samples):
    if type(samples) is not int or not 0 < samples <= 3900*16000 or not 0 < len(supports) <= 5000:
        refuse('Invalid bounded observation-envelope source inventory.')
    previous = Fraction(0)
    for source in supports:
        if not previous <= source.start < source.end <= samples:
            refuse('Original observation supports are unordered or overlapping.')
        if source.end-source.start > 52*16000:
            refuse('Complete support exceeds two bounded observations; no split.')
        if (Fraction(source.native_start*16000, source.native_rate) != source.start
                or Fraction(source.native_end*16000, source.native_rate) != source.end):
            refuse('Original native/rational support identity differs.')
        previous = source.end
    groups = []
    first = 0
    while first < len(supports):
        last = first
        if supports[first].end-supports[first].start <= 26*16000:
            while last+1 < len(supports) and supports[last+1].end-supports[first].start <= 26*16000:
                last += 1
        groups.append((first, last, supports[first].start, supports[last].end))
        first = last+1
    result = []
    for index, (first, last, start, end) in enumerate(groups):
        begin = max(0, ceil(start)-32000)
        stop = min(samples, floor(end)+32000)
        if index:
            begin = max(begin, ceil((groups[index-1][3]+start)/2))
        if index+1 < len(groups):
            stop = min(stop, floor((end+groups[index+1][2])/2))
        if not 0 <= begin <= start < end <= stop <= samples or stop-begin > 56*16000:
            refuse('Complete envelope lacks bounded source-clock context.')
        result.append(Envelope(first, last, start, end, begin, stop))
    return tuple(result)


def fingerprint(envelopes, supports, samples):
    # Bind every original boundary, not just the grouped endpoints. This digest
    # is data identity inside the session HMAC, never standalone trust.
    record = dict(policy=POLICY, samples=samples, envelopes=[e.record() for e in envelopes],
                  supports=[[str(s.start), str(s.end), s.native_start, s.native_end, s.native_rate]
                            for s in supports])
    raw = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(raw) > 2*1024**2:
        refuse('Envelope plan exceeds its bounded canonical representation.')
    return dict(policy=POLICY, sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw), envelopes=len(envelopes), supports=len(supports))


def native_word_rows(rows, words):
    """Link each actual word to its actual native ASR row by ordered text.

    Parent confidence is retained at row granularity; no new score is invented.
    """
    parents = []
    cursor = 0
    for row_index, row in enumerate(rows):
        wanted = normalized(row['text'])
        if not wanted:
            refuse('Native ASR row has no complete normalized text.')
        start = cursor
        while cursor < len(words):
            value = normalized(' '.join(w['text'] for w in words[start:cursor+1]))
            if not value or not (wanted == value or wanted.startswith(value+' ')):
                refuse('Native word sequence does not partition its actual ASR rows.')
            parents.append(row_index)
            cursor += 1
            if value == wanted:
                break
        else:
            refuse('Native ASR row lacks complete word evidence.')
    if cursor != len(words):
        refuse('Native words escape their original ASR row inventory.')
    return tuple(parents)


@dataclass(frozen=True)
class SupportView:
    index: int
    word_indices: tuple
    words: tuple
    text: str


def assign(envelope, supports, words, text):
    if not 0 < len(words) <= 512:
        refuse('No bounded complete envelope words.')
    selected = [[] for _ in range(envelope.last-envelope.first+1)]
    previous = envelope.begin
    owner = 0
    for index, original in enumerate(words):
        a, b, probability = original['start'], original['end'], original['probability']
        if (type(a) is not int or type(b) is not int or not envelope.begin <= a <= b <= envelope.stop
                or a < previous or type(probability) not in (int, float) or not math.isfinite(probability)
                or not 0 <= probability <= 1 or not isinstance(original['text'], str)
                or not normalized(original['text'])):
            refuse('Invalid, duplicated or overlapping complete envelope word.', word_index=index)
        previous = b
        while owner < len(supports) and supports[owner].end <= a:
            owner += 1
        # Attention timestamps are estimates, not phoneme boundaries. Require one
        # acoustic owner across the ENTIRE source inventory. Preserve raw clocks
        # and text; independent VAD/waveform/confidence still qualify that owner's
        # unchanged source anchors. Never give an overlapping second slot away.
        candidates=[];candidate=owner
        while candidate<len(supports) and supports[candidate].start<=b:
            source=supports[candidate]
            if (source.start<=a<source.end if a==b else max(a,source.start)<min(b,source.end)):
                candidates.append(candidate)
            candidate+=1
        if (len(candidates)!=1 or not envelope.first<=candidates[0]<=envelope.last):
            refuse('Complete estimated word lacks one acoustic support owner.',
                   word_index=index, word_start=a, word_end=b,
                   support_index=min(owner, envelope.last),candidate_supports=candidates[:3])
        owner=candidates[0];source=supports[owner];middle=Fraction(a+b,2)
        if (not source.start<=middle<source.end
                or max(Fraction(0),source.start-a,b-source.end)>8000):
            refuse('Estimated word/source discrepancy exceeds independent ownership bounds.',
                   word_index=index,word_start=a,word_end=b,support_index=owner)
        selected[owner-envelope.first].append((index, MappingProxyType(dict(original))))
    result = []
    for offset, entries in enumerate(selected):
        index = envelope.first+offset
        if not entries:
            refuse('Original support lacks complete word evidence; no noise discard.', support_index=index)
        indices, items = zip(*entries)
        if not any(w['start']<w['end'] for w in items):
            refuse('Point-only word estimates lack a corroborated utterance extent.',support_index=index)
        result.append(SupportView(index, tuple(indices), tuple(items), ' '.join(w['text'].strip() for w in items)))
    if normalized(' '.join(v.text for v in result)) != normalized(text):
        refuse('Derived support views do not cover the complete native envelope text.')
    return tuple(result)
