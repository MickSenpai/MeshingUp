from pathlib import Path
import tempfile,json
from meshingup.avatar3d import create_avatar_project, read_glb_document
from meshingup.agent3d import operate,digest
from meshingup.project import load_project,update_workflow,ProjectError
root=Path(tempfile.mkdtemp(prefix='rigm-agent-integration-'))
p=root/'test.meshingup'
import argparse
parser=argparse.ArgumentParser(description='Integration test on a temporary copy; requires Blender and a GLB with two clips.')
parser.add_argument('source', type=Path)
source=parser.parse_args().source.resolve()
original=digest(source)
create_avatar_project(source,p)
a=operate(p,'evaluate');assert a['visual_review']=='pending'
e=operate(p,'evidence');assert len(e['evidence'])==3
review={'source_sha256':e['source_sha256'],'evidence_id':e['id'],'reviewer':'integration-test (not visual AI)','verdict':'needs_changes','findings':['Synthetic test of recording; no visual approval.']}
operate(p,'review',review)
file=root/e['files'][0]['path'];saved=file.read_bytes();file.write_bytes(saved+b' ')
try: operate(p,'review',review);raise AssertionError('Tampered evidence accepted')
except ProjectError: pass
file.write_bytes(saved)
geometry=json.loads((root/e['geometry']).read_text());mesh=next(iter(geometry));v=geometry[mesh][0]
edit={'source_sha256':e['source_sha256'],'changes':[{'type':'weights','mesh':mesh,'vertices':[0],'weights':v['weights']},{'type':'translate_vertices','mesh':mesh,'vertices':[0],'delta':[0,0,.0001]}]}
before=load_project(p)['avatar3d'];oldfile=root/before['path']
operate(p,'edit',edit)
d=load_project(p);assert d['status']=='needs_review' and d['avatar3d']['agent_edited']
assert 'review' not in d['agent3d'] and 'evidence' not in d['agent3d']
assert oldfile.exists() and digest(oldfile)==before['sha256']
assert len(read_glb_document(root/d['avatar3d']['path']).get('animations',[]))==2
try: update_workflow(p,'ready','done');raise AssertionError('Edited model marked ready')
except ProjectError: pass
try: operate(p,'review',review);raise AssertionError('Stale review accepted')
except ProjectError: pass
assert digest(source)==original
print(json.dumps({'passed':True,'directory':str(root),'checks':['evaluate','render 3 views','review recording','tampering rejection','weights and geometry edits','clips preserved','original preserved','stale review rejection','ready blocked']}))
