"""Add facial morph targets, separate fingers and secondary (hair/cloth) bones to a rigged GLB.

Everything added here is a reconstruction, not observed geometry: the eyelids
and the open mouth are small overlay patches shrunk to an invisible miniature in
the rest pose and coloured from their own small atlas, the fingers are generated
capsules that replace a fused finger block, and the secondary bones only
re-split weights the parent bone already had. The source texture is never
written; the only source vertices that move are the ones of a flattened finger block.

Coordinates in the request are Blender world coordinates of the imported GLB
(X side, Y depth with the face looking at -Y, Z up), the same space used by
``avatar3d evidence`` in ``geometry.json``.
"""

import bpy
import json
import math
import sys

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

# Mouth outlines as fractions of the mouth width: half width, half height, height of the upper half (1 = ellipse,
# small = flat top), bend (corners up when positive) and how far the centre drops below the painted mouth line.
MOUTH_SHAPES = {
    "aa": (0.30, 0.26, 1.0, 0.0, 0.75), "ih": (0.40, 0.09, 1.0, 0.0, 0.75), "ou": (0.15, 0.17, 1.0, 0.0, 0.75),
    "ee": (0.42, 0.14, 1.0, 0.0, 0.75), "oh": (0.22, 0.25, 1.0, 0.0, 0.75),
    "happy": (0.46, 0.30, 0.15, 0.10, 0.80), "sad": (0.30, 0.05, 1.0, -0.26, -0.6),
    "angry": (0.25, 0.055, 1.0, -0.14, -0.3), "surprised": (0.19, 0.30, 1.0, 0.0, 0.70),
}
VISEMES = ("aa", "ih", "ou", "ee", "oh")
EXPRESSIONS = ("happy", "sad", "angry", "surprised")
# Eyelid lines per morph, in half eye heights from the eye's middle: (level at the corners, rise at the centre,
# slope towards the nose, whether the lower lid closes up to the line).
EYE_SHAPES = {"happy": (-0.28, 0.46, 0.0, True), "sad": (0.36, 0.0, 0.24, False), "angry": (0.44, 0.0, -0.32, False)}


def fail(message):
    print("MESHINGUP_ENHANCE_ERROR: " + message, file=sys.stderr)
    raise RuntimeError(message)


args = sys.argv[sys.argv.index("--") + 1:]
source_path, output_path, report_path, request_path = args
request = json.load(open(request_path, encoding="utf-8"))
scene = bpy.context.scene
scene.render.fps = int(request.get("fps", 24))
scene.render.fps_base = 1.0
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
try:
    bpy.ops.import_scene.gltf(filepath=source_path)
except Exception as exc:
    fail("No pude importar el GLB: " + str(exc))
armatures = [obj for obj in scene.objects if obj.type == "ARMATURE"]
if len(armatures) != 1:
    fail(f"Se esperaba un armature, encontré {len(armatures)}.")
armature = armatures[0]
skinned = [obj for obj in scene.objects if obj.type == "MESH" and any(m.type == "ARMATURE" and m.object == armature for m in obj.modifiers)]
if not skinned:
    fail("El GLB no tiene mallas unidas al armature.")
body = max(skinned, key=lambda obj: len(obj.data.vertices))
if body.data.shape_keys:
    fail("La malla principal ya tiene morph targets; no la modifico.")
world = body.matrix_world.copy()
mesh = body.data
vertex_count = len(mesh.vertices)
positions = np.array([(world @ v.co)[:] for v in mesh.vertices])
height = float(positions[:, 2].max() - positions[:, 2].min())
report = {"mesh": body.name, "vertices": vertex_count, "reconstruction": True, "notes": []}


def texture_pixels():
    for material in mesh.materials:
        if material and material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image and node.image.size[0]:
                    image = node.image
                    width, rows = image.size
                    return np.array(image.pixels[:], dtype=np.float32).reshape(rows, width, 4)[:, :, :3]
    return None


_vertex_colors = []


def vertex_colors():
    """Mean base-colour texel per vertex (None without texture or UV); computed once."""
    if not _vertex_colors:
        pixels = texture_pixels()
        if pixels is None or not mesh.uv_layers.active:
            _vertex_colors.append(None)
        else:
            rows, width = pixels.shape[:2]
            uv = mesh.uv_layers.active.data
            colors = np.zeros((vertex_count, 3))
            counts = np.zeros(vertex_count)
            for loop in mesh.loops:
                u, v = uv[loop.index].uv
                colors[loop.vertex_index] += pixels[int(v % 1 * (rows - 1)), int(u % 1 * (width - 1))]
                counts[loop.vertex_index] += 1
            _vertex_colors.append(colors / np.maximum(counts, 1)[:, None])
    return _vertex_colors[0]


def group_weights(name):
    """Per-vertex weight of one vertex group as an array."""
    values = np.zeros(vertex_count)
    group = body.vertex_groups.get(name)
    if group is None:
        return values
    for vertex in mesh.vertices:
        for item in vertex.groups:
            if item.group == group.index:
                values[vertex.index] = item.weight
    return values


