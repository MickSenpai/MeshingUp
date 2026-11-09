import unittest
from unittest.mock import patch
from meshingup.cli import main
from meshingup.avatar3d import _repair_lineage
class CreationTests(unittest.TestCase):
    def test_unwritable_library_does_not_hide_created_project(self):
        with patch('sys.argv',['meshingupctl','create','image.png','project.meshingup']), patch('meshingup.cli.create_project',return_value='project.meshingup'), patch('meshingup.cli.ProjectLibrary') as library, patch('meshingup.cli.load_project',return_value={}), patch('meshingup.cli._emit') as emit:
            library.return_value.add.side_effect=OSError('read only')
            main()
            result=emit.call_args.args[0]
            self.assertEqual(result['created'],'project.meshingup')
            self.assertTrue(result['warnings'])
    def test_generation_provenance_survives_rigging(self):
        info={'generation':{'source_sha256':'abc','provider':'TRELLIS'}}
        self.assertEqual(_repair_lineage(info),info)
