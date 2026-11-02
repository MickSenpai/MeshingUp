"""Blender background script used by RigM's supervised GLB auto-rig command.

Run by avatar3d.autorig_glb; this file intentionally has no RigM imports because
Blender ships its own Python runtime.
"""

import bpy
import bmesh
import json
import math
import sys
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree


def fail(message):
    print("RIGM_AUTORIG_ERROR: " + message, file=sys.stderr)
    raise RuntimeError(message)


args = sys.argv[sys.argv.index("--") + 1:]
source_path, output_path, report_path, forward_axis = args

try:
    bpy.ops.import_scene.gltf.get_rna_type()
    bpy.ops.export_scene.gltf.get_rna_type()
except Exception:
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
        bpy.ops.import_scene.gltf.get_rna_type()
        bpy.ops.export_scene.gltf.get_rna_type()
    except Exception as exc:
        fail("Activa la extensión glTF 2.0 de Blender para importar/exportar GLB: " + str(exc))

# Start from an empty scene. Importing/exporting the original remains non-destructive.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=source_path)
meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
if not meshes:
    fail("No se encontraron objetos mesh en el GLB.")
if any(obj.type == "ARMATURE" for obj in bpy.context.scene.objects):
    fail("El GLB ya tiene un armature; RigM no lo reemplazará automáticamente.")

# Normalize the chosen front to Blender's conventional -Y so the exported
# character faces the viewer camera consistently. This rotates the whole source
# scene around its horizontal center without changing relative mesh transforms.
input_forward_axis = forward_axis
front_vectors = {"-Y": Vector((0, -1, 0)), "+Y": Vector((0, 1, 0)), "-X": Vector((-1, 0, 0)), "+X": Vector((1, 0, 0))}
front = front_vectors[forward_axis]
target_front = front_vectors["-Y"]
angle = math.atan2(front.x * target_front.y - front.y * target_front.x, front.x * target_front.x + front.y * target_front.y)
initial_points = [obj.matrix_world @ vertex.co for obj in meshes for vertex in obj.data.vertices]
if not initial_points:
    fail("Las mallas no contienen vértices.")
origin = Vector((
    (min(point.x for point in initial_points) + max(point.x for point in initial_points)) * 0.5,
    (min(point.y for point in initial_points) + max(point.y for point in initial_points)) * 0.5,
    0,
))
del initial_points
rotation = Matrix.Rotation(angle, 4, "Z")
transform = Matrix.Translation(origin) @ rotation @ Matrix.Translation(-origin)
if abs(angle) > 1e-6:
    for obj in bpy.context.scene.objects:
        obj.matrix_world = transform @ obj.matrix_world
forward_axis = "-Y"

points = []
for obj in meshes:
    points.extend(obj.matrix_world @ vertex.co for vertex in obj.data.vertices)
if len(points) < 30:
    fail("La malla tiene muy pocos vértices para inferir proporciones humanoides.")

mins = [min(point[i] for point in points) for i in range(3)]
maxs = [max(point[i] for point in points) for i in range(3)]
height = maxs[2] - mins[2]
if height <= 1e-5:
    fail("El modelo no parece estar erguido en el eje Z después de importarlo.")
center = [(mins[i] + maxs[i]) * 0.5 for i in range(3)]
forward_index = {"-Y": 1, "+Y": 1, "-X": 0, "+X": 0}[forward_axis]
side_index = 1 - forward_index
forward_sign = -1 if forward_axis.startswith("-") else 1
# Character-left is up cross forward.
side_sign = (1 if forward_axis == "-Y" else -1 if forward_axis == "+Y" else -1 if forward_axis == "-X" else 1)


def point_at(side, forward, z):
    value = [center[0], center[1], z]
    value[side_index] = center[side_index] + side_sign * side
    value[forward_index] = center[forward_index] + forward_sign * forward
    return Vector(value)


def section(z_ratio):
    z = mins[2] + height * z_ratio
    band = height * 0.012
    values = sorted((point[side_index] - center[side_index]) * side_sign for point in points if abs(point.z - z) <= band)
    if len(values) < 8:
        values = sorted((point[side_index] - center[side_index]) * side_sign for point in points if abs(point.z - z) <= height * 0.035)
    if not values:
        return 0.0, 0.0
    return values[int((len(values) - 1) * 0.1)], values[int((len(values) - 1) * 0.9)]


# Detect a large stylized head from the vertical width profile. The adult
# fallback remains available for ordinary proportions and non-humanoids are
# reported with low confidence below.
profile_bins = 64
profile_samples = [[] for _ in range(profile_bins)]
for point in points:
    ratio = (point.z - mins[2]) / height
    if 0.0 <= ratio <= 1.0:
        profile_samples[min(profile_bins - 1, int(ratio * profile_bins))].append(
            (point[side_index] - center[side_index]) * side_sign
        )
profile = []
for index, values in enumerate(profile_samples):
    if len(values) < 8:
        profile.append({"ratio": (index + 0.5) / profile_bins, "width": 0.0})
        continue
    values.sort()
    quantile = lambda q: values[min(len(values) - 1, int((len(values) - 1) * q))]
    profile.append({"ratio": (index + 0.5) / profile_bins, "width": quantile(0.95) - quantile(0.05)})
head_profile = [item for item in profile if item["width"] > 0 and 0.78 <= item["ratio"] <= 0.99]
head_peak = max(head_profile, key=lambda item: item["width"], default=None)
neck_candidates = [
    item for item in profile
    if item["width"] > 0 and 0.48 <= item["ratio"] < (head_peak["ratio"] if head_peak else 0.9) - 0.04
]
local_necks = []
for item in neck_candidates:
    index = profile.index(item)
    above, below = profile[index + 1], profile[index - 1]
    if (
        above["width"] > 0 and below["width"] > 0
        and item["width"] <= above["width"] and item["width"] <= below["width"]
        and head_peak and head_peak["width"] > item["width"] * 1.32
        and head_peak["ratio"] - item["ratio"] > 0.07
    ):
        local_necks.append(item)