# --------------------------------------------------------------------------
# Secondary bones: split the parent's weights along new child bones.
# --------------------------------------------------------------------------
def add_secondary_bones(spec):
    parent_name = spec.get("parent", "head")
    if parent_name not in armature.data.bones:
        fail(f"El rig no contiene el hueso padre `{parent_name}`.")
    bones = spec["bones"]
    existing = {bone.name for bone in armature.data.bones}
    for bone in bones:
        if bone["name"] in existing:
            fail(f"El rig ya contiene un hueso llamado `{bone['name']}`.")
    parent_weight = group_weights(parent_name)
    candidates = parent_weight >= float(spec.get("min_parent_weight", 0.5))
    color_filter = spec.get("color_like")
    if color_filter:
        colors = vertex_colors()
        if colors is None:
            fail("color_like necesita una textura base y UV en la malla.")
        nearest = int(np.argmin(np.linalg.norm(positions - np.array(color_filter["point"]), axis=1)))
        reference = colors[nearest]
        tolerance = float(color_filter.get("tolerance", 0.14))
        candidates &= np.linalg.norm(colors - reference, axis=1) <= tolerance
        report["secondary_color_reference"] = [round(float(c), 4) for c in reference]
    if not candidates.any():
        fail("Ningún vértice cumple la selección para los huesos secundarios.")
    inverse_armature = armature.matrix_world.inverted()
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in bones:
        edit_bone = armature.data.edit_bones.new(bone["name"])
        edit_bone.head = inverse_armature @ Vector(bone["head"])
        edit_bone.tail = inverse_armature @ Vector(bone["tail"])
        edit_bone.parent = armature.data.edit_bones[parent_name]
        edit_bone.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    heads = np.array([bone["head"] for bone in bones], dtype=float)
    tails = np.array([bone["tail"] for bone in bones], dtype=float)
    axes = tails - heads
    lengths_sq = (axes * axes).sum(1)
    if (lengths_sq < 1e-10).any():
        fail("Un hueso secundario tiene longitud cero.")
    radius = float(spec.get("radius", 0.12 * height))
    relative = positions[:, None, :] - heads[None, :, :]
    along = (relative * axes[None]).sum(2) / lengths_sq[None]
    closest = heads[None] + axes[None] * np.clip(along, 0, 1)[:, :, None]
    distance = np.linalg.norm(positions[:, None, :] - closest, axis=2)
    share = np.exp(-(distance / radius) ** 2) + 1e-12
    share /= share.sum(1, keepdims=True)
    ramp = np.clip(along, 0, 1)
    ramp = ramp * ramp * (3 - 2 * ramp)
    secondary = share * ramp * float(spec.get("max_weight", 1.0))
    # Work on welded positions: UV seams duplicate vertices, and duplicates must
    # get identical weights or the surface cracks open when the bone moves.
    _unique, welded = np.unique(np.round(positions / (1e-5 * height)).astype(np.int64), axis=0, return_inverse=True)
    welded = welded.reshape(-1)
    welded_count = int(welded.max()) + 1
    edges = np.unique(np.sort(welded[np.array([edge.vertices[:] for edge in mesh.edges])], axis=1), axis=0)
    edges = edges[edges[:, 0] != edges[:, 1]]
    degree = np.zeros(welded_count)
    np.add.at(degree, edges[:, 0], 1)
    np.add.at(degree, edges[:, 1], 1)
    degree[degree == 0] = 1

    def neighbour_mean(values):
        total = np.zeros_like(values, dtype=float)
        np.add.at(total, edges[:, 0], values[edges[:, 1]])
        np.add.at(total, edges[:, 1], values[edges[:, 0]])
        return total / (degree[:, None] if values.ndim == 2 else degree)

    eligible = np.zeros(welded_count)
    np.maximum.at(eligible, welded, (parent_weight >= float(spec.get("min_parent_weight", 0.5))).astype(float))
    selected = np.zeros(welded_count)
    np.add.at(selected, welded, candidates.astype(float))
    selected = (selected > 0).astype(float)
    # Majority filter: fill specks the colour test missed inside the region
    # (shaded undersides) and drop isolated matches outside it.
    for _ in range(int(spec.get("fill_passes", 4))):
        mean = neighbour_mean(selected)
        selected = np.where(mean > 0.6, 1.0, np.where(mean < 0.25, 0.0, selected)) * eligible
    candidates = selected[welded] > 0
    merged = np.zeros((welded_count, secondary.shape[1]))
    np.maximum.at(merged, welded, secondary)
    merged[selected == 0] = 0
    for _ in range(int(spec.get("smooth", 4))):
        merged = 0.5 * merged + 0.5 * neighbour_mean(merged)
        merged[selected == 0] = 0
    secondary = merged[welded]
    secondary[secondary < 0.02] = 0
    scale = np.minimum(1.0, 1.0 / np.maximum(secondary.sum(1), 1e-9))
    secondary *= scale[:, None]
    parent_group = body.vertex_groups[parent_name]
    new_groups = [body.vertex_groups.new(name=bone["name"]) for bone in bones]
    moved = 0
    for index in np.where(secondary.sum(1) > 0)[0]:
        index = int(index)
        original = float(parent_weight[index])
        kept = original * (1 - float(secondary[index].sum()))
        if kept > 1e-4:
            parent_group.add([index], kept, "REPLACE")
        else:
            parent_group.remove([index])
        for column, group in enumerate(new_groups):
            value = original * float(secondary[index, column])
            if value > 1e-4:
                group.add([index], value, "REPLACE")
        moved += 1
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=4)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)
    dominant = {bone["name"]: int(((secondary.argmax(1) == i) & (secondary.max(1) > 0.5)).sum()) for i, bone in enumerate(bones)}
    report["secondary_bones"] = {
        "parent": parent_name, "bones": [bone["name"] for bone in bones], "candidate_vertices": int(candidates.sum()),
        "reweighted_vertices": moved, "dominant_vertices": dominant, "radius": round(radius, 5),
        "method": "parent_weight_split_along_bone",
    }
    report["notes"].append("Los huesos secundarios reparten pesos que ya tenía el hueso padre; revisa los contactos con hombros y espalda.")


