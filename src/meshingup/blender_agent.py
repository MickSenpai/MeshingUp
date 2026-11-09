"""Structured local agent bridge. No arbitrary Python from requests."""
import bpy, json, sys, math
from pathlib import Path
from mathutils import Vector
source, request_file, output_dir = sys.argv[sys.argv.index('--') + 1:]
request = json.loads(Path(request_file).read_text())
root = Path(output_dir)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=source)
meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
rigs = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
report = {'meshes': [], 'skeletons': [], 'evidence': []}
for obj in meshes:
    report['meshes'].append({'name': obj.name, 'vertices': len(obj.data.vertices),
        'faces': len(obj.data.polygons), 'unweighted_vertices': sum(not v.groups for v in obj.data.vertices),
        'groups': [g.name for g in obj.vertex_groups]})
for rig in rigs:
    report['skeletons'].append({'name': rig.name, 'bones': [dict(name=b.name, parent=b.parent.name if b.parent else None,
        head=list(b.head_local), tail=list(b.tail_local)) for b in rig.data.bones]})
if request['operation'] == 'edit':
    for change in request['changes']:
        obj = next((o for o in meshes if o.name == change['mesh']), None)
        if obj is None: raise ValueError('Unknown mesh')
        indices = change['vertices']
        if any(type(i) is not int or i < 0 or i >= len(obj.data.vertices) for i in indices): raise ValueError('Invalid vertex index')
        if change['type'] == 'weights':
            weights = change['weights']
            bound_rigs = [m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object]
            bone_names = {b.name for rig in bound_rigs for b in rig.data.bones}
            if not weights or len(weights)>4 or any(k not in bone_names or not math.isfinite(v) or v < 0 for k,v in weights.items()) or sum(weights.values()) <= 0:
                raise ValueError('Invalid weights')
            for group in obj.vertex_groups: group.remove(indices)
            total = sum(weights.values())
            for name, value in weights.items():
                group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
                group.add(indices, value/total, 'REPLACE')
        elif change['type'] == 'translate_vertices':
            delta = change['delta']
            if len(delta)!=3 or not all(math.isfinite(v) for v in delta): raise ValueError('Invalid delta')
            if obj.data.shape_keys: raise ValueError('Vertex editing with shape keys is unsupported')
            for index in indices: obj.data.vertices[index].co += Vector(delta)
        else: raise ValueError('Unsupported edit type')
    bpy.ops.export_scene.gltf(filepath=str(root/'edited.glb'), export_format='GLB')
else:
    # Export indexed local coordinates and weights so agents can select exact vertices.
    geometry = {o.name: [dict(index=v.index, position=list(v.co), weights={o.vertex_groups[g.group].name:g.weight for g in v.groups}) for v in o.data.vertices] for o in meshes}
    (root/'geometry.json').write_text(json.dumps(geometry))
    points = [o.matrix_world @ v.co for o in meshes for v in o.data.vertices]
    if not points: raise ValueError('Empty model')
    lo = Vector([min(p[i] for p in points) for i in range(3)])
    hi = Vector([max(p[i] for p in points) for i in range(3)])
    center = (lo+hi)/2; size = max(hi-lo)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'; scene.cycles.samples = 8
    scene.render.resolution_x = 640; scene.render.resolution_y = 640; scene.render.resolution_percentage = 100
    scene.world.color = (.2,.2,.2)
    for rig in rigs:
        rig.animation_data_clear()
        for bone in rig.pose.bones:
            bone.matrix_basis.identity()
    bpy.ops.object.camera_add()
    camera = bpy.context.object; camera.data.type='ORTHO'; camera.data.ortho_scale=size*1.25; scene.camera=camera
    for offset in [(1,-2,2),(-1,-1,1),(0,2,2)]:
        bpy.ops.object.light_add(type='AREA', location=center+Vector(offset)*size)
        light=bpy.context.object; light.data.energy=400; light.data.size=size*2
        light.rotation_euler=(center-light.location).to_track_quat('-Z','Y').to_euler()
    for name, direction in [('front',(0,-1,0)),('back',(0,1,0)),('side',(1,0,0))]:
        camera.location=center+Vector(direction)*size*3
        camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
        scene.render.filepath=str(root/(name+'.png')); bpy.ops.render.render(write_still=True)
        report['evidence'].append(name+'.png')
(root/'report.json').write_text(json.dumps(report))