local_necks.sort(key=lambda item: item["ratio"], reverse=True)
neck_slice = local_necks[0] if local_necks else min(
    (item for item in neck_candidates if item["ratio"] >= max(0.48, (head_peak["ratio"] if head_peak else 0.9) - 0.30)),
    key=lambda item: item["width"], default=None,
)
chibi_head_detected = bool(
    head_peak and neck_slice and head_peak["width"] > neck_slice["width"] * 1.32
    and head_peak["ratio"] - neck_slice["ratio"] > 0.07
)
neck_ratio = neck_slice["ratio"] if chibi_head_detected else 0.85
shoulder_ratio = max(0.48, min(0.82, neck_ratio - 0.035)) if chibi_head_detected else 0.77
hip_ratio = max(0.27, shoulder_ratio * 0.64) if chibi_head_detected else 0.51
spine_ratio = hip_ratio + (shoulder_ratio - hip_ratio) * 0.45
chest_ratio = hip_ratio + (shoulder_ratio - hip_ratio) * 0.82
head_ratio = neck_ratio + (1.0 - neck_ratio) * 0.5 if chibi_head_detected else 0.94

# Estimate torso width from sections between pelvis and shoulders; this keeps
# a large head from being mistaken for the torso of a chibi character.
torso_widths = []
for fraction in (0.25, 0.38, 0.50, 0.68, 0.82):
    ratio = hip_ratio + (shoulder_ratio - hip_ratio) * fraction
    lo, hi = section(ratio)
    if hi > lo:
        torso_widths.append(hi - lo)