# --------------------------------------------------------------------------
# Hands: flatten the fused finger block and add separate fingers with bones.
# --------------------------------------------------------------------------
def add_hands(hands):
    global positions
    existing = {bone.name for bone in armature.data.bones}
    inverse_world = world.inverted()
    inverse_armature = armature.matrix_world.inverted()
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        fail("Los dedos necesitan UV en la malla para tomar el color de piel.")
    first_loop = {}
    for loop in mesh.loops:
        first_loop.setdefault(loop.vertex_index, loop.index)
    verts, faces, face_uvs, weights = [], [], [], []
    bone_specs, hand_reports = [], []
    for hand in hands:
        parent = hand["bone"]
        if parent not in existing:
            fail(f"El rig no contiene el hueso `{parent}`.")
        fingers = hand["fingers"]
        suffixes = {finger["name"]: (("Metacarpal", "Proximal", "Distal") if finger["name"].endswith("Thumb") else ("Proximal", "Intermediate", "Distal")) for finger in fingers}
        for finger in fingers:
            for suffix in suffixes[finger["name"]]:
                if finger["name"] + suffix in existing:
                    fail(f"El rig ya contiene un hueso llamado `{finger['name'] + suffix}`.")
                existing.add(finger["name"] + suffix)
        # The skin colour is read before the block is flattened.
        sample = np.array(hand.get("skin_sample") or fingers[0]["points"][1], dtype=float)
        nearest = int(np.argmin(np.linalg.norm(positions - sample, axis=1)))
        skin_uv = tuple(uv_layer.data[first_loop[nearest]].uv)
        colors = vertex_colors()
        tolerance = float(hand.get("skin_tolerance", 0.25))
        # Only skin-coloured vertices are flattened, so a glove or sleeve keeps its edge.
        skin_like = np.ones(vertex_count, dtype=bool) if colors is None else np.linalg.norm(colors - colors[nearest], axis=1) <= tolerance
        parent_weight = group_weights(parent)
        collapsed = np.zeros(vertex_count, dtype=bool)
        for cut in hand.get("collapse", []):
            point = np.array(cut["point"], dtype=float)
            normal = np.array(cut["normal"], dtype=float)
            normal /= np.linalg.norm(normal)
            relative = positions - point
            along = relative @ normal
            reach = float(cut["radius"])
            # Skin right past the cut, and anything well past it (a seam or shadow in the middle of the block).
            selected = (along > 0) & (np.linalg.norm(relative, axis=1) <= reach) & (parent_weight >= 0.5) & (skin_like | (along > 0.15 * reach))
            # Onto the cut plane, then under the nearest finger: the stump ends inside the finger bases and the
            # skin between the cut and the fingers stretches into a short web instead of a flat plate.
            flat = point + relative[selected] - np.outer(along[selected], normal)
            centres, reaches = [], []
            for finger in fingers:
                start, direction = np.array(finger["points"][0], dtype=float), np.array(finger["points"][1], dtype=float) - np.array(finger["points"][0], dtype=float)
                slope = direction @ normal
                if abs(slope) > 1e-9:
                    centres.append(start + direction * (((point - start) @ normal) / slope))
                    reaches.append(0.8 * float(finger["radius"]))
            if centres:
                centres, reaches = np.array(centres), np.array(reaches)
                offsets = flat[:, None, :] - centres[None]
                distances = np.linalg.norm(offsets, axis=2)
                nearest_finger = distances.argmin(1)
                rows_index = np.arange(len(flat))
                distance = np.maximum(distances[rows_index, nearest_finger], 1e-12)
                scale = np.minimum(1.0, reaches[nearest_finger] / distance)
                flat = centres[nearest_finger] + offsets[rows_index, nearest_finger] * scale[:, None]
            positions[selected] = flat
            collapsed |= selected
        for index in np.where(collapsed)[0]:
            mesh.vertices[int(index)].co = inverse_world @ Vector(positions[int(index)])
        palm_normal = None
        if hand.get("palm_normal"):
            palm_normal = np.array(hand["palm_normal"], dtype=float)
            palm_normal /= np.linalg.norm(palm_normal)
        for finger in fingers:
            points = np.array(finger["points"], dtype=float)
            radius = float(finger["radius"])
            names = [finger["name"] + suffix for suffix in suffixes[finger["name"]]]
            for k, name in enumerate(names):
                bone_specs.append((name, points[k], points[k + 1], names[k - 1] if k else parent))
            # Centreline samples (chain parameter s runs 0..3 over the three bones); the tip keeps room for a round cap.
            tip_radius = radius * float(finger.get("tip_scale", 0.78))
            knuckle = float(finger.get("knuckle", 0.07))
            flatten = float(finger.get("flatten", 0.86)) if palm_normal is not None else 1.0
            lengths = np.linalg.norm(points[1:] - points[:-1], axis=1)
            last_dir = (points[3] - points[2]) / lengths[2]
            end = points[3] - last_dir * min(1.1 * tip_radius, 0.6 * lengths[2])
            line = [points[0], points[1], points[2], end]
            per_segment = 7
            samples = []
            for k in range(3):
                for step in range(per_segment if k < 2 else per_segment + 1):
                    t = step / per_segment
                    samples.append((k + t * (np.linalg.norm(line[k + 1] - line[k]) / lengths[k] if k == 2 else 1.0), line[k] + (line[k + 1] - line[k]) * t))
            # Rings share one reference direction so the tube does not twist; with a palm normal the section is
            # slightly flattened along it, like a real finger.
            axis = points[3] - points[0]
            axis /= np.linalg.norm(axis)
            if palm_normal is not None and np.linalg.norm(np.cross(axis, palm_normal)) > 0.2:
                reference = np.cross(palm_normal, axis)
            else:
                reference = np.cross(axis, [0.0, 0.0, 1.0])
                if np.linalg.norm(reference) < 0.2:
                    reference = np.cross(axis, [0.0, 1.0, 0.0])
            reference /= np.linalg.norm(reference)
            sides = 12
            rings = []
            for i, (s, centre) in enumerate(samples):
                before = samples[max(i - 1, 0)][1]
                after = samples[min(i + 1, len(samples) - 1)][1]
                tangent = (after - before) / np.linalg.norm(after - before)
                side = np.cross(tangent, reference)
                side /= np.linalg.norm(side)
                up = np.cross(side, tangent)
                # Tapers towards the tip, with a soft bulge on each knuckle and a rounded pad before the tip.
                profile = radius + (tip_radius - radius) * min(1.0, s / 3) ** 1.3
                profile *= 1 + knuckle * (math.exp(-((s - 1) / 0.2) ** 2) + 0.8 * math.exp(-((s - 2) / 0.2) ** 2)) + 0.5 * knuckle * math.exp(-((s - 2.6) / 0.3) ** 2)
                rings.append((s, centre, tangent, side, up, profile))
            cap_s, cap_centre, cap_tangent, cap_side, cap_up, cap_radius = rings[-1]
            cap_length = float(np.linalg.norm(points[3] - cap_centre))
            for degrees in (28, 52, 74):
                angle = math.radians(degrees)
                rings.append((3.0, cap_centre + cap_tangent * cap_length * math.sin(angle), cap_tangent, cap_side, cap_up, cap_radius * math.cos(angle)))
            offset = len(verts)

            def influence(s):
                """Each joint bends over a stretch of the tube (smoothstep), so the skin rolls instead of creasing."""
                blend = 0.38
                index = min(2, int(s))
                previous = names[index - 1] if index else parent
                if s - index < blend:
                    t = (s - index) / blend
                    share = (1 - t * t * (3 - 2 * t)) * (0.5 if index else 1.0)
                    return {names[index]: 1 - share, previous: share}
                if index < 2 and index + 1 - s < blend:
                    t = (index + 1 - s) / blend
                    share = 0.5 * (1 - t * t * (3 - 2 * t))
                    return {names[index]: 1 - share, names[index + 1]: share}
                return {names[index]: 1.0}

            for s, centre, _tangent, side, up, ring_radius in rings:
                for n in range(sides):
                    angle = 2 * math.pi * n / sides
                    verts.append(tuple(centre + ring_radius * (math.cos(angle) * side + flatten * math.sin(angle) * up)))
                    weights.append(influence(s))
            for i in range(len(rings) - 1):
                for n in range(sides):
                    a, b = offset + i * sides + n, offset + i * sides + (n + 1) % sides
                    faces.append((a, b, b + sides, a + sides))
                    face_uvs.append(skin_uv)
            verts.append(tuple(points[3]))
            weights.append({names[2]: 1.0})
            verts.append(tuple(points[0]))
            weights.append({parent: 1.0})
            last = offset + (len(rings) - 1) * sides
            for n in range(sides):
                faces.append((last + n, last + (n + 1) % sides, len(verts) - 2))
                face_uvs.append(skin_uv)
                faces.append((offset + (n + 1) % sides, offset + n, len(verts) - 1))
                face_uvs.append(skin_uv)
        hand_reports.append({"bone": parent, "collapsed_vertices": int(collapsed.sum()),
                             "fingers": [finger["name"] for finger in fingers],
                             "skin_color_uv": [round(float(value), 5) for value in skin_uv]})
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    for name, head, tail, parent in bone_specs:
        edit_bone = armature.data.edit_bones.new(name)
        edit_bone.head = inverse_armature @ Vector(head)
        edit_bone.tail = inverse_armature @ Vector(tail)
        edit_bone.parent = armature.data.edit_bones[parent]
        edit_bone.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    finger_mesh = bpy.data.meshes.new(body.name + "Fingers")
    finger_mesh.from_pydata(verts, [], faces)
    finger_mesh.update()
    layer = finger_mesh.uv_layers.new(name=uv_layer.name)
    for polygon in finger_mesh.polygons:
        polygon.use_smooth = True
        for loop_index in polygon.loop_indices:
            layer.data[loop_index].uv = face_uvs[polygon.index]
    for material in mesh.materials:
        finger_mesh.materials.append(material)
    finger_object = bpy.data.objects.new(body.name + "Fingers", finger_mesh)
    scene.collection.objects.link(finger_object)
    groups = {}
    for index, item in enumerate(weights):
        for name, value in item.items():
            if value > 1e-4:
                groups.setdefault(name, finger_object.vertex_groups.new(name=name) if name not in finger_object.vertex_groups else finger_object.vertex_groups[name]).add([index], value, "REPLACE")
    finger_object.parent = armature
    finger_object.matrix_parent_inverse = armature.matrix_world.inverted()
    modifier = finger_object.modifiers.new("Armature", "ARMATURE")
    modifier.object = armature
    # Closed capsules: let Blender orient every face outwards.
    import bmesh
    work = bmesh.new()
    work.from_mesh(finger_mesh)
    bmesh.ops.recalc_face_normals(work, faces=work.faces)
    work.to_mesh(finger_mesh)
    work.free()
    report["hands"] = {"mesh": finger_object.name, "vertices": len(verts), "bones": [item[0] for item in bone_specs],
                       "hands": hand_reports, "method": "collapsed_block_plus_generated_fingers"}
    report["notes"].append("Los dedos son geometría generada (cápsulas con color de piel plano) que sustituye al bloque de dedos fusionado, aplanado contra el guante; no son geometría observada.")


