from pathlib import Path
import json
from .mf_contracts import ProfileError,SPEAKERS,REVISION,check_selectors
from .mf_native_files import NativeFiles,path
from .mf_constants import MODELS
def verify_members(folder,expected,owner):
    folder=path(folder);owner.parents(folder);seen=set();directories={p.as_posix() for name in expected for p in Path(name).parents if p!=Path('.')}
    for child in folder.rglob('*'):
        relative=child.relative_to(folder).as_posix()
        if child.lstat().st_file_attributes&0x400:raise ProfileError('unsafe_alias','Model reparse member.')
        if child.is_dir():
            if relative not in directories:raise ProfileError('incomplete','Unlisted model directory.')
            owner.add(child,True)
        else:
            if relative not in expected:raise ProfileError('incomplete','Unlisted model member.')
            row=expected[relative];h=owner.add(child);digest,_=owner.digest(h,row['size'])
            if digest!=row['sha256']:raise ProfileError('digest_mismatch','Model member failed authentication.')
            seen.add(relative)
    if seen!=set(expected):raise ProfileError('incomplete','Missing required model members.')
class Components:
    def __init__(self,root,talker,tokenizer):
        self.root=path(root);self.talker=path(talker);self.tokenizer=path(tokenizer);self.owner=NativeFiles();self.documents={}
        if self.talker!=self.root/'models'/'qwen3-tts-06b-customvoice'/REVISION or self.tokenizer!=self.root/'models'/'qwen3-tts-tokenizer-12hz'/REVISION:raise ProfileError('unsafe_path','Exact parent-selected component identities and immutable revision required.')
        try:
            # Phase1 authenticates BOTH complete components under one owner.
            # No configuration reads/parsing occur until that barrier succeeds.
            selected=[('talker',self.talker),('tokenizer',self.tokenizer)]
            for label,folder in selected:
                verify_members(folder,{m['name']:m for m in MODELS[label]},self.owner)
            # Phase2 parses only authenticated, still-leased documents.
            for label,folder in selected:
                for name in (m['name'] for m in MODELS[label]):
                    # Vocabulary entries are literal token data, not executable
                    # configuration selectors (e.g. the token "implementation").
                    # All members were authenticated above, including vocab.
                    if name in ('config.json','generation_config.json','tokenizer_config.json','preprocessor_config.json'):
                        data=(folder/name).read_bytes();value=json.loads(data);check_selectors(value);self.documents[label+'/'+name]=value
            config=self.documents['talker/config.json'];tokenizer_config=self.documents['tokenizer/config.json']
            if config['model_type']!='qwen3_tts' or config['tts_model_type']!='custom_voice' or config['tts_model_size']!='0b6' or config['tokenizer_type']!='qwen3_tts_tokenizer_12hz':raise ProfileError('unsupported_model','Unsupported model class.')
            if config['talker_config']['spk_id']!=SPEAKERS or config['talker_config']['codec_language_id']['russian']!=2069 or tokenizer_config['model_type']!='qwen3_tts_tokenizer_12hz' or tokenizer_config['output_sample_rate']!=24000:raise ProfileError('unsupported_model','Speaker/tokenizer identity differs.')
        except BaseException:
            self.owner.close();raise
    def close(self):self.owner.close()
