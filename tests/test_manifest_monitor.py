import unittest
from nexus_release_core import manifest_release, manifest_action, mark_manifest, REPO

def current(v='0.6.69', sha='a'*64):
    return manifest_release({'version':v,'apkUrl':f'https://github.com/{REPO}/releases/download/v{v}/NexusMusic.apk','sha256':sha})

class MonitorTests(unittest.TestCase):
    def test_baseline(self): self.assertEqual(manifest_action({'channel':1},current()),'baseline')
    def test_legacy_migration(self): self.assertEqual(manifest_action({'channel':1,'last':'123:456'},current()),'baseline')
    def test_explicit_notice(self): self.assertEqual(manifest_action({'channel':1,'announce_current':True},current()),'announce')
    def test_dedup(self): self.assertEqual(manifest_action(mark_manifest({'channel':1,'announce_current':True},current()),current()),'none')
    def test_new_release(self): self.assertEqual(manifest_action(mark_manifest({'channel':1},current()),current('0.6.70')),'announce')
    def test_rollback(self): self.assertEqual(manifest_action(mark_manifest({'channel':1},current()),current('0.6.68')),'none')
    def test_replaced_apk(self): self.assertEqual(manifest_action(mark_manifest({'channel':1},current()),current(sha='b'*64)),'announce')
    def test_disabled(self): self.assertEqual(manifest_action({'announce_current':True},current()),'none')
    def test_missing_feed(self): self.assertEqual(manifest_action({'channel':1},None),'none')
    def test_no_invented_metadata(self): self.assertIsNone(current()['size']); self.assertIsNone(current()['published'])
