"""Microsoft catalog-member trust, cache-only; no authority without policy checks.

Called by the trusted application host-driver verifier. No catalog
installation, trust-store update, online retry, or revocation waiver exists.
"""
import ctypes as C
from ctypes import wintypes as W
from mediaforge.infrastructure.models.driver_discovery import Guid,guid,system_directory,Refusal
class CatalogInfo(C.Structure):
    _fields_=[('size',W.DWORD),('version',W.DWORD),('catalog_path',W.LPCWSTR),('member_tag',W.LPCWSTR),('member_path',W.LPCWSTR),('member_file',W.HANDLE),('hash',C.POINTER(C.c_ubyte)),('hash_size',W.DWORD),('catalog_context',C.c_void_p),('admin',W.HANDLE)]
class TrustData(C.Structure):
    _fields_=[('size',W.DWORD),('policy_data',C.c_void_p),('sip_data',C.c_void_p),('ui',W.DWORD),('revocation',W.DWORD),('choice',W.DWORD),('subject',C.c_void_p),('state_action',W.DWORD),('state',W.HANDLE),('url',W.LPWSTR),('flags',W.DWORD),('ui_context',W.DWORD),('signature_settings',C.c_void_p)]
assert C.sizeof(CatalogInfo)==72 and C.sizeof(TrustData)==88
assert CatalogInfo.admin.offset==64 and TrustData.state.offset==56 and TrustData.flags.offset==72
class ProviderSigner(C.Structure):
    _fields_=[('size',W.DWORD),('verify_time',W.FILETIME),('count',W.DWORD),('certs',C.c_void_p),('kind',W.DWORD),('signer_info',C.c_void_p),('error',W.DWORD),('counter_count',W.DWORD),('counters',C.c_void_p),('chain',C.c_void_p)]
class ProviderCert(C.Structure):
    _fields_=[('size',W.DWORD),('context',C.c_void_p),('commercial',W.BOOL),('trusted_root',W.BOOL),('self_signed',W.BOOL),('test_cert',W.BOOL),('revoked_reason',W.DWORD),('confidence',W.DWORD),('error',W.DWORD),('trust_list',C.c_void_p),('trust_list_signer',W.BOOL),('ctl',C.c_void_p),('ctl_error',W.DWORD),('cyclic',W.BOOL),('chain_element',C.c_void_p)]
class CertContext(C.Structure):_fields_=[('encoding',W.DWORD),('encoded',C.c_void_p),('length',W.DWORD),('info',C.c_void_p),('store',W.HANDLE)]
assert C.sizeof(ProviderSigner)==64 and ProviderSigner.certs.offset==16 and C.sizeof(ProviderCert)==88 and C.sizeof(CertContext)==40