torso_width = sorted(torso_widths)[len(torso_widths) // 2] if torso_widths else height * 0.2
torso_width = max(height * 0.12, min(height * 0.42, torso_width))
hip_z = mins[2] + height * hip_ratio
spine_z = mins[2] + height * spine_ratio
chest_z = mins[2] + height * chest_ratio
neck_z = mins[2] + height * neck_ratio
head_z = mins[2] + height * head_ratio
top_z = maxs[2] - height * 0.015
shoulder_z = mins[2] + height * shoulder_ratio
left_slice, right_slice = section(shoulder_ratio)
shoulder_half = max(torso_width * 0.56, min(height * 0.34, max(abs(left_slice), abs(right_slice)) * 0.55))


def body_depth_at(z):
    """Median front/back position of the body around a requested height."""
    band = height * 0.018
    values = [
        (point[forward_index] - center[forward_index]) * forward_sign
        for point in points
        if abs(point.z - z) <= band
        and abs((point[side_index] - center[side_index]) * side_sign) <= torso_width * 0.72
    ]
    if len(values) < 8:
        values = [
            (point[forward_index] - center[forward_index]) * forward_sign
            for point in points if abs(point.z - z) <= height * 0.04
        ]
    if not values:
        return 0.0
    values.sort()
    return values[(len(values) - 1) // 2]


def body_point(lateral, z, front_offset=0.0):
    return point_at(lateral, body_depth_at(z) + front_offset, z)

# Find each arm's outside silhouette in the shoulder band. Joints are placed
# along these measured extents, rather than simply scaling all axes by height.
arm_levels = [max(0.08, min(0.92, shoulder_ratio - 0.07 + index * 0.009)) for index in range(20)]
arm_extents = {}
for side_name, sign in (("left", 1), ("right", -1)):
    candidates = []
    for ratio in arm_levels:
        _lo, _hi = section(ratio)
        signed = _hi if sign > 0 else -_lo
        candidates.append((signed, ratio))
    extent, wrist_ratio = max(candidates)
    extent = max(shoulder_half * 1.45, min(height * 0.72, extent))
    arm_extents[side_name] = (extent, mins[2] + height * wrist_ratio)

hip_half = max(height * 0.055, min(torso_width * 0.36, height * 0.17))
knee_half = hip_half * 0.9
ankle_half = hip_half * 0.7
zmin = mins[2]
bones = []
hand_finger_layout_by_side = {}
arm_component_sizes = {}
hand_finger_track_counts = {}


def find_arm_component_points(side_direction):
    """Use a connected outboard mesh island as an optional arm landmark source."""
    candidates = []
    for obj in meshes:
        parent = list(range(len(obj.data.vertices)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        for edge in obj.data.edges:
            first, second = edge.vertices
            first_root, second_root = find(first), find(second)
            if first_root != second_root:
                parent[first_root] = second_root
        components = {}
        for vertex in obj.data.vertices:
            components.setdefault(find(vertex.index), []).append(obj.matrix_world @ vertex.co)
        for component in components.values():
            if len(component) < 80:
                continue
            lateral = [
                (point[side_index] - center[side_index]) * side_sign * side_direction
                for point in component
            ]
            # Rank islands by geometry actually present at shoulder height.
            # A hair island can be wide and lie beside the head, so whole-island
            # averages alone incorrectly classify it as an arm.
            arm_band = [
                (point, value) for point, value in zip(component, lateral)
                if shoulder_z - height * 0.12 <= point.z <= shoulder_z + height * 0.12
                and value >= shoulder_half * 0.62
            ]
            if len(arm_band) < 24:
                continue
            band_lateral = [value for _point, value in arm_band]
            band_span = max(band_lateral) - min(band_lateral)
            if band_span < height * 0.07:
                continue
            score = len(arm_band) * band_span
            candidates.append((score, component))
    return max(candidates, key=lambda item: item[0])[1] if candidates else []


def add_bone(name, head, tail, parent=None):
    if (tail - head).length < height * 0.008:
        tail = head + Vector((0, 0, height * 0.01))
    bones.append((name, head, tail, parent))


def infer_hand_finger_tracks(side_name, side_direction, component_points):
    """Estimate four finger centerlines from the detected arm/hand island."""
    if not component_points:
        return []
    arm_region = [
        point for point in component_points
        if abs(point.z - shoulder_z) <= height * 0.16
        and (point[side_index] - center[side_index]) * side_sign * side_direction >= shoulder_half * 0.62
    ]
    lateral_all = [
        (point[side_index] - center[side_index]) * side_sign * side_direction
        for point in arm_region
    ]
    if len(arm_region) < 24:
        hand_finger_track_counts[side_name] = {"candidate_vertices": len(arm_region), "tracks": 0}
        return []
    distal_floor = max(shoulder_half * 1.05, sorted(lateral_all)[int((len(lateral_all) - 1) * 0.68)])
    candidates = []
    for point in arm_region:
        lateral = (point[side_index] - center[side_index]) * side_sign * side_direction
        if lateral < distal_floor:
            continue
        depth = (point[forward_index] - center[forward_index]) * forward_sign
        candidates.append((point, lateral, depth))
    if len(candidates) < 80:
        hand_finger_track_counts[side_name] = {"candidate_vertices": len(candidates), "tracks": 0}
        return []

    def quantile(values, fraction):
        values = sorted(values)
        return values[min(len(values) - 1, max(0, int((len(values) - 1) * fraction)))]

    depths = [item[2] for item in candidates]
    depth_low, depth_high = quantile(depths, 0.05), quantile(depths, 0.95)
    band_width = max(height * 0.012, (depth_high - depth_low) * 0.075)
    tracks = []
    for fraction in (0.13, 0.37, 0.63, 0.87):
        center_depth = quantile(depths, fraction)
        band = [item for item in candidates if abs(item[2] - center_depth) <= band_width]
        if len(band) < 12:
            band = sorted(candidates, key=lambda item: abs(item[2] - center_depth))[:max(12, len(candidates) // 18)]
        lateral_values = [item[1] for item in band]
        tip_side = quantile(lateral_values, 0.96)
        base_side = quantile(lateral_values, 0.12)
        if tip_side - base_side < height * 0.004:
            tip_side = min(max(lateral_values), base_side + height * 0.01)
        base_band = [item for item in band if item[1] <= quantile(lateral_values, 0.30)]
        tip_band = [item for item in band if item[1] >= quantile(lateral_values, 0.75)]
        if not base_band:
            base_band = band
        if not tip_band:
            tip_band = band
        base_depth = quantile([item[2] for item in base_band], 0.5)
        tip_depth = quantile([item[2] for item in tip_band], 0.5)
        base_z = quantile([item[0].z for item in base_band], 0.5)
        tip_z = quantile([item[0].z for item in tip_band], 0.5)

        def position(progress):
            lateral = base_side + (tip_side - base_side) * progress
            depth = base_depth + (tip_depth - base_depth) * progress
            z = base_z + (tip_z - base_z) * progress
            return point_at(side_direction * lateral, depth, z)

        tracks.append((position(0.0), position(0.30), position(0.66), position(1.0)))
    hand_finger_track_counts[side_name] = {"candidate_vertices": len(candidates), "tracks": len(tracks)}
    return tracks if len(tracks) == 4 else []


pelvis = body_point(0, hip_z)
add_bone("hips", pelvis, body_point(0, spine_z), None)
add_bone("spine", body_point(0, spine_z), body_point(0, chest_z), "hips")
add_bone("chest", body_point(0, chest_z), body_point(0, neck_z), "spine")
add_bone("neck", body_point(0, neck_z), body_point(0, head_z), "chest")
add_bone("head", body_point(0, head_z), body_point(0, top_z), "neck")

for side_name, sign in (("left", 1), ("right", -1)):
    shoulder = body_point(sign * shoulder_half, shoulder_z)
    outside, wrist_z = arm_extents[side_name]
    elbow_side = sign * (shoulder_half + (outside - shoulder_half) * 0.52)
    wrist_side = sign * (shoulder_half + (outside - shoulder_half) * 0.87)
    elbow_z = shoulder_z + (wrist_z - shoulder_z) * 0.52
    wrist_z2 = shoulder_z + (wrist_z - shoulder_z) * 0.88
    elbow = body_point(elbow_side, elbow_z)
    wrist = body_point(wrist_side, wrist_z2)
    hand = body_point(sign * outside, wrist_z)
    arm_component = find_arm_component_points(sign)
    arm_component_sizes[side_name] = len(arm_component)
    if arm_component:
        arm_region = [
            point for point in arm_component
            if abs(point.z - shoulder_z) <= height * 0.12
            and (point[side_index] - center[side_index]) * side_sign * sign >= shoulder_half * 0.62
        ]
        component_lateral = [
            (point[side_index] - center[side_index]) * side_sign * sign
            for point in arm_region
        ]
        def component_landmark(fraction):
            lateral_target = sorted(component_lateral)[int((len(component_lateral) - 1) * fraction)]
            nearby = [
                point for point, lateral in zip(arm_region, component_lateral)
                if abs(lateral - lateral_target) <= height * 0.018
            ]
            if len(nearby) < 6:
                nearby = sorted(
                    arm_region,
                    key=lambda point: abs((point[side_index] - center[side_index]) * side_sign * sign - lateral_target),
                )[:max(6, len(arm_component) // 100)]
            depths = sorted((point[forward_index] - center[forward_index]) * forward_sign for point in nearby)
            heights = sorted(point.z for point in nearby)
            depth = depths[(len(depths) - 1) // 2]
            z = heights[(len(heights) - 1) // 2]
            return point_at(sign * lateral_target, depth, z)

        # Fit the arm chain to the connected outboard island; retain the
        # silhouette estimate only when a coherent arm component is absent.
        wrist, hand = (component_landmark(f) for f in (0.78, 0.985))
        outside = max(component_lateral)
        wrist_z = wrist.z
        wrist_side = sign * abs((wrist[side_index] - center[side_index]) * side_sign)
        wrist_z2 = wrist.z
        # Refit the elbow from the same measured chain. Previously only the
        # wrist/hand were replaced, leaving the elbow aimed at the old
        # silhouette estimate (which may have been influenced by hair).
        elbow = shoulder.lerp(wrist, 0.52)
        elbow_side = sign * abs((elbow[side_index] - center[side_index]) * side_sign)
        elbow_z = elbow.z
    add_bone(side_name + "Shoulder", body_point(sign * torso_width * 0.43, chest_z), shoulder, "chest")
    add_bone(side_name + "UpperArm", shoulder, elbow, side_name + "Shoulder")
    add_bone(side_name + "LowerArm", elbow, wrist, side_name + "UpperArm")
    add_bone(side_name + "Hand", wrist, hand, side_name + "LowerArm")
    palm_side = sign * (abs(wrist_side) + (outside - abs(wrist_side)) * 0.34)
    palm_front = height * 0.018
    finger_tracks = infer_hand_finger_tracks(side_name, sign, arm_component)
    hand_finger_layout_by_side[side_name] = "mesh_point_cloud" if finger_tracks else "proportional_fallback"
    if not finger_tracks:
        finger_length = max(height * 0.02, min(height * 0.045, outside - abs(palm_side) - height * 0.004))
        finger_tracks = []
        for spread in (-0.025, -0.008, 0.010, 0.027):
            finger_front = palm_front + forward_sign * side_sign * spread * height
            finger_tracks.append((
                body_point(palm_side, wrist_z, finger_front),
                body_point(palm_side + sign * finger_length * 0.30, wrist_z, finger_front),
                body_point(palm_side + sign * finger_length * 0.65, wrist_z, finger_front),
                body_point(palm_side + sign * finger_length, wrist_z, finger_front),
            ))
    for finger_name, track in zip(("Index", "Middle", "Ring", "Little"), finger_tracks):
        base, knuckle, middle, tip = track
        prefix = side_name + finger_name
        add_bone(prefix + "Proximal", base, knuckle, side_name + "Hand")
        add_bone(prefix + "Intermediate", knuckle, middle, prefix + "Proximal")
        add_bone(prefix + "Distal", middle, tip, prefix + "Intermediate")
    thumb_base = body_point(palm_side - sign * height * 0.008, wrist_z + height * 0.004, palm_front - forward_sign * side_sign * height * 0.018)
    thumb_mid = body_point(palm_side + sign * height * 0.006, wrist_z + height * 0.01, palm_front - forward_sign * side_sign * height * 0.034)
    thumb_tip = body_point(palm_side + sign * height * 0.022, wrist_z + height * 0.016, palm_front - forward_sign * side_sign * height * 0.047)
    add_bone(side_name + "ThumbMetacarpal", thumb_base, thumb_mid, side_name + "Hand")
    add_bone(side_name + "ThumbProximal", thumb_mid, thumb_tip, side_name + "ThumbMetacarpal")
    add_bone(side_name + "ThumbDistal", thumb_tip, body_point(palm_side + sign * height * 0.032, wrist_z + height * 0.02, palm_front - forward_sign * side_sign * height * 0.055), side_name + "ThumbProximal")
    hip = body_point(sign * hip_half, hip_z)
    knee = body_point(sign * knee_half, zmin + height * 0.27)
    ankle = body_point(sign * ankle_half, zmin + height * 0.055)
    toe = body_point(sign * ankle_half, zmin + height * 0.035, height * 0.12)
    add_bone(side_name + "UpperLeg", hip, knee, "hips")
    add_bone(side_name + "LowerLeg", knee, ankle, side_name + "UpperLeg")
    add_bone(side_name + "Foot", ankle, toe, side_name + "LowerLeg")

arm_data = bpy.data.armatures.new("RigM_AutoRig")
arm_obj = bpy.data.objects.new("RigM_AutoRig", arm_data)
bpy.context.collection.objects.link(arm_obj)
bpy.context.view_layer.objects.active = arm_obj
arm_obj.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
for name, head, tail, parent_name in bones:
    bone = arm_data.edit_bones.new(name)
    bone.head = head
    bone.tail = tail
    if parent_name:
        bone.parent = arm_data.edit_bones[parent_name]
bpy.ops.object.mode_set(mode="OBJECT")
arm_obj.show_in_front = True
arm_obj.display_type = "WIRE"

# Ask Blender's heat-based automatic weights first. A distance-based, normalized
# fallback keeps the result inspectable if Blender reports a heat-weight failure.
bpy.ops.object.select_all(action="DESELECT")
for obj in meshes:
    obj.select_set(True)
arm_obj.select_set(True)
bpy.context.view_layer.objects.active = arm_obj
weights_method = "blender_automatic_weights"
weight_proxy_stats = {
    "attempted": False, "objects": 0, "vertices_before": 0, "vertices_after_weld": 0,
    "proxy_weighted_vertices_before_repair": 0, "proxy_vertices_repaired": 0, "failure": None,
}


def try_welded_proxy_weights():
    """Try heat weights on welded copies, then transfer them to source vertices."""
    proxies = []
    weight_proxy_stats["attempted"] = True
    try:
        for obj in meshes:
            proxy_mesh = obj.data.copy()
            proxy = bpy.data.objects.new(obj.name + "_RigM_WeightProxy", proxy_mesh)
            bpy.context.collection.objects.link(proxy)
            proxy.matrix_world = obj.matrix_world.copy()
            bm = bmesh.new()
            bm.from_mesh(proxy.data)
            before = len(bm.verts)
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
            weight_proxy_stats["vertices_before"] += before
            weight_proxy_stats["vertices_after_weld"] += len(bm.verts)
            bm.to_mesh(proxy.data)
            bm.free()
            proxies.append((obj, proxy))

        bpy.ops.object.select_all(action="DESELECT")
        for _source_obj, proxy in proxies:
            proxy.select_set(True)
        arm_obj.select_set(True)
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.parent_set(type="ARMATURE_AUTO", keep_transform=True, xmirror=True)

        for source_obj, proxy in proxies:
            proxy_groups = {group.name: group for group in proxy.vertex_groups}
            for name, _head, _tail, _parent in bones:
                if name not in proxy_groups:
                    proxy_groups[name] = proxy.vertex_groups.new(name=name)
            proxy_group_names = {group.index: group.name for group in proxy.vertex_groups}
            bone_segments = [(name, head, tail) for name, head, tail, _parent in bones]

            def distance_to_segment(point, start, end):
                direction = end - start
                t = max(0.0, min(1.0, (point - start).dot(direction) / max(direction.length_squared, 1e-12)))
                return (point - (start + direction * t)).length

            weighted_count = 0
            for vertex in proxy.data.vertices:
                weights = [(item.group, item.weight) for item in vertex.groups if item.weight > 1e-10]
                total = sum(weight for _index, weight in weights)
                if total > 1e-10:
                    weighted_count += 1
                    for group_index, weight in weights:
                        proxy.vertex_groups[group_index].add([vertex.index], weight / total, "REPLACE")
                    continue
                point = proxy.matrix_world @ vertex.co
                nearest_name, _head, _tail = min(
                    bone_segments,
                    key=lambda segment: distance_to_segment(point, segment[1], segment[2]),
                )
                proxy_groups[nearest_name].add([vertex.index], 1.0, "REPLACE")
                weight_proxy_stats["proxy_vertices_repaired"] += 1
            weight_proxy_stats["proxy_weighted_vertices_before_repair"] += weighted_count
            if weighted_count < max(1, int(len(proxy.data.vertices) * 0.10)):
                raise RuntimeError(f"Los pesos por calor cubrieron menos del 10% del proxy {proxy.name}.")

            tree = KDTree(len(proxy.data.vertices))
            for vertex in proxy.data.vertices:
                tree.insert(proxy.matrix_world @ vertex.co, vertex.index)
            tree.balance()
            for modifier in list(source_obj.modifiers):
                if modifier.type == "ARMATURE" and modifier.object == arm_obj:
                    source_obj.modifiers.remove(modifier)
            for group in list(source_obj.vertex_groups):
                source_obj.vertex_groups.remove(group)
            target_groups = {name: source_obj.vertex_groups.new(name=name) for name in proxy_group_names.values()}
            for vertex in source_obj.data.vertices:
                world = source_obj.matrix_world @ vertex.co
                _nearest, proxy_index, _distance = tree.find(world)
                proxy_vertex = proxy.data.vertices[proxy_index]
                weights = [(proxy_group_names[item.group], item.weight) for item in proxy_vertex.groups if item.weight > 1e-10]
                total = sum(weight for _name, weight in weights)
                if total <= 1e-10:
                    raise RuntimeError(f"No se encontró peso proxy para {source_obj.name}:{vertex.index}.")
                for name, weight in weights:
                    target_groups[name].add([vertex.index], weight / total, "REPLACE")
            source_obj.parent = arm_obj
            modifier = source_obj.modifiers.new("RigM AutoRig", "ARMATURE")
            modifier.object = arm_obj

        weight_proxy_stats["objects"] = len(proxies)
        return True
    except Exception as exc:
        weight_proxy_stats["failure"] = str(exc)
        print("RIGM_AUTORIG_PROXY_WEIGHT_FALLBACK: " + str(exc), file=sys.stderr)
        return False
    finally:
        for _source_obj, proxy in proxies:
            mesh_data = proxy.data
            bpy.data.objects.remove(proxy, do_unlink=True)
            if mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)


try:
    bpy.ops.object.parent_set(type="ARMATURE_AUTO", keep_transform=True, xmirror=True)
    has_armature_modifier = all(
        any(modifier.type == "ARMATURE" and modifier.object == arm_obj for modifier in obj.modifiers)
        for obj in meshes
    )
    has_complete_vertex_weights = all(
        bool(obj.vertex_groups)
        and all(
            bool(vertex.groups)
            and abs(sum(item.weight for item in vertex.groups) - 1.0) <= 1e-3
            for vertex in obj.data.vertices
        )
        for obj in meshes
    )
    if not has_armature_modifier or not has_complete_vertex_weights:
        raise RuntimeError("Blender no creó modifiers y pesos normalizados para todos los vértices.")
except Exception as exc:
    print("RIGM_AUTORIG_WEIGHT_FALLBACK: " + str(exc), file=sys.stderr)
    if try_welded_proxy_weights():
        weights_method = "blender_automatic_weights_welded_proxy"
    else:
        weights_method = "normalized_bone_segment_distance"
        segments = [
            {"name": name, "head": head.copy(), "tail": tail.copy(), "parent": parent}
            for name, head, tail, parent in bones
        ]
        for segment_index, segment in enumerate(segments):
            segment["neighbors"] = [
                candidate_index for candidate_index, candidate in enumerate(segments)
                if candidate_index == segment_index
                or candidate["parent"] == segment["parent"]
                or candidate["parent"] == segment["name"]
                or segment["parent"] == candidate["name"]
            ]
        for obj in meshes:
            for group in list(obj.vertex_groups):
                obj.vertex_groups.remove(group)
            groups = {segment["name"]: obj.vertex_groups.new(name=segment["name"]) for segment in segments}
            for vertex in obj.data.vertices:
                world = obj.matrix_world @ vertex.co
                distances = []
                for segment in segments:
                    start, end = segment["head"], segment["tail"]
                    direction = end - start
                    t = max(0.0, min(1.0, (world - start).dot(direction) / max(direction.length_squared, 1e-12)))
                    distances.append((world - (start + direction * t)).length)
                nearest = min(range(len(segments)), key=distances.__getitem__)
                allowed = sorted(segments[nearest]["neighbors"], key=distances.__getitem__)[:4]
                raw = [
                    (segments[index]["name"], math.exp(-((distances[index] / max(height * 0.09, 1e-5)) ** 2) * 2.0))
                    for index in allowed
                ]
                total = sum(weight for _name, weight in raw) or 1.0
                for name, weight in raw:
                    groups[name].add([vertex.index], weight / total, "REPLACE")
            if not any(mod.type == "ARMATURE" for mod in obj.modifiers):
                modifier = obj.modifiers.new("RigM AutoRig", "ARMATURE")
                modifier.object = arm_obj


def correct_head_and_arm_weights():
    """Apply conservative head and torso/arm corrections to skin weights."""
    bone_by_name = {name: (head, tail) for name, head, tail, _parent in bones}
    arm_names = [name for name in bone_by_name if name.endswith((
        "Shoulder", "UpperArm", "LowerArm", "Hand", "IndexProximal", "IndexIntermediate", "IndexDistal",
        "MiddleProximal", "MiddleIntermediate", "MiddleDistal", "RingProximal", "RingIntermediate",
        "RingDistal", "LittleProximal", "LittleIntermediate", "LittleDistal", "ThumbMetacarpal",
        "ThumbProximal", "ThumbDistal",
    ))]
    torso_names = [name for name in ("hips", "spine", "chest", "neck") if name in bone_by_name]
    head_corrected = 0
    arm_plane_corrected = 0
    hand_corrected = {"left": 0, "right": 0}
    # Keep the sleeve cap attached to the shoulder. The plane is deliberately
    # inside the shoulder landmark and only affects the torso vertical band.
    arm_plane = shoulder_half * 0.82
    upper_limit = neck_z + height * 0.035

    def segment_distance(point, start, end):
        direction = end - start
        t = max(0.0, min(1.0, (point - start).dot(direction) / max(direction.length_squared, 1e-12)))
        return (point - (start + direction * t)).length

    arm_names_by_side = {
        side: {name for name in arm_names if name.startswith(side)}
        for side in ("left", "right")
    }
    for obj in meshes:
        groups_by_index = {index: group for index, group in enumerate(obj.vertex_groups)}
        arm_group_indices = {index for index, group in groups_by_index.items() if group.name in arm_names}
        head_index = next((index for index, group in groups_by_index.items() if group.name == "head"), None)
        torso_group_indices = {index for index, group in groups_by_index.items() if group.name in torso_names}
        for vertex in obj.data.vertices:
            point = obj.matrix_world @ vertex.co
            existing = [(element.group, element.weight) for element in vertex.groups]
            weights = {index: weight for index, weight in existing}
            if point.z > upper_limit and head_index is not None:
                # Above the neck, move meaningful arm influence to the head;
                # this avoids cutting away real shoulder/sleeve-cap weights.
                arm_weight = sum(weights.get(index, 0.0) for index in arm_group_indices)
                if arm_weight > 1e-8:
                    primary_arm = max((weights.get(index, 0.0) for index in arm_group_indices), default=0.0)
                    if primary_arm > 0.5:
                        weights = {head_index: 1.0}
                    else:
                        for index in arm_group_indices:
                            weights.pop(index, None)
                        weights[head_index] = weights.get(head_index, 0.0) + arm_weight
                    head_corrected += 1

            signed_side = (point[side_index] - center[side_index]) * side_sign
            if hip_z <= point.z <= neck_z and abs(signed_side) < arm_plane:
                for side, side_direction in (("left", 1), ("right", -1)):
                    if signed_side * side_direction <= 0:
                        continue
                    side_indices = {
                        index for index, group in groups_by_index.items()
                        if group.name in arm_names_by_side[side]
                        and not group.name.endswith("Shoulder")
                    }
                    removed_weight = sum(weights.pop(index, 0.0) for index in side_indices)
                    if removed_weight <= 1e-8:
                        continue
                    available_torso = [index for index in torso_group_indices if weights.get(index, 0.0) > 0]
                    if available_torso:
                        total_torso = sum(weights[index] for index in available_torso)
                        for index in available_torso:
                            weights[index] += removed_weight * weights[index] / total_torso
                    else:
                        nearest = min(torso_names, key=lambda name: segment_distance(point, *bone_by_name[name]), default=None)
                        nearest_index = next((index for index, group in groups_by_index.items() if group.name == nearest), None)
                        if nearest_index is not None:
                            weights[nearest_index] = weights.get(nearest_index, 0.0) + removed_weight
                    arm_plane_corrected += 1

            # The distance fallback often gives the long forearm segment most
            # of the palm because the generated hand bone is short. Pull the
            # distal palm region onto Hand so rotating the wrist moves it too.
            for side, side_direction in (("left", 1), ("right", -1)):
                hand_name = side + "Hand"
                hand_index = next((index for index, group in groups_by_index.items() if group.name == hand_name), None)
                if hand_index is None:
                    continue
                hand_head, hand_tail = bone_by_name[hand_name]
                hand_distance = segment_distance(point, hand_head, hand_tail)
                hand_side = (hand_head[side_index] - center[side_index]) * side_sign * side_direction
                point_side = signed_side * side_direction
                if (
                    point_side < hand_side - height * 0.018
                    or hand_distance > height * 0.055
                ):
                    continue
                digit_indices = {
                    index for index, group in groups_by_index.items()
                    if group.name.startswith(side) and group.name.endswith((
                        "IndexProximal", "IndexIntermediate", "IndexDistal",
                        "MiddleProximal", "MiddleIntermediate", "MiddleDistal",
                        "RingProximal", "RingIntermediate", "RingDistal",
                        "LittleProximal", "LittleIntermediate", "LittleDistal",
                        "ThumbMetacarpal", "ThumbProximal", "ThumbDistal",
                    ))
                }
                if sum(weights.get(index, 0.0) for index in digit_indices) >= 0.35:
                    continue
                current_hand = weights.get(hand_index, 0.0)
                target_hand = max(current_hand, 0.65)
                if target_hand <= current_hand + 1e-8:
                    continue
                other_total = sum(weight for index, weight in weights.items() if index != hand_index)
                if other_total > 1e-8:
                    scale = (1.0 - target_hand) / other_total
                    for index in list(weights):
                        if index != hand_index:
                            weights[index] *= scale
                else:
                    target_hand = 1.0
                weights[hand_index] = target_hand
                hand_corrected[side] += 1

            total = sum(weights.values())
            if total > 1e-8 and weights != {index: weight for index, weight in existing}:
                for index, _weight in existing:
                    groups_by_index[index].remove([vertex.index])
                for index, weight in weights.items():
                    groups_by_index[index].add([vertex.index], weight / total, "REPLACE")
    return {
        "method": "mesh2motion_inspired_head_arm_correction_v2",
        "head_corrected_vertex_count": head_corrected,
        "torso_arm_plane_corrected_vertex_count": arm_plane_corrected,
        "hand_region_corrected_vertex_count": hand_corrected,
        "hand_minimum_influence": 0.65,
        "upper_region_min_height_ratio": round((upper_limit - zmin) / height, 4),
        "torso_arm_plane_half_width_height_ratio": round(arm_plane / height, 4),
        "shoulder_weights_preserved": True,
    }


weight_constraints = correct_head_and_arm_weights()


def ensure_complete_vertex_weights():
    """Repair uncovered vertices and normalize every vertex group sum."""
    segments = [(name, head, tail) for name, head, tail, _parent in bones]
    total_vertices = 0
    unweighted_before = 0
    repaired = 0
    normalized = 0

    def segment_distance(point, start, end):
        direction = end - start
        t = max(0.0, min(1.0, (point - start).dot(direction) / max(direction.length_squared, 1e-12)))
        return (point - (start + direction * t)).length

    for obj in meshes:
        groups = {group.name: group for group in obj.vertex_groups}
        for name, _head, _tail in segments:
            if name not in groups:
                groups[name] = obj.vertex_groups.new(name=name)
        for vertex in obj.data.vertices:
            total_vertices += 1
            existing = [(item.group, item.weight) for item in vertex.groups if item.weight > 1e-10]
            total = sum(weight for _index, weight in existing)
            if total <= 1e-10:
                unweighted_before += 1
                point = obj.matrix_world @ vertex.co
                nearest = min(segments, key=lambda segment: segment_distance(point, segment[1], segment[2]))
                groups[nearest[0]].add([vertex.index], 1.0, "REPLACE")
                repaired += 1
                continue
            if abs(total - 1.0) > 1e-5 or len(existing) != len(vertex.groups):
                for index, _weight in existing:
                    obj.vertex_groups[index].add([vertex.index], next(weight for group_index, weight in existing if group_index == index) / total, "REPLACE")
                normalized += 1
    return {
        "method": "complete_normalized_vertex_weights_v1",
        "vertex_count": total_vertices,
        "unweighted_vertex_count_before_repair": unweighted_before,
        "unweighted_vertex_count_after_repair": 0,
        "repaired_vertex_count": repaired,
        "normalized_vertex_count": normalized,
        "coverage_percent_after_repair": 100.0 if total_vertices else 0.0,
    }


weight_coverage = ensure_complete_vertex_weights()


def evaluate_pose_stress():
    """Measure edge distortion in deterministic joint-limit poses."""
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    rest_edges = []
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        world_vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        rest_edges.append((obj, [tuple(edge.vertices) for edge in mesh.edges], world_vertices))
        evaluated.to_mesh_clear()

    pose_specs = []
    for name in arm_obj.pose.bones.keys():
        if name.endswith(("Shoulder", "UpperArm", "LowerArm", "UpperLeg", "LowerLeg")):
            # Probe each local rotation axis in both directions. This is a
            # structural stress test, not a claim that every pose is natural.
            angle = math.radians(35 if name.endswith(("Shoulder", "UpperArm", "UpperLeg")) else 55)
            for axis in range(3):
                for sign in (-1, 1):
                    pose_specs.append((name, axis, sign * angle))

    results = []
    for bone_name, axis, angle in pose_specs:
        pose_bone = arm_obj.pose.bones[bone_name]
        pose_bone.rotation_mode = "XYZ"
        rotation = [0.0, 0.0, 0.0]
        rotation[axis] = angle
        pose_bone.rotation_euler = rotation
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ratios = []
        for obj, edges, baseline in rest_edges:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            current = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
            for first, second in edges:
                old_length = (baseline[first] - baseline[second]).length
                if old_length <= height * 1e-7:
                    continue
                new_length = (current[first] - current[second]).length
                ratios.append(new_length / old_length)
            evaluated.to_mesh_clear()
        pose_bone.rotation_euler = (0.0, 0.0, 0.0)
        if ratios:
            ratios.sort()
            severe = sum(value < 0.65 or value > 1.55 for value in ratios)
            p95 = ratios[min(len(ratios) - 1, int((len(ratios) - 1) * 0.95))]
            p99 = ratios[min(len(ratios) - 1, int((len(ratios) - 1) * 0.99))]
            severe_fraction = severe / len(ratios)
            results.append({
                "bone": bone_name,
                "axis": "XYZ"[axis],
                "degrees": round(math.degrees(angle), 1),
                "edge_ratio_p95": round(p95, 3),
                "edge_ratio_p99": round(p99, 3),
                "severe_edge_fraction": round(severe_fraction, 5),
                "passed": severe_fraction <= 0.02 and p99 <= 3.0,
            })
    bpy.context.view_layer.update()
    failed = [item for item in results if not item["passed"]]
    return {
        "method": "blender_pose_edge_stress_v1",
        "pose_count": len(results),
        "passed": bool(results) and not failed,
        "failed_pose_count": len(failed),
        "poses": results,
        "warnings": [
            f"La prueba de deformación detectó estiramiento anómalo en {len(failed)} de {len(results)} poses; revisa pesos/huesos: "
            + ", ".join(f"{item['bone']} {item['axis']} {item['degrees']}°" for item in failed[:8])
            + ("…" if len(failed) > 8 else "")
        ] if failed else [],
    }


deformation_validation = evaluate_pose_stress()

# Export preserves embedded textures/materials and emits a standard glTF skin.
bpy.ops.object.select_all(action="SELECT")
result = bpy.ops.export_scene.gltf(filepath=output_path, export_format="GLB", export_skins=True, export_animations=True)
if "FINISHED" not in result:
    fail("El exportador glTF de Blender no pudo escribir el GLB.")

ratio = maxs[side_index] - mins[side_index]
depth = maxs[forward_index] - mins[forward_index]
shape_ok = 0.16 <= ratio / height <= 0.95 and 0.06 <= depth / height <= 0.75
report = {
    "algorithm": "silhouette_proportions_v2",
    "weighting": weights_method,
    "hand_bone_layout": "connected_arm_surface_v3",
    "hand_finger_layout_by_side": hand_finger_layout_by_side,
    "arm_component_vertex_count": arm_component_sizes,
    "hand_finger_track_detection": hand_finger_track_counts,
    "forward_axis": input_forward_axis,
    "normalized_forward_axis": "-Y",
    "confidence": "medium" if shape_ok and weights_method in {"blender_automatic_weights", "blender_automatic_weights_welded_proxy"} and deformation_validation["passed"] and weight_coverage["repaired_vertex_count"] == 0 else "low",
    "deformation_validation": deformation_validation,
    "weight_constraints": weight_constraints,
    "weight_coverage": weight_coverage,
    "weight_proxy": weight_proxy_stats,
    "height": round(height, 5),
    "mesh_count": len(meshes),
    "vertex_count": len(points),
    "bone_count": len(bones),
    "proportions": {"pelvis": round(hip_ratio, 4), "chest": round(chest_ratio, 4), "shoulders": round(shoulder_ratio, 4), "neck": round(neck_ratio, 4), "head_center": round(head_ratio, 4), "chibi_head_detected": chibi_head_detected},
    "landmark_height_ratios": {"pelvis": round(hip_ratio, 4), "chest": round(chest_ratio, 4), "neck": round(neck_ratio, 4), "head": round(head_ratio, 4), "knees": 0.27, "ankles": 0.055},
    "landmark_depths": {"pelvis": round(body_depth_at(hip_z), 5), "chest": round(body_depth_at(chest_z), 5), "neck": round(body_depth_at(neck_z), 5), "head": round(body_depth_at(head_z), 5)},
    "warnings": [
        "El esqueleto se estimó desde la silueta y proporciones globales; revisa pivotes y pesos antes de animar.",
        "La inferencia presupone un humanoide erguido y una malla cerrada; ropa suelta, cabello y accesorios pueden sesgar las medidas.",
        "Las cadenas de dedos son una pose anatómica estándar aproximada; ajusta su orientación y pesos para que sigan la geometría de cada mano.",
    ],
}
if weights_method == "normalized_bone_segment_distance":
    report["warnings"].append("Blender no logró los pesos automáticos por calor; se aplicaron pesos aproximados por distancia a los huesos.")
elif weights_method == "blender_automatic_weights_welded_proxy":
    report["warnings"].append("Los pesos por calor se calcularon en una copia soldada para unir vértices duplicados y se transfirieron a la malla original por posición.")
if not shape_ok:
    report["warnings"].append("Las proporciones están fuera del rango humanoide heurístico; la confianza del auto-rig es baja.")
if any(layout != "mesh_point_cloud" for layout in hand_finger_layout_by_side.values()):
    report["warnings"].append("No se pudo detectar una isla conectada por brazo para ajustar los dedos; se usó la disposición proporcional de respaldo.")
report["warnings"].extend(deformation_validation["warnings"])
if weight_coverage["repaired_vertex_count"]:
    report["warnings"].append(
        f"Se reasignaron los pesos de {weight_coverage['repaired_vertex_count']} vértices sin influencia a su hueso más cercano."
    )
with open(report_path, "w", encoding="utf-8") as report_file:
    json.dump(report, report_file, ensure_ascii=False, indent=2)
print("RIGM_AUTORIG_COMPLETE: " + json.dumps(report, ensure_ascii=False))