# --------------------------------------------------------------------------
# Facial morph targets: overlay patches conformed to the face surface.
# --------------------------------------------------------------------------
def dilate(mask, passes):
    for _ in range(passes):
        padded = np.pad(mask, 1, mode="constant")
        mask = (padded[:-2, 1:-1] | padded[2:, 1:-1] | padded[1:-1, :-2] | padded[1:-1, 2:] | padded[1:-1, 1:-1]
                | padded[:-2, :-2] | padded[:-2, 2:] | padded[2:, :-2] | padded[2:, 2:])
    return mask


def components(mask):
    labels = np.zeros(mask.shape, dtype=int)
    count = 0
    rows, columns = mask.shape
    for i, j in zip(*np.where(mask)):
        if labels[i, j]:
            continue
        count += 1
        labels[i, j] = count
        stack = [(i, j)]
        while stack:
            a, b = stack.pop()
            for da in (-1, 0, 1):
                for db in (-1, 0, 1):
                    c, d = a + da, b + db
                    if 0 <= c < rows and 0 <= d < columns and mask[c, d] and not labels[c, d]:
                        labels[c, d] = count
                        stack.append((c, d))
    return labels


def inpaint(color, hole):
    """Harmonic fill: colours inside the hole are diffused from its border, so the border itself matches exactly."""
    result = color.copy()
    known = ~hole
    if not known.any() or not hole.any():
        return result
    result[hole] = color[known].mean(0)
    for _ in range(40 * max(color.shape)):
        padded = np.pad(result, ((1, 1), (1, 1), (0, 0)), mode="edge")
        average = (padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:]) / 4
        result[hole] = average[hole]
    return result