class CatalogTrust:
    def __init__(self):
        self.api=C.WinDLL(str(system_directory()/'wintrust.dll'),use_last_error=True,winmode=0x800);self.admin=W.HANDLE()
        self.api.CryptCATAdminAcquireContext2.argtypes=[C.POINTER(W.HANDLE),C.POINTER(Guid),W.LPCWSTR,C.c_void_p,W.DWORD];self.api.CryptCATAdminAcquireContext2.restype=W.BOOL
        self.api.CryptCATAdminCalcHashFromFileHandle2.argtypes=[W.HANDLE,W.HANDLE,C.POINTER(W.DWORD),C.c_void_p,W.DWORD];self.api.CryptCATAdminCalcHashFromFileHandle2.restype=W.BOOL
        self.api.CryptCATAdminReleaseContext.argtypes=[W.HANDLE,W.DWORD];self.api.CryptCATAdminReleaseContext.restype=W.BOOL
        self.api.WinVerifyTrust.argtypes=[W.HWND,C.POINTER(Guid),C.POINTER(TrustData)];self.api.WinVerifyTrust.restype=C.c_long
        self.api.WTHelperProvDataFromStateData.argtypes=[W.HANDLE];self.api.WTHelperProvDataFromStateData.restype=C.c_void_p
        self.api.WTHelperGetProvSignerFromChain.argtypes=[C.c_void_p,W.DWORD,W.BOOL,W.DWORD];self.api.WTHelperGetProvSignerFromChain.restype=C.POINTER(ProviderSigner)
        self.cert=C.WinDLL(str(system_directory()/'crypt32.dll'),use_last_error=True,winmode=0x800)
        self.cert.CertGetNameStringW.argtypes=[C.c_void_p,W.DWORD,W.DWORD,C.c_void_p,W.LPWSTR,W.DWORD];self.cert.CertGetNameStringW.restype=W.DWORD
        subsystem=guid('f750e6c3-38ee-11d1-85e5-00c04fc295ee')
        if not self.api.CryptCATAdminAcquireContext2(C.byref(self.admin),C.byref(subsystem),'SHA256',None,0):raise Refusal('catalog_api_unavailable','AcquireContext2 SHA256 WinError '+str(C.get_last_error()))
    def member(self,api,path,handle,catalog):
        if not api.SetFilePointerEx(handle,0,None,0):raise Refusal('held_read_failed','Cannot rewind exact held catalog member')
        size=W.DWORD();C.set_last_error(0)
        first=self.api.CryptCATAdminCalcHashFromFileHandle2(self.admin,handle,C.byref(size),None,0)
        if not first and C.get_last_error() not in (0,122,234):raise Refusal('sip_hash_failed','Member digest size query failed '+str(C.get_last_error()))
        if size.value!=32:raise Refusal('sip_algorithm_unqualified','Expected SHA256 catalog member digest')
        buffer=(C.c_ubyte*32)()
        if not api.SetFilePointerEx(handle,0,None,0) or not self.api.CryptCATAdminCalcHashFromFileHandle2(self.admin,handle,C.byref(size),buffer,0):raise Refusal('sip_hash_failed','Held-file catalog hash failed '+str(C.get_last_error()))
        digest=bytes(buffer).hex();subject=CatalogInfo(C.sizeof(CatalogInfo),0,str(catalog),digest.upper(),str(path),handle,buffer,size.value,None,self.admin)
        # UI_NONE; whole-chain revocation policy excludes only the trusted root;
        # cache-only URL retrieval; weak MD2/MD4 signatures disabled. No retry
        # with WTD_REVOKE_NONE or online retrieval is implemented.
        policy=TrustData();policy.size=C.sizeof(policy);policy.ui=2;policy.revocation=1;policy.choice=2;policy.subject=C.cast(C.byref(subject),C.c_void_p);policy.state_action=1;policy.flags=0x1000|0x80|0x2000
        action=guid('00aac56b-cd44-11d0-8cc2-00c04fc295ee');record=dict(action='WINTRUST_ACTION_GENERIC_VERIFY_V2',action_guid='00aac56b-cd44-11d0-8cc2-00c04fc295ee',catalog=str(catalog),member=str(path),sip_algorithm='SHA256',sip_hash=digest,member_tag=digest.upper(),raw_hash_is_separate=True,ui_choice=2,revocation_checks=1,prov_flags=policy.flags,policy='cache-only URL retrieval; chain revocation excluding trusted root; no weak MD2/MD4; no interactive prompt')
        try:
            status=self.api.WinVerifyTrust(W.HWND(C.c_void_p(-1).value),C.byref(action),C.byref(policy));record['wintrust_status']=status&0xffffffff;record['wintrust_hex']=hex(status&0xffffffff);record['verified']=status==0
            if status==0:record['signer_chain']=self.signer_chain(policy.state)
        finally:
            if policy.state:
                policy.state_action=2;closed=self.api.WinVerifyTrust(W.HWND(C.c_void_p(-1).value),C.byref(action),C.byref(policy));record['state_close_status']=closed&0xffffffff
                if closed!=0:raise Refusal('lifecycle_failed','WinTrust state close failed',retained=(self,policy))
        return record
    def signer_chain(self,state):
        import hashlib
        provider=self.api.WTHelperProvDataFromStateData(state)
        if not provider:raise Refusal('chain_evidence_unavailable','WinTrust state has no provider evidence')
        pointer=self.api.WTHelperGetProvSignerFromChain(provider,0,False,0)
        if not pointer:raise Refusal('chain_evidence_unavailable','No primary catalog signer')
        signer=pointer.contents
        if signer.size!=64 or not 1<=signer.count<=16 or not signer.certs:raise Refusal('chain_evidence_unavailable','Unsupported provider-signer layout/count')
        rows=[]
        for i in range(signer.count):
            item=ProviderCert.from_address(signer.certs+i*C.sizeof(ProviderCert))
            if item.size!=88 or not item.context:raise Refusal('chain_evidence_unavailable','Unsupported provider-certificate layout')
            context=CertContext.from_address(item.context)
            if not 0<context.length<=128*1024 or not context.encoded:raise Refusal('chain_evidence_unavailable','Certificate bounds')
            names=[]
            for flags in (0,1):
                text=C.create_unicode_buffer(2048);n=self.cert.CertGetNameStringW(item.context,4,flags,None,text,len(text))
                if not 1<n<len(text):raise Refusal('chain_evidence_unavailable','Certificate name unavailable')
                names.append(text.value)
            rows.append(dict(subject=names[0],issuer=names[1],der_sha256=hashlib.sha256(C.string_at(context.encoded,context.length)).hexdigest(),der_sha1=hashlib.sha1(C.string_at(context.encoded,context.length)).hexdigest(),der_bytes=context.length,trusted_root=bool(item.trusted_root),self_signed=bool(item.self_signed),test_certificate=bool(item.test_cert),revoked_reason=item.revoked_reason,error=item.error,confidence=item.confidence))
        if signer.error or any(r['error'] or r['revoked_reason'] or r['test_certificate'] for r in rows) or not rows[-1]['trusted_root']:raise Refusal('chain_evidence_unavailable','Signer/chain status conflicts with trusted production chain')
        return dict(signer_error=signer.error,verification_filetime=(signer.verify_time.dwHighDateTime<<32)|signer.verify_time.dwLowDateTime,counter_signer_count=signer.counter_count,certificates=rows,revocation_freshness='WinTrust cache-only policy success at this observation; raw CRL/OCSP validity intervals not exported here')
    def close(self):
        if self.admin:
            if not self.api.CryptCATAdminReleaseContext(self.admin,0):raise Refusal('lifecycle_failed','Catalog admin context retained',retained=(self,))
            self.admin=W.HANDLE()
