"""Pure validation and pagination helpers for NexusMusic's official APK feeds."""
import re
from urllib.parse import urlsplit

REPO = 'santhiagocaro05-debug/Nexus-Update'
RELEASES_URL = f'https://api.github.com/repos/{REPO}/releases'
MANIFEST_URL = f'https://raw.githubusercontent.com/{REPO}/main/version.json'
REPO_URL = f'https://github.com/{REPO}/releases'
WEBS = [('NexusMusic','https://nexusmusicx.vercel.app'),('NexusForgex','https://nexusforgex.vercel.app')]

def apk_url(url):
    if not isinstance(url,str): return None
    p=urlsplit(url)
    if p.scheme!='https' or p.netloc!='github.com' or p.query or p.fragment: return None
    if not re.fullmatch(r'/'+re.escape(REPO)+r'/releases/download/[A-Za-z0-9._%+\-]+/NexusMusic\.apk',p.path): return None
    return url

def manifest(data):
    if not isinstance(data,dict): raise ValueError('Formato de version.json inválido')
    v=data.get('version'); sha=data.get('sha256'); url=apk_url(data.get('apkUrl'))
    if not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}',v) or not url or not isinstance(sha,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',sha):
        raise ValueError('version.json contiene campos inválidos')
    return {'version':v,'apkUrl':url,'sha256':sha.lower()}

def releases(data):
    if not isinstance(data,list): raise ValueError('Respuesta de GitHub inválida')
    out=[]
    for r in data:
        if not isinstance(r,dict) or r.get('draft') or not r.get('published_at'): continue
        for a in r.get('assets') or []:
            url=apk_url(a.get('browser_download_url'))
            if a.get('name')!='NexusMusic.apk' or not url or a.get('state','uploaded')!='uploaded': continue
            rid=r.get('id'); aid=a.get('id'); tag=r.get('tag_name')
            if type(rid)!=int or type(aid)!=int or not isinstance(tag,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,79}',tag): continue
            out.append({'key':f'{rid}:{aid}','version':tag,'url':url,'published':str(r['published_at']),'size':max(0,int(a.get('size') or 0)),'pre':bool(r.get('prerelease'))})
            break
    return sorted(out,key=lambda r:(r['published'],r['key']),reverse=True)


def should_announce(state, release):
    """Do not repeat a release or announce a rollback as a new publication."""
    return bool(state.get('channel') and release and not release.get('pre') and state.get('last')!=release['key'] and (not state.get('last_published') or release['published']>=state['last_published']))
