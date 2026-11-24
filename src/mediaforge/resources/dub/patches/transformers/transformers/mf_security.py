"""Derived Transformers/Accelerate profile guards; stdlib-only, before side effects."""
class ProfileUnsupported(ValueError):pass
def unavailable(*args,**kwargs):raise ProfileUnsupported('Unsupported checkpoint/kernel/remote/training/save API in Qwen P0b profile.')
def config_guard(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key in {'auto_map','custom_generate','custom_pipelines','quantization_config','_auto_class'} or key.startswith('_') and ('attn_implementation' in key or 'experts_implementation' in key):unavailable()
            if key in {'attn_implementation','experts_implementation'} and item not in (None,'eager'):unavailable()
            config_guard(item)
    elif isinstance(value,(list,tuple)):
        for item in value:config_guard(item)
def generation_guard(custom_generate,kwargs):
    if custom_generate is not None or any(k in kwargs for k in ('custom_generate','trust_remote_code','assistant_model','token_healing','custom_generation')):unavailable()
def eager(value):
    if value not in (None,'eager'):unavailable()
    return 'eager'
