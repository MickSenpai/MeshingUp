"""Run with Blender --background --factory-startup --python-exit-code 1 --python this_file.
Checks seam separation before/after weight repair on a synthetic skinned mesh.
"""
import ast, bpy, math
from mathutils.kdtree import KDTree
from pathlib import Path
source=str(Path(__file__).resolve().parents[1] / 'src/rigm/blender_autorig.py')
tree=ast.parse(open(source).read())
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in {'coincident_vertex_groups','synchronize_seam_weights','evaluate_pose_stress'}:
        exec(compile(ast.Module(body=[node],type_ignores=[]),source,'exec'))
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
height=2.0
mesh=bpy.data.meshes.new('seam'); mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0),(1,0,0),(0,1,0),(1,1,0)],[],[(0,1,2),(3,5,4)])
obj=bpy.data.objects.new('seam',mesh); bpy.context.collection.objects.link(obj); meshes=[obj]
arm=bpy.data.armatures.new('rig'); arm_obj=bpy.data.objects.new('rig',arm); bpy.context.collection.objects.link(arm_obj); bpy.context.view_layer.objects.active=arm_obj; arm_obj.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
a=arm.edit_bones.new('leftUpperArm'); a.head=(0,0,0); a.tail=(0,1,0)
b=arm.edit_bones.new('leftLowerArm'); b.head=(0,1,0); b.tail=(0,2,0); b.parent=a
bpy.ops.object.mode_set(mode='OBJECT')
obj.vertex_groups.new(name='leftUpperArm').add([0,1,2],1,'REPLACE'); obj.vertex_groups.new(name='leftLowerArm').add([3,4,5],1,'REPLACE')
modifier=obj.modifiers.new('skin','ARMATURE'); modifier.object=arm_obj
seam_groups={obj.name:coincident_vertex_groups(obj)}
assert len(seam_groups[obj.name])==2
before=evaluate_pose_stress()
assert max(p['seam_gap_growth_height_ratio'] for p in before['poses'])>0.01
repair=synchronize_seam_weights(); assert repair['changed_vertex_count']==4
after=evaluate_pose_stress()
assert max(p['seam_gap_growth_height_ratio'] for p in after['poses'])<1e-4
assert any(p['affected_edge_count'] for p in after['poses'])
assert all(abs(sum(g.weight for g in v.groups)-1)<1e-6 for v in obj.data.vertices)
print('RIGM_SEAM_CHECK_OK',repair)
