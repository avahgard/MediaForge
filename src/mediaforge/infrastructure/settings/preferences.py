"""One portable, bounded data-only preference file. Never a model inventory."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import io
import json
from uuid import uuid4

from mediaforge.infrastructure.settings.location import settings_file
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.owned import Ownership,ModelMutex

DEFAULTS={'dub_voice':'aiden','dub_audio_format':'mp3'}
VOICES={'aiden','dylan','eric','ono_anna','ryan','serena','sohee','uncle_fu','vivian'}


def validate(value):
    if (not isinstance(value,dict) or set(value)!={'schema','dub_voice','dub_audio_format'}
            or type(value['schema']) is not int or value['schema']!=1
            or not isinstance(value['dub_voice'],str) or not isinstance(value['dub_audio_format'],str)
            or value['dub_voice'] not in VOICES or value['dub_audio_format'] not in ('mp3','wav')):
        raise ValueError('Preferences are invalid or from an unsupported version. Original settings are preserved.')
    return {key:value[key] for key in DEFAULTS}


class PortablePreferences:
    defaults = DEFAULTS
    validate = staticmethod(validate)
    reservation = 'preferences'
    backup_prefix = '.settings-prior-'

    def __init__(self,paths):self.paths=paths;self.path=settings_file(paths)

    def load(self):
        if not self.path.exists():return dict(self.defaults)
        with ReadLocks() as guard:
            guard.parents(self.path.parent);guard.add(self.path)
            if self.path.stat().st_nlink!=1 or self.path.stat().st_size>8192:
                raise ValueError('Unsafe or oversized settings file; it is preserved.')
            return self.validate(json.loads(self.path.read_bytes()))

    def save(self,values):
        chosen=self.validate(dict(schema=1,**values))
        raw=json.dumps(dict(schema=1,**chosen),sort_keys=True,separators=(',',':')).encode()
        self.paths.prepare()
        # Data-only preferences use the reserved settings.json location and one
        # cross-instance reservation. No source path or secret is persisted.
        with ModelMutex(self.paths,self.reservation),Ownership() as owner:
            owner.parents.parents(self.path.parent)
            for index,path in enumerate(self.path.parent.glob(self.backup_prefix + '*')):
                if index>=1:raise ValueError('Prior interrupted preference backups require explicit disposition; no unlimited backup growth.')
            old=None
            if self.path.exists():
                old=owner.open(self.path,0x80010000,1,3)
                info=owner.info(old)
                size=(info.high<<32)|info.low
                if size>8192:raise ValueError('Oversized existing preferences; no overwrite.')
                owner.api.ReadFile.argtypes=[W.HANDLE,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.c_void_p]
                owner.api.ReadFile.restype=W.BOOL
                buffer=C.create_string_buffer(8193);count=W.DWORD()
                owner.require(owner.api.ReadFile(old,buffer,size,C.byref(count),None))
                if count.value!=size:raise ValueError('Incomplete preference read.')
                self.validate(json.loads(buffer.raw[:size]))
                # Rename this exact object, never replace an unowned pathname.
                # A crash can leave an unadopted bounded backup, no code trust.
                owner.rename(old,self.path.parent/(self.backup_prefix+uuid4().hex))
            try:
                owner.publish(self.path,io.BytesIO(raw),len(raw),hashlib.sha256(raw).hexdigest())
            except Exception:
                if old is not None and not self.path.exists():
                    owner.rename(old, self.path)
                raise
            if old is not None:owner.pending(old,True)


class ThemePreference(PortablePreferences):
    defaults = {'theme': 'System'}
    reservation = 'appearance-preference'
    backup_prefix = '.appearance-prior-'

    def __init__(self, paths):
        self.paths = paths
        self.path = settings_file(paths).with_name('appearance.json')

    @staticmethod
    def validate(value):
        if (not isinstance(value, dict) or set(value) != {'schema', 'theme'}
                or type(value['schema']) is not int or value['schema'] != 1
                or value['theme'] not in ('System', 'Dark', 'Light')):
            raise ValueError('Appearance preference is invalid. Original settings are preserved.')
        return {'theme': value['theme']}
