import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from PIL import Image
from meshingup.project import create_project, load_project, ProjectError
from meshingup.generation3d import generate_avatar

class GenerationTests(unittest.TestCase):
    def test_consent_required_before_access(self):
        with self.assertRaisesRegex(ProjectError, 'allow-upload'):
            generate_avatar('/missing/project')

    def test_generation_preserves_reference_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / 'reference.png'
            Image.new('RGB', (32, 32)).save(image)
            project = create_project(image, root / 'test.meshingup')
            original = image.read_bytes()
            # Use a real validated repository fixture, without contacting a provider.
            fixture = Path(__file__).resolve().parents[1] / 'work/meido_3d/assets/avatars/meido_3d-7e777c2e12.glb'
            if not fixture.exists():
                self.skipTest('Local GLB fixture unavailable')
            class Client:
                def __init__(self, *args, **kwargs): pass
                def predict(self, **kwargs):
                    if 'is_multiimage' in kwargs:
                        raise TypeError('is_multiimage is internal Gradio state, not an API parameter')
                    if kwargs['api_name'] == '/preprocess_image': return str(image)
                    return [None, str(fixture)]
            module = types.SimpleNamespace(Client=Client, handle_file=lambda x: x)
            with patch.dict(sys.modules, {'gradio_client': module}):
                result = generate_avatar(project, allow_upload=True)
            data = load_project(project)
            self.assertEqual(data['status'], 'needs_review')
            self.assertTrue(data['avatar3d']['generation']['inferred_surfaces'])
            self.assertEqual(image.read_bytes(), original)
            self.assertTrue((root / result['avatar']['path']).is_file())
            with self.assertRaisesRegex(ProjectError, 'ya tiene un avatar'):
                generate_avatar(project, allow_upload=True)

if __name__ == '__main__': unittest.main()