def add_face_morphs(spec):
    bone_name = spec.get("bone", "head")
    if bone_name not in armature.data.bones:
        fail(f"El rig no contiene el hueso `{bone_name}`.")
    pixels = texture_pixels()
    uv_layer = mesh.uv_layers.active
    if pixels is None or uv_layer is None:
        fail("Los morphs faciales necesitan una textura base y UV en la malla.")
    rows, width = pixels.shape[:2]
    mesh.calc_loop_triangles()
    triangles = [tuple(tri.vertices) for tri in mesh.loop_triangles]
    triangle_loops = [tuple(tri.loops) for tri in mesh.loop_triangles]
    tree = BVHTree.FromPolygons([Vector(p) for p in positions], triangles)
    head_bone = armature.data.bones[bone_name]
    depth_limit = (armature.matrix_world @ head_bone.head_local).y
    front = float(positions[:, 1].min()) - 0.1 * height

    def face_hit(x, z):
        """Deepest front-facing surface in front of the head bone: the face under the fringe."""
        origin = Vector((x, front, z))
        best = None
        for _ in range(12):
            location, normal, index, _distance = tree.ray_cast(origin, Vector((0, 1, 0)))
            if location is None or location.y >= depth_limit:
                break
            if normal.y < -0.15:
                best = (location.copy(), normal.copy(), index)
            origin = location + Vector((0, 1e-4, 0))
        return best

    def hit_uv(hit):
        location, _normal, index = hit
        a, b, c = (Vector(positions[i]) for i in triangles[index])
        v0, v1, v2 = b - a, c - a, location - a
        d00, d01, d11, d20, d21 = v0.dot(v0), v0.dot(v1), v1.dot(v1), v2.dot(v0), v2.dot(v1)
        denominator = d00 * d11 - d01 * d01 or 1e-12
        wb = (d11 * d20 - d01 * d21) / denominator
        wc = (d00 * d21 - d01 * d20) / denominator
        uvs = [uv_layer.data[i].uv for i in triangle_loops[index]]
        return uvs[0] * (1 - wb - wc) + uvs[1] * wb + uvs[2] * wc

    def texel(uv):
        return int(uv.y % 1 * (rows - 1)), int(uv.x % 1 * (width - 1))

    def neighbourhood(row, column, size):
        return pixels[max(0, row - size):row + size + 1, max(0, column - size):column + size + 1].reshape(-1, 3)

    eyes = spec.get("eyes", [])
    mouth = spec.get("mouth")
    expressions = [name for name in spec.get("expressions", EXPRESSIONS) if name in EXPRESSIONS]
    face_centre_x = sum(eye["center"][0] for eye in eyes) / len(eyes) if len(eyes) >= 2 else (mouth["center"][0] if mouth else 0.0)
    if not eyes and not mouth:
        fail("Indica al menos un ojo o la boca.")
    # Flat skin colour: the calmest texel around the requested sample point.
    if spec.get("skin_sample"):
        sample_x, sample_z = spec["skin_sample"]
    elif len(eyes) >= 2:
        sample_x = sum(eye["center"][0] for eye in eyes) / len(eyes)
        sample_z = sum(eye["center"][1] for eye in eyes) / len(eyes)
    else:
        fail("Indica skin_sample: un punto [x, z] de piel lisa en la cara.")
    step = 0.004 * height
    skin_choices = []
    for dx in (-2, -1, 0, 1, 2):
        for dz in (-2, -1, 0, 1, 2):
            hit = face_hit(sample_x + dx * step, sample_z + dz * step)
            if hit:
                uv = hit_uv(hit)
                skin_choices.append((float(neighbourhood(*texel(uv), 8).std(0).sum()), uv.copy()))
    if not skin_choices:
        fail("No encontré la superficie de la cara en skin_sample.")
    skin_color = pixels[texel(min(skin_choices, key=lambda item: item[0])[1])].astype(float)
    # Line colour: the most uniformly dark texel inside the eye areas (lashes or pupil).
    if spec.get("line_sample"):
        hit = face_hit(*spec["line_sample"])
        if not hit:
            fail("No encontré la superficie de la cara en line_sample.")
        line_color = pixels[texel(hit_uv(hit))].astype(float)
    else:
        dark_choices = []
        for eye in eyes:
            (cx, cz), (rx, rz) = eye["center"], eye["radius"]
            for i in range(-10, 11):
                for j in range(-10, 11):
                    hit = face_hit(cx + i / 10 * rx, cz + j / 10 * rz)
                    if hit:
                        block = neighbourhood(*texel(hit_uv(hit)), 3)
                        dark_choices.append((float(block.max(0).sum()), block.mean(0)))
        if not dark_choices:
            fail("Indica line_sample: un punto [x, z] oscuro de la cara para el color de línea.")
        line_color = min(dark_choices, key=lambda item: item[0])[1].astype(float)

    class Patch:
        """The face seen from the front around one feature: colour, depth and normal on a regular grid."""

        def __init__(self, cx, cz, hx, hz, n):
            self.n, self.x0, self.z0 = n, cx - hx, cz - hz
            self.dx, self.dz = 2 * hx / n, 2 * hz / n
            self.xs = self.x0 + (np.arange(n) + 0.5) * self.dx
            self.zs = self.z0 + (np.arange(n) + 0.5) * self.dz
            self.color = np.zeros((n, n, 3))
            self.valid = np.zeros((n, n), dtype=bool)
            depth = np.full((n, n), np.nan)
            for i, z in enumerate(self.zs):
                for j, x in enumerate(self.xs):
                    hit = face_hit(x, z)
                    if hit:
                        self.color[i, j] = neighbourhood(*texel(hit_uv(hit)), 1).mean(0)
                        depth[i, j] = hit[0].y
                        self.valid[i, j] = True
            if self.valid.sum() < 30:
                fail("No encontré la superficie de la cara en una de las zonas indicadas.")
            # The face is one continuous surface: keep the hits connected to the patch centre without a jump in
            # depth. Hair strands in front and inner geometry behind are dropped, with their colours.
            seeds = np.argwhere(self.valid)
            start = tuple(seeds[np.argmin(((seeds - n / 2) ** 2).sum(1))])
            jump = 2.5 * max(self.dx, self.dz)
            keep = np.zeros((n, n), dtype=bool)
            keep[start] = True
            stack = [start]
            while stack:
                i, j = stack.pop()
                for a, b in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                    if 0 <= a < n and 0 <= b < n and self.valid[a, b] and not keep[a, b] and abs(depth[a, b] - depth[i, j]) < jump:
                        keep[a, b] = True
                        stack.append((a, b))
            if keep.sum() < 30:
                fail("La superficie de la cara es demasiado irregular en una de las zonas indicadas.")
            self.rejected = int((self.valid & ~keep).sum())
            self.valid = keep
            # Cells without a face hit continue the surface smoothly (quadratic fit of the kept ones).
            grid_x, grid_z = np.meshgrid((self.xs - cx) / hx, (self.zs - cz) / hz)
            basis = np.stack([np.ones_like(grid_x), grid_x, grid_z, grid_x * grid_x, grid_x * grid_z, grid_z * grid_z], axis=2)
            depth = np.where(keep, depth, basis @ np.linalg.lstsq(basis[keep], depth[keep], rcond=None)[0])
            # Never behind the sampled surface: smooth, then keep the nearest of both.
            smooth = depth.copy()
            for _ in range(60):
                padded = np.pad(smooth, 1, mode="edge")
                smooth = (padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:] + 4 * smooth) / 8
                smooth = np.minimum(smooth, depth)
            self.depth = smooth
            self.surface = depth
            self.tile = None
            self.different = np.zeros((n, n), dtype=bool)

        def index(self, x, z):
            return (x - self.x0) / self.dx - 0.5, (z - self.z0) / self.dz - 0.5

        def bilinear(self, grid, x, z):
            fx, fz = self.index(x, z)
            fx, fz = min(max(fx, 0.0), self.n - 1.0), min(max(fz, 0.0), self.n - 1.0)
            j, i = min(int(fx), self.n - 2), min(int(fz), self.n - 2)
            tx, tz = fx - j, fz - i
            return (grid[i, j] * (1 - tx) * (1 - tz) + grid[i, j + 1] * tx * (1 - tz)
                    + grid[i + 1, j] * (1 - tx) * tz + grid[i + 1, j + 1] * tx * tz)

        def point(self, x, z, lift, hug=0.0):
            """On the smoothed envelope, or blended towards the real surface (`hug` = 1) to meet the skin without a step."""
            depth = float(self.bilinear(self.depth, x, z))
            if hug > 0:
                depth += (float(self.bilinear(self.surface, x, z)) - depth) * hug
            return Vector((x, depth - lift, z))

        def painted(self, cx, cz, seed, limit, tolerance, grow, keep_skin_tones=True):
            """Cells of the painted feature: non-skin colours connected to the seed ellipse, inside the limit ellipse."""
            n = self.n
            ring = np.zeros((n, n), dtype=bool)
            margin = max(2, n // 10)
            ring[:margin] = ring[-margin:] = True
            ring[:, :margin] = ring[:, -margin:] = True
            local = np.median(self.color[ring & self.valid], axis=0) if (ring & self.valid).sum() > 20 else skin_color
            distance = np.minimum(np.linalg.norm(self.color - local, axis=2), np.linalg.norm(self.color - skin_color, axis=2))
            different = distance > tolerance
            warmth = float(skin_color[0] - skin_color[2])
            if keep_skin_tones and warmth > 0.08:
                # Blush and shading keep the skin's warm hue and brightness; painted features (whites, dark lines,
                # cool colours) do not.
                skin_like = (self.color.max(2) >= 0.6 * float(skin_color.max())) & (self.color[:, :, 0] - self.color[:, :, 2] >= 0.5 * warmth)
                different &= ~skin_like
            self.different = different
            grid_x, grid_z = np.meshgrid(self.xs, self.zs)
            inside = ((grid_x - cx) / limit[0]) ** 2 + ((grid_z - cz) / limit[1]) ** 2 <= 1
            paint = self.valid & different & inside
            labels = components(paint)
            seeds = ((grid_x - cx) / seed[0]) ** 2 + ((grid_z - cz) / seed[1]) ** 2 <= 1
            keep = np.unique(labels[seeds & paint])
            keep = keep[keep > 0]
            if not len(keep):
                return None, False
            mask = np.isin(labels, keep)
            edge = dilate(~inside, 1) & mask
            return dilate(mask, grow), bool(edge.any())

        def envelope(self, mask):
            """Column range and smoothed top/bottom edges (world z) of a mask, as callables of x."""
            columns = np.where(mask.any(0))[0]
            j0, j1 = int(columns[0]), int(columns[-1])
            span = np.arange(j0, j1 + 1)
            top = np.interp(span, columns, [np.where(mask[:, j])[0].max() for j in columns]).astype(float)
            bottom = np.interp(span, columns, [np.where(mask[:, j])[0].min() for j in columns]).astype(float)
            for _ in range(2):
                padded_top, padded_bottom = np.pad(top, 1, mode="edge"), np.pad(bottom, 1, mode="edge")
                top = np.maximum(top, (padded_top[:-2] + 2 * top + padded_top[2:]) / 4)
                bottom = np.minimum(bottom, (padded_bottom[:-2] + 2 * bottom + padded_bottom[2:]) / 4)
            region = np.zeros_like(mask)
            for k, j in enumerate(span):
                region[int(math.floor(bottom[k])):int(math.ceil(top[k])) + 1, j] = True
            xs = self.xs[span]
            top_z = self.z0 + (top + 1) * self.dz
            bottom_z = self.z0 + bottom * self.dz
            return (float(xs[0] - self.dx / 2), float(xs[-1] + self.dx / 2), lambda x: float(np.interp(x, xs, top_z)),
                    lambda x: float(np.interp(x, xs, bottom_z)), region)

    tiles = []  # (image rows x columns x 3, patch or None)

    def add_tile(image, patch=None):
        tiles.append([image, patch, 0, 0])
        return len(tiles) - 1

    PAD = 4
    verts, vert_uvs, faces = [], [], []
    targets = {}

    REST_SCALE = 0.002

    def add_sheet(patch, grid, target_names, anchor_row, lift):
        """grid[i][j] = (shape: {target: position} or one position for all, tile, tile x, tile y); quads face -Y.

        At rest every column is a miniature of its first shape hanging from `anchor_row` (None: the column's
        centre), pulled forward so the straight path to any shape never dips behind the face. A miniature, not a
        zero-area collapse, keeps valid normals parallel to the open shape in Blender and in real-time engines.
        """
        offset = len(verts)
        columns = len(grid[0])
        first = lambda shaped: next(iter(shaped.values())) if isinstance(shaped, dict) else shaped
        for j in range(columns):
            if anchor_row is None:
                anchor = sum((first(row[j][0]) for row in grid), Vector()) / len(grid)
            else:
                anchor = first(grid[anchor_row][j][0]).copy()
            pull = 0.0
            for row in grid:
                for shape in (row[j][0].values() if isinstance(row[j][0], dict) else [row[j][0]]):
                    for t in (0.25, 0.5, 0.75):
                        x, z = anchor.x + (shape.x - anchor.x) * t, anchor.z + (shape.z - anchor.z) * t
                        dip = (anchor.y - pull + (shape.y - anchor.y + pull) * t) - (float(patch.bilinear(patch.depth, x, z)) - lift)
                        if dip > 0:
                            pull += dip / (1 - t)
            for row in grid:
                row[j] = (anchor + (first(row[j][0]) - anchor) * REST_SCALE - Vector((0, pull, 0)), *row[j])
        for row in grid:
            for rest, shaped, tile, tile_x, tile_y in row:
                verts.append(rest)
                vert_uvs.append((tile, tile_x, tile_y))
                for name in target_names:
                    targets.setdefault(name, {})[len(verts) - 1] = shaped[name] if isinstance(shaped, dict) else shaped
        for i in range(len(grid) - 1):
            for j in range(columns - 1):
                a, b = offset + i * columns + j, offset + i * columns + j + 1
                c, d = offset + (i + 1) * columns + j + 1, offset + (i + 1) * columns + j
                faces.append((a, d, c, b))

    line_tile = add_tile(np.tile(line_color, (8, 8, 1)))
    eye_reports = []
    for eye in eyes:
        (cx, cz), (rx, rz) = eye["center"], eye["radius"]
        search = float(eye.get("search", 1.5))
        patch = Patch(cx, cz, rx * search, rz * search, 96)
        mask, clipped = patch.painted(cx, cz, (0.7 * rx, 0.7 * rz), (1.35 * rx, 1.35 * rz),
                                      float(eye.get("tolerance", 0.12)), int(eye.get("grow", 2)))
        if mask is None:
            fail(f"No encontré el ojo pintado `{eye.get('name', '')}`: revisa center y radius.")
        x_left, x_right, top_at, bottom_at, region = patch.envelope(mask)
        patch.tile = add_tile(inpaint(patch.color, dilate(patch.different & dilate(region, 3), 2) | ~patch.valid), patch)
        if request.get("debug_dir"):
            np.savez(request["debug_dir"] + f"/eye_{eye.get('name', len(eye_reports))}.npz", color=patch.color, valid=patch.valid, mask=mask, region=region, filled=tiles[patch.tile][0], depth=patch.depth)
        lift = 0.045 * rz
        top_edge = max(top_at(x) for x in np.linspace(x_left, x_right, 40))
        bottom_edge = min(bottom_at(x) for x in np.linspace(x_left, x_right, 40))
        middle, half = (top_edge + bottom_edge) / 2, (top_edge - bottom_edge) / 2
        level, sag = float(eye.get("line_level", 0.30)), float(eye.get("line_sag", 0.22))
        tilt = math.tan(math.radians(float(eye.get("angle", 0))))

        inner = 1.0 if face_centre_x > cx else -1.0
        eye_names = ["blink"] + [name for name in expressions if name in EYE_SHAPES]

        def line_at(name, x):
            """Height of the lid line of one morph (blink: a sagging arc in the lower half of the painted eye)."""
            u = (2 * x - x_left - x_right) / (x_right - x_left)
            if name == "blink":
                return middle - (level + sag * (1 - u * u)) * half + tilt * (x - (x_left + x_right) / 2)
            corner, rise, slope, _closes = EYE_SHAPES[name]
            return middle + (corner + rise * (1 - u * u) + slope * u * inner) * half

        closes = lambda name: name == "blink" or EYE_SHAPES[name][3]

        def vertex(x, z, lift_scale, tile=None):
            fx, fz = patch.index(x, z)
            point = patch.point(x, z, lift * lift_scale, 0.0 if tile is not None else (1 - lift_scale) / 0.88)
            return point, fx + 0.5, fz + 0.5

        def sheet(rows, names, height, lift_scale, tile=None):
            """rows x columns of {morph: position}; `height(name, x, i)` gives the z of row i."""
            grid = []
            for i in range(rows):
                row = []
                for j, x in enumerate(columns_x if tile is None else lash_x):
                    shaped, uv = {}, None
                    for name in names:
                        shaped[name], fx, fz = vertex(x, height(name, x, i, j), lift_scale(i, j), tile)
                        uv = uv or (fx, fz)
                    row.append((shaped, patch.tile, *uv) if tile is None else (shaped, tile, 4.0, 4.0))
                grid.append(row)
            return grid

        across = 32
        columns_x = [x_left + (x_right - x_left) * j / across for j in range(across + 1)]
        lash_x = [x_left + (x_right - x_left) * (0.03 + 0.94 * j / across) for j in range(across + 1)]
        # Upper lid comes down from the top edge to the morph's line; for the morphs that close the eye the lower
        # lid rises from the bottom edge to the same line. Where the painted eye does not reach the line (thin
        # corners), the lids only cover skin with its own colour.
        upper_top = lambda name, x: max(top_at(x), line_at(name, x))
        lower_bottom = lambda name, x: min(bottom_at(x), line_at(name, x))
        # Lids thin out towards their outer border so they leave no step (and no contact shadow) on the skin.
        feather = lambda steps: 0.12 + 0.88 * min(1.0, max(0.0, steps) / 3)
        side = lambda j: min(j, across - j)
        upper = sheet(11, eye_names, lambda name, x, i, j: upper_top(name, x) + (line_at(name, x) - upper_top(name, x)) * i / 10, lambda i, j: feather(min(i, side(j))))
        closing = [name for name in eye_names if closes(name)]
        lower = sheet(7, closing, lambda name, x, i, j: line_at(name, x) + (lower_bottom(name, x) - line_at(name, x)) * i / 6, lambda i, j: feather(min(6 - i, side(j))))
        add_sheet(patch, upper, eye_names, 0, lift)
        add_sheet(patch, lower, closing, len(lower) - 1, lift)
        thickness = float(eye.get("line_thickness", 0.075)) * half
        # The lash line rides on the upper lid's edge. Its first row only anchors the rest shape near the lid's
        # top; in every morph that row folds onto the line.
        lash = sheet(4, eye_names, lambda name, x, i, j: (upper_top(name, x) - 3 * patch.dz) if i == 0 else
                     line_at(name, x) + (2 - i) * thickness * (1 - (-1 + 2 * j / across) ** 4), lambda i, j: 1.8, line_tile)
        add_sheet(patch, lash, eye_names, 0, 1.8 * lift)
        for name in eye_names:
            for j in range(across + 1):
                index = len(verts) - 4 * (across + 1) + j
                targets[name][index] = targets[name][index + across + 1]
        eye_reports.append({"name": eye.get("name", ""), "center": [cx, cz], "radius": [rx, rz],
                            "painted_cells": int(mask.sum()), "off_surface_cells": patch.rejected, "covered_cells": int(region.sum()), "reaches_search_limit": clipped,
                            "x_range": [round(x_left, 5), round(x_right, 5)], "z_range": [round(bottom_edge, 5), round(top_edge, 5)]})
        if clipped:
            report["notes"].append(f"El ojo `{eye.get('name', '')}` llega al límite de búsqueda: puede quedar pintura sin cubrir; revisa center/radius.")

    mouth_report = None
    if mouth:
        mx, mz = mouth["center"]
        mouth_width = float(mouth["width"])
        angle = math.radians(float(mouth.get("angle", 0)))
        lift = 0.03 * mouth_width
        names = list(VISEMES) + list(expressions)
        patch = Patch(mx, mz, 0.95 * mouth_width, 0.6 * mouth_width, 64)
        # Skin cover over the painted mouth line: a band that opens around the line with any viseme.
        mask, _clipped = patch.painted(mx, mz, (0.5 * mouth_width, 0.25 * mouth_width), (0.9 * mouth_width, 0.55 * mouth_width),
                                       float(mouth.get("tolerance", 0.10)), 2, keep_skin_tones=False)
        covered = 0
        if mask is not None:
            x_left, x_right, top_at, bottom_at, region = patch.envelope(mask)
            patch.tile = add_tile(inpaint(patch.color, dilate(patch.different & dilate(region, 3), 2) | ~patch.valid), patch)
            covered = int(region.sum())
            across = 24
            cover = []
            for i in range(5):
                row = []
                for j in range(across + 1):
                    x = x_left + (x_right - x_left) * j / across
                    top, bottom = top_at(x), bottom_at(x)
                    z = top + (bottom - top) * i / 4
                    fx, fz = patch.index(x, z)
                    row.append((patch.point(x, z, lift), patch.tile, fx + 0.5, fz + 0.5))
                cover.append(row)
            add_sheet(patch, cover, names, 2, lift)
        else:
            report["notes"].append("No encontré una boca pintada que cubrir; la boca abierta se dibuja sobre la piel.")
        # Mouth interior: dark outline, warm inside and a tongue, mapped radially.
        size = 64
        grid_x, grid_y = np.meshgrid((np.arange(size) + 0.5) / size * 2 - 1, (np.arange(size) + 0.5) / size * 2 - 1)
        radius = np.hypot(grid_x, grid_y)
        inside_color = np.clip(0.55 * line_color + 0.45 * np.array([0.62, 0.16, 0.20]), 0, 1)
        interior = np.tile(inside_color, (size, size, 1)) * (0.85 + 0.15 * np.clip(-grid_y, 0, 1))[:, :, None]
        tongue = ((grid_x / 0.62) ** 2 + ((grid_y + 0.62) / 0.5) ** 2) <= 1
        interior[tongue] = np.array([0.86, 0.43, 0.45])
        interior[radius > 0.84] = line_color
        mouth_tile = add_tile(interior)
        rings, segments = 4, 24
        grid = []
        for ring in range(rings + 1):
            row = []
            for segment in range(segments + 1):
                theta = 2 * math.pi * segment / segments
                fraction = ring / rings
                shaped = {}
                for name in names:
                    half_w, half_h, top, bend, drop = MOUTH_SHAPES[name]
                    half_w, half_h = half_w * mouth_width, half_h * mouth_width
                    px = math.cos(theta) * half_w * fraction
                    pz = math.sin(theta) * half_h * fraction * (top if math.sin(theta) > 0 else 1.0) - drop * half_h + bend * half_w * (math.cos(theta) * fraction) ** 2
                    shaped[name] = patch.point(mx + px * math.cos(angle) - pz * math.sin(angle), mz + px * math.sin(angle) + pz * math.cos(angle), 2 * lift)
                row.append((shaped, mouth_tile, size / 2 * (1 + math.cos(theta) * fraction), size / 2 * (1 + math.sin(theta) * fraction)))
            grid.append(row)
        # Rest anchor at mid radius: a thin ring keeps every quad non-degenerate (a centre anchor breaks the normals).
        add_sheet(patch, grid, names, None, 2 * lift)
        mouth_report = {"center": [mx, mz], "width": mouth_width, "visemes": list(VISEMES), "expressions": list(expressions), "covered_cells": covered}

    # Atlas: one padded tile per patch on shelves; sampled once, never written back to the source texture.
    atlas_width = 512
    cursor_x = cursor_y = shelf = 0
    for tile in tiles:
        tile_height, tile_width = tile[0].shape[0] + 2 * PAD, tile[0].shape[1] + 2 * PAD
        if cursor_x + tile_width > atlas_width:
            cursor_x, cursor_y, shelf = 0, cursor_y + shelf, 0
        tile[2], tile[3] = cursor_x + PAD, cursor_y + PAD
        cursor_x += tile_width
        shelf = max(shelf, tile_height)
    atlas_height = 64
    while atlas_height < cursor_y + shelf:
        atlas_height *= 2
    atlas = np.tile(skin_color, (atlas_height, atlas_width, 1))
    for image, _patch, x, y in tiles:
        atlas[y - PAD:y + image.shape[0] + PAD, x - PAD:x + image.shape[1] + PAD] = np.pad(image, ((PAD, PAD), (PAD, PAD), (0, 0)), mode="edge")
    atlas_image = bpy.data.images.new(body.name + "FaceAtlas", atlas_width, atlas_height, alpha=False)
    atlas_image.pixels.foreach_set(np.concatenate([atlas, np.ones((atlas_height, atlas_width, 1))], axis=2).astype(np.float32).ravel())
    atlas_image.update()
    if request.get("debug_dir"):
        atlas_image.filepath_raw = request["debug_dir"] + "/face_atlas.png"
        atlas_image.file_format = "PNG"
        atlas_image.save()
    atlas_image.pack()
    source_material = next(material for material in mesh.materials if material and material.use_nodes)
    face_material = source_material.copy()
    face_material.name = source_material.name + "Face"
    for node in face_material.node_tree.nodes:
        if node.type == "TEX_IMAGE":
            node.image = atlas_image

    face_mesh = bpy.data.meshes.new(body.name + "Face")
    face_mesh.from_pydata([tuple(v) for v in verts], [], faces)
    face_mesh.update()
    layer = face_mesh.uv_layers.new(name=uv_layer.name)
    for loop in face_mesh.loops:
        tile, tile_x, tile_y = vert_uvs[loop.vertex_index]
        layer.data[loop.index].uv = ((tiles[tile][2] + tile_x) / atlas_width, (tiles[tile][3] + tile_y) / atlas_height)
    for polygon in face_mesh.polygons:
        polygon.use_smooth = True
    face_mesh.materials.append(face_material)
    face_object = bpy.data.objects.new(body.name + "Face", face_mesh)
    scene.collection.objects.link(face_object)
    face_object.shape_key_add(name="Basis", from_mix=False)
    for name, moved in targets.items():
        key = face_object.shape_key_add(name=name, from_mix=False)
        key.value = 0.0
        for index, position in moved.items():
            key.data[index].co = position
    group = face_object.vertex_groups.new(name=bone_name)
    group.add(list(range(len(verts))), 1.0, "REPLACE")
    face_object.parent = armature
    face_object.matrix_parent_inverse = armature.matrix_world.inverted()
    modifier = face_object.modifiers.new("Armature", "ARMATURE")
    modifier.object = armature
    report["face_morphs"] = {
        "mesh": face_object.name, "bone": bone_name, "targets": list(targets), "vertices": len(verts),
        "eyes": eye_reports, "mouth": mouth_report, "atlas": [atlas_width, atlas_height],
        "skin_color": [round(float(c), 4) for c in skin_color], "line_color": [round(float(c), 4) for c in line_color],
        "method": "hidden_overlay_patches_with_inpainted_atlas",
    }
    report["notes"].append("Párpados y boca son parches reconstruidos sobre la cara pintada, ocultos en reposo; su color se rellena desde la piel que rodea cada rasgo y no es geometría ni textura observada.")


if request.get("secondary_bones"):
    add_secondary_bones(request["secondary_bones"])
if request.get("hands"):
    add_hands(request["hands"])
if request.get("face"):
    add_face_morphs(request["face"])

bpy.ops.object.select_all(action="SELECT")
try:
    result = bpy.ops.export_scene.gltf(
        filepath=output_path, export_format="GLB", export_skins=True,
        export_animations=bool(bpy.data.actions), export_animation_mode="ACTIONS", export_frame_range=True,
        export_morph=True, export_morph_normal=False,
    )
except Exception as exc:
    fail("Falló la exportación glTF de Blender: " + str(exc))
if "FINISHED" not in result:
    fail("El exportador glTF de Blender no terminó correctamente.")
with open(report_path, "w", encoding="utf-8") as report_file:
    json.dump(report, report_file, ensure_ascii=False, indent=2)
print("MESHINGUP_ENHANCE_COMPLETE: " + json.dumps(report, ensure_ascii=False))
