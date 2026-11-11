"""Add facial morph targets and secondary (hair/cloth) bones to a rigged GLB.

Everything added here is a reconstruction, not observed geometry: the eyelids
and the open mouth are small overlay patches hidden inside the head in the rest
pose, and the secondary bones only re-split weights the parent bone already had.
The source mesh keeps its vertices, UVs and texture untouched.

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

VISEME_SHAPES = {  # half width, half height, as fractions of the mouth width
    "aa": (0.30, 0.26), "ih": (0.40, 0.09), "ou": (0.15, 0.17), "ee": (0.42, 0.14), "oh": (0.22, 0.25),
}


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
        pixels = texture_pixels()
        if pixels is None or not mesh.uv_layers.active:
            fail("color_like necesita una textura base y UV en la malla.")
        rows, width = pixels.shape[:2]
        uv = mesh.uv_layers.active.data
        colors = np.zeros((vertex_count, 3))
        counts = np.zeros(vertex_count)
        for loop in mesh.loops:
            u, v = uv[loop.index].uv
            colors[loop.vertex_index] += pixels[int(v % 1 * (rows - 1)), int(u % 1 * (width - 1))]
            counts[loop.vertex_index] += 1
        colors /= np.maximum(counts, 1)[:, None]
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
# Facial morph targets: overlay patches conformed to the face surface.
# --------------------------------------------------------------------------
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
    skin_uv = min(skin_choices, key=lambda item: item[0])[1]
    skin_color = pixels[texel(skin_uv)]
    # Line colour: the most uniformly dark texel inside the eye areas (lashes or pupil).
    dark_choices = []
    for eye in eyes:
        (cx, cz), (rx, rz) = eye["center"], eye["radius"]
        for i in range(-10, 11):
            for j in range(-10, 11):
                hit = face_hit(cx + i / 10 * rx, cz + j / 10 * rz)
                if hit:
                    uv = hit_uv(hit)
                    dark_choices.append((float(neighbourhood(*texel(uv), 3).max(0).sum()), uv.copy()))
    if spec.get("line_sample"):
        hit = face_hit(*spec["line_sample"])
        if not hit:
            fail("No encontré la superficie de la cara en line_sample.")
        line_uv = hit_uv(hit)
    elif dark_choices:
        line_uv = min(dark_choices, key=lambda item: item[0])[1]
    else:
        fail("Indica line_sample: un punto [x, z] oscuro de la cara para el color de línea.")
    line_color = pixels[texel(line_uv)]

    verts, base, faces, face_uvs = [], [], [], []
    targets = {}

    def conform(x, z, lift):
        hit = face_hit(x, z)
        if hit is None:
            return None
        location, normal, _index = hit
        return location + normal * lift

    def add_grid(grid, uv, target_names):
        """grid[i][j] = (rest position, {target: position}); adds quads facing -Y."""
        offset = len(verts)
        columns = len(grid[0])
        for row in grid:
            for rest, shaped in row:
                verts.append(rest)
                base.append(rest)
                for name in target_names:
                    targets.setdefault(name, {})[len(verts) - 1] = shaped[name]
        for i in range(len(grid) - 1):
            for j in range(columns - 1):
                a, b = offset + i * columns + j, offset + i * columns + j + 1
                c, d = offset + (i + 1) * columns + j + 1, offset + (i + 1) * columns + j
                faces.append((a, d, c, b))
                face_uvs.append(uv)

    def rotate(dx, dz, degrees):
        angle = math.radians(degrees)
        return dx * math.cos(angle) - dz * math.sin(angle), dx * math.sin(angle) + dz * math.cos(angle)

    def perimeter_skin_uv(eye_outline):
        """Skin texel matching the median colour just outside the eye, so the lid blends in."""
        samples = []
        for step_index in range(48):
            u = -1 + 2 * ((step_index % 24) + 0.5) / 24
            hit = face_hit(*eye_outline(u, 0 if step_index < 24 else 1, 1.14))
            if hit:
                uv = hit_uv(hit)
                block = neighbourhood(*texel(uv), 4)
                samples.append((block.mean(0), float(block.std(0).sum()), uv.copy()))
        calm = [item for item in samples if item[1] < 0.06]
        if len(calm) < 6:
            return skin_uv
        median = np.median(np.array([item[0] for item in calm]), axis=0)
        return min(calm, key=lambda item: float(np.linalg.norm(item[0] - median)) + item[1])[2]

    def smooth_envelope(depth):
        """Smooth a grid of depths without ever going behind the sampled surface."""
        smooth = depth.copy()
        for _ in range(12):
            padded = np.pad(smooth, 1, mode="edge")
            smooth = (padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:] + 4 * smooth) / 8
            smooth = np.minimum(smooth, depth)
        return smooth

    eye_reports = []
    for eye in eyes:
        (cx, cz), (rx, rz) = eye["center"], eye["radius"]
        angle = float(eye.get("angle", 0))
        roundness = float(eye.get("roundness", 3.0))
        lift = 0.045 * rz
        across, down = 28, 16

        def outline(u, v, grow=1.0):
            """v=0 top edge, v=1 bottom edge of a superellipse."""
            half = (max(0.0, 1 - abs(u) ** roundness)) ** (1 / roundness)
            dx, dz = rotate(u * rx * grow, (half - 2 * half * v) * rz * grow, angle)
            return cx + dx, cz + dz

        def lash_curve(u, v):
            """Closed-eye line: a sagging arc in the lower half of the eye."""
            level = float(eye.get("line_level", 0.30))
            sag = float(eye.get("line_sag", 0.22))
            thickness = float(eye.get("line_thickness", 0.075))
            dz = -(level + sag * (1 - u * u)) + (0.5 - v) * 2 * thickness * (1 - 0.55 * u * u)
            dx, dz = rotate(u * 0.9 * rx, dz * rz, angle)
            return cx + dx, cz + dz

        lid_uv = perimeter_skin_uv(outline)
        missing = 0
        fallback_depth = None
        for curve, uv, is_line, rows_v in ((outline, lid_uv, False, down), (lash_curve, line_uv, True, 2)):
            points = np.zeros((rows_v + 1, across + 1, 2))
            depth = np.full((rows_v + 1, across + 1), np.nan)
            for i in range(rows_v + 1):
                for j in range(across + 1):
                    points[i, j] = curve(-1 + 2 * j / across, i / rows_v)
                    hit = face_hit(*points[i, j])
                    if hit:
                        depth[i, j] = hit[0].y
            missing += int(np.isnan(depth).sum())
            if np.isnan(depth).all():
                fail(f"No encontré la superficie de la cara en el ojo `{eye.get('name', '')}`.")
            depth[np.isnan(depth)] = np.nanmin(depth)
            depth = smooth_envelope(depth)
            if fallback_depth is None:
                fallback_depth = depth
            grid = []
            for i in range(rows_v + 1):
                row = []
                for j in range(across + 1):
                    u = -1 + 2 * j / across
                    # The lid thins out towards its border so it has no visible step.
                    edge = min(1.0, 4 * min(i, rows_v - i) / rows_v, 6 * min(j, across - j) / across)
                    offset = lift * (1.6 if is_line else 0.3 + 0.7 * edge)
                    closed = Vector((points[i, j, 0], depth[i, j] - offset, points[i, j, 1]))
                    top_x, top_z = outline(u * 0.97, 0.02)
                    # Collapsed rows have zero area, so the rest shape is invisible even in front of the face;
                    # keeping it in front means half-closed lids never dip behind the surface.
                    rest = Vector((top_x, fallback_depth[0, j] - offset, top_z))
                    row.append((rest, {"blink": closed}))
                grid.append(row)
            add_grid(grid, uv, ["blink"])
        eye_reports.append({"name": eye.get("name", ""), "center": [cx, cz], "radius": [rx, rz], "missed_rays": missing,
                            "lid_color": [round(float(c), 4) for c in pixels[texel(lid_uv)]]})

    mouth_report = None
    if mouth:
        mx, mz = mouth["center"]
        mouth_width = float(mouth["width"])
        angle = float(mouth.get("angle", 0))
        lift = 0.02 * mouth_width
        rings, segments = 3, 20
        centre_rest = conform(mx, mz, 0.0)
        if centre_rest is None:
            fail("No encontré la superficie de la cara en el centro de la boca.")
        centre_rest = centre_rest - Vector((0, lift, 0))  # zero-area at rest, already in front of the face
        names = list(VISEME_SHAPES)
        grid = []
        for ring in range(rings + 1):
            row = []
            for segment in range(segments + 1):
                theta = 2 * math.pi * segment / segments
                shaped = {}
                for name in names:
                    half_w, half_h = (value * mouth_width for value in VISEME_SHAPES[name])
                    radius = ring / rings
                    dx, dz = rotate(math.cos(theta) * half_w * radius, math.sin(theta) * half_h * radius - 0.75 * half_h, angle)
                    point = conform(mx + dx, mz + dz, lift)
                    shaped[name] = point or centre_rest
                row.append((centre_rest.copy(), shaped))
            grid.append(row)
        add_grid(grid, line_uv, names)
        mouth_report = {"center": [mx, mz], "width": mouth_width, "visemes": names}

    face_mesh = bpy.data.meshes.new(body.name + "Face")
    face_mesh.from_pydata([tuple(v) for v in verts], [], faces)
    face_mesh.update()
    layer = face_mesh.uv_layers.new(name=uv_layer.name)
    for polygon in face_mesh.polygons:
        for loop_index in polygon.loop_indices:
            layer.data[loop_index].uv = face_uvs[polygon.index]
    for polygon in face_mesh.polygons:
        polygon.use_smooth = True
    for material in mesh.materials:
        face_mesh.materials.append(material)
    face_object = bpy.data.objects.new(body.name + "Face", face_mesh)
    scene.collection.objects.link(face_object)
    face_object.shape_key_add(name="Basis", from_mix=False)
    for name, moved in targets.items():
        key = face_object.shape_key_add(name=name, from_mix=False)
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
        "eyes": eye_reports, "mouth": mouth_report,
        "skin_color": [round(float(c), 4) for c in skin_color], "line_color": [round(float(c), 4) for c in line_color],
        "method": "hidden_overlay_patches",
    }
    report["notes"].append("Párpados y boca son parches reconstruidos sobre la cara pintada, ocultos en reposo; no son geometría observada.")


if request.get("secondary_bones"):
    add_secondary_bones(request["secondary_bones"])
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
