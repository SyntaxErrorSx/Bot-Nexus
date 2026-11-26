import unittest
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock
import requests
import nexus_supabase as sb

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.multiple(sb,SUPABASE_URL='https://example.supabase.co',SUPABASE_SECRET_KEY='sb_secret_TEST_ONLY',SUPABASE_PUBLISHABLE_KEY='')
        self.env.start(); self.addCleanup(self.env.stop)
        self.client=sb.RestClient()
    def response(self,status=200,data=None):
        r=Mock(status_code=status,content=b'[]',headers={}); r.json.return_value=[] if data is None else data; return r
    @patch('nexus_supabase.time.sleep')
    @patch('nexus_supabase.requests.request')
    def test_retry_upsert(self,req,sleep):
        req.side_effect=[requests.exceptions.ConnectTimeout(),self.response(503),self.response()]
        self.client.set_guild_data('nexusmusic_updates',123,{'channel':456})
        self.assertEqual(req.call_count,3)
        for c in req.call_args_list:
            self.assertEqual(c.kwargs['params']['on_conflict'],'guild_id')
            self.assertEqual(c.kwargs['json'],{'guild_id':'123','data':{'channel':456}})
            self.assertNotIn('Authorization',c.kwargs['headers'])
    @patch('nexus_supabase.time.sleep')
    @patch('nexus_supabase.requests.request')
    def test_dns_safe(self,req,sleep):
        req.side_effect=requests.exceptions.ConnectionError(socket.gaierror(-2,'secret should not appear'))
        with self.assertRaisesRegex(RuntimeError,'DNS:') as e: self.client.get_guild_data('nexusmusic_updates',1)
        self.assertNotIn('secret should',str(e.exception)); self.assertEqual(req.call_count,3)
    @patch('nexus_supabase.requests.request')
    def test_tls_no_retry(self,req):
        req.side_effect=requests.exceptions.SSLError('secret')
        with self.assertRaisesRegex(RuntimeError,'TLS'): self.client.get_guild_data('nexusmusic_updates',1)
        self.assertEqual(req.call_count,1)
    @patch('nexus_supabase.requests.request')
    def test_permission_no_retry(self,req):
        req.return_value=self.response(403,{'code':'42501'})
        with self.assertRaisesRegex(RuntimeError,'403'): self.client.set_guild_data('nexusmusic_updates',1,{})
        self.assertEqual(req.call_count,1)
    @patch('nexus_supabase.requests.request')
    def test_other_table_no_retry(self,req):
        req.side_effect=requests.exceptions.ReadTimeout()
        with self.assertRaisesRegex(RuntimeError,'respuesta HTTP'): self.client.get_guild_data('config',1)
        self.assertEqual(req.call_count,1)
    @patch('nexus_supabase.requests.request')
    def test_plain_insert_no_retry(self,req):
        req.side_effect=requests.exceptions.ReadTimeout()
        with self.assertRaises(RuntimeError): self.client.table('nexusmusic_updates').insert({'guild_id':'1'}).execute()
        self.assertEqual(req.call_count,1)

class StoreTests(unittest.TestCase):
    def test_local_opt_in(self):
        import nexus_updates as u
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ',{'NEXUS_UPDATES_LOCAL_ONLY':'1'}),patch.object(u,'LOCAL',Path(d)/'updates.json'):
            client=Mock(); s=u.Store(client); s.save(1,{'channel':2}); self.assertEqual(s.load(1),{'channel':2}); self.assertFalse(client.mock_calls)
    def test_cloud_failure_not_silent(self):
        import nexus_updates as u
        with patch.dict('os.environ',{'NEXUS_UPDATES_LOCAL_ONLY':'0'}):
            client=Mock(); client.get_guild_data.side_effect=RuntimeError('Supabase: DNS')
            with self.assertRaisesRegex(RuntimeError,'DNS'): u.Store(client).load(1)
    def test_incomplete_config(self):
        import nexus_updates as u
        with patch.dict('os.environ',{'NEXUS_UPDATES_LOCAL_ONLY':'0','SUPABASE_URL':'https://example.supabase.co','SUPABASE_SECRET_KEY':''}):
            with self.assertRaisesRegex(RuntimeError,'configuración incompleta'): u.Store(None).load(1)
    def test_error_not_collapsed(self):
        import nexus_updates as u
        self.assertIn('DNS',u.storage_error(RuntimeError('Supabase: DNS: no se pudo resolver')))
