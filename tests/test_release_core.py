import unittest
from nexus_release_core import apk_url,manifest,releases,REPO,should_announce

BASE=f'https://github.com/{REPO}/releases/download/v0.6.69/NexusMusic.apk'
SHA='7b48762d0c36f8814fd2153aee8c72d117f2db2539b2c22209b04aad50a1c9e0'
def release(**kw):
    r={'id':1,'tag_name':'v0.6.69','published_at':'2026-11-26T20:00:00Z','draft':False,'prerelease':False,'assets':[{'id':5,'name':'NexusMusic.apk','browser_download_url':BASE,'size':1000000,'state':'uploaded'}]}
    r.update(kw); return r
class TestFeed(unittest.TestCase):
    def test_manifest(self): self.assertEqual(manifest({'version':'0.6.69','apkUrl':BASE,'sha256':SHA})['sha256'],SHA)
    def test_bad_hash(self):
        with self.assertRaises(ValueError): manifest({'version':'0.6.69','apkUrl':BASE,'sha256':'bad'})
    def test_bad_url(self):
        for s in [BASE.replace('github.com','evil.com'), BASE+'?x=1',BASE.replace('https://','http://'),BASE.replace('NexusMusic.apk','other.apk'),BASE.replace('github.com','github.com@evil.com')]: self.assertIsNone(apk_url(s))
    def test_valid_url(self): self.assertEqual(apk_url(BASE),BASE)
    def test_drafts(self): self.assertEqual(releases([release(draft=True)]),[])
    def test_missing_apk(self): self.assertEqual(releases([release(assets=[])]),[])
    def test_release_key(self): self.assertEqual(releases([release()])[0]['key'],'1:5')
    def test_pre(self): self.assertTrue(releases([release(prerelease=True)])[0]['pre'])
    def test_order(self): self.assertEqual(releases([release(),release(id=2,published_at='2026-11-27T01:00:00Z')])[0]['key'],'2:5')
    def test_no_publish(self): self.assertEqual(releases([release(published_at=None)]),[])
    def test_wrong_asset(self): self.assertEqual(releases([release(assets=[{'name':'other.apk','browser_download_url':BASE}])]),[])
    def test_dedup(self):
        r=releases([release()])[0]
        self.assertFalse(should_announce({'channel':1,'last':r['key']},r))
        self.assertTrue(should_announce({'channel':1,'last':'other'},r))
    def test_rollback(self):
        r=releases([release()])[0]
        self.assertFalse(should_announce({'channel':1,'last_published':'2026-12-01T00:00:00Z'},r))
    def test_disabled(self): self.assertFalse(should_announce({},releases([release()])[0]))
    def test_prerelease_no_notice(self): self.assertFalse(should_announce({'channel':1},releases([release(prerelease=True)])[0]))
if __name__=='__main__': unittest.main()
