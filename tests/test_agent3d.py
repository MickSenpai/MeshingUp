import unittest
from meshingup.agent3d import contract
from meshingup.project import ProjectError
from pathlib import Path
import tempfile
from unittest.mock import patch
from meshingup.agent3d import operate
class AgentTests(unittest.TestCase):
    def test_contract_exposes_bounded_edits(self):
        self.assertEqual(contract()['edit_schema']['changes'][0]['type'], 'weights')
    def test_stale_hash_rejected_before_blender(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'model.glb').write_bytes(b'model')
            import hashlib
            sha=hashlib.sha256(b'model').hexdigest()
            (root/'project').write_text('{}')
            with patch('meshingup.agent3d.load_project',return_value={'avatar3d':{'path':'model.glb','sha256':sha}}):
                with self.assertRaisesRegex(ProjectError,'source_sha256'):
                    operate(root/'project','edit',{'source_sha256':'stale'})

class RequestValidationTests(unittest.TestCase):
    def test_rejects_malformed_edits(self):
        from meshingup.agent3d import validate_changes
        base={'type':'weights','mesh':'body','vertices':[0],'weights':{'head':1}}
        invalid=[None,[],[None],[{}], [dict(base,vertices=[True])], [dict(base,vertices=[])],
                 [dict(base,weights={'head':float('nan')})], [dict(base,weights={'head':True})],
                 [dict(base,weights={'head':0})], [dict(base,weights={'head':'1'})],
                 [dict(base,type='execute')], [dict(base,type='translate_vertices',delta=[0,1,float('inf')])]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ProjectError): validate_changes(value)
    def test_accepts_bounded_operations(self):
        from meshingup.agent3d import validate_changes
        validate_changes([{'type':'weights','mesh':'body','vertices':[0,1],'weights':{'head':2,'neck':1}},
                          {'type':'translate_vertices','mesh':'body','vertices':[1],'delta':[0,0,.01]}])
