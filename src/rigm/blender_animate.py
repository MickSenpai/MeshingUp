"""Create a named, editable GLB animation from structured bone keyframes."""

import bpy
import json
import math
import sys


def fail(message):
    print("RIGM_ANIMATION_ERROR: " + message, file=sys.stderr)
    raise RuntimeError(message)


args = sys.argv[sys.argv.index("--") + 1:]
source_path, output_path, report_path, request_path = args
request = json.load(open(request_path, encoding="utf-8"))
clip_name = request["name"]
duration = int(request["duration"])
fps = int(request["fps"])
keyframes = request["keyframes"]

try:
    bpy.ops.import_scene.gltf(filepath=source_path)
except Exception as exc:
    fail("No pude importar el GLB riggeado: " + str(exc))

armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
if len(armatures) != 1:
    fail(f"Se esperaba un armature, encontré {len(armatures)}.")
armature = armatures[0]
known_bones = {bone.name for bone in armature.pose.bones}
unknown = sorted({key["bone"] for key in keyframes} - known_bones)
if unknown:
    fail("El GLB no contiene estos huesos: " + ", ".join(unknown))

scene = bpy.context.scene
scene.render.fps = fps
scene.frame_start = 0
scene.frame_end = duration
animation_data = armature.animation_data_create()
old_action = animation_data.action
muted_tracks = [(track, track.mute) for track in animation_data.nla_tracks]
for track, _mute in muted_tracks:
    track.mute = True

action = bpy.data.actions.new(clip_name)
if action.name != clip_name:
    fail(f"Ya existe una acción Blender llamada {action.name}; elige otro nombre para el clip.")
animation_data.action = action
for pose_bone in armature.pose.bones:
    pose_bone.rotation_mode = "XYZ"

for key in keyframes:
    frame = int(key["frame"])
    scene.frame_set(frame)
    pose_bone = armature.pose.bones[key["bone"]]
    pose_bone.rotation_euler = tuple(math.radians(float(value)) for value in key["rotation"])
    pose_bone.keyframe_insert(data_path="rotation_euler", frame=frame, group=pose_bone.name)
for curve in getattr(action, "fcurves", []):
    for point in curve.keyframe_points:
        point.interpolation = "BEZIER"

# Restore existing animation tracks and make the new action active for export.
animation_data.action = action
for track, was_muted in muted_tracks:
    track.mute = was_muted
bpy.context.view_layer.objects.active = armature
armature.select_set(True)
bpy.ops.object.select_all(action="DESELECT")
armature.select_set(True)
try:
    result = bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format="GLB",
        export_skins=True,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_frame_range=True,
    )
except Exception as exc:
    fail("Falló la exportación glTF de Blender: " + str(exc))
if "FINISHED" not in result:
    fail("El exportador glTF de Blender no terminó correctamente.")

report = {
    "animation": clip_name,
    "duration_frames": duration,
    "fps": fps,
    "keyframe_count": len(keyframes),
    "animated_bones": sorted({key["bone"] for key in keyframes}),
    "interpolation": "BEZIER",
}
with open(report_path, "w", encoding="utf-8") as report_file:
    json.dump(report, report_file, ensure_ascii=False, indent=2)
print("RIGM_ANIMATION_COMPLETE: " + json.dumps(report, ensure_ascii=False))
