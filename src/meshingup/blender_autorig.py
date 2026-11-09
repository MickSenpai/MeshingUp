"""Blender background script used by MeshingUp's supervised GLB auto-rig command.

Run by avatar3d.autorig_glb; this file intentionally has no MeshingUp imports because
Blender ships its own Python runtime.
"""

import bpy
import bmesh
import heapq
import json
import math
import sys
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree


def fail(message):
    print("MESHINGUP_AUTORIG_ERROR: " + message, file=sys.stderr)
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
    fail("El GLB ya tiene un armature; MeshingUp no lo reemplazará automáticamente.")

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
    # Transform roots only: descendants inherit the rotation from their parent.
    roots = [(obj, obj.matrix_world.copy()) for obj in bpy.context.scene.objects if obj.parent is None]
    for obj, world_matrix in roots:
        obj.matrix_world = transform @ world_matrix
    bpy.context.view_layer.update()
forward_axis = "-Y"

points = []
for obj in meshes:
    points.extend(obj.matrix_world @ vertex.co for vertex in obj.data.vertices)
if len(points) < 30:
    fail("La malla tiene muy pocos vértices para inferir proporciones humanoides.")

hair_texture_masks = {}


def hair_masks_from_texture():
    """Mark hair-coloured vertices by sampling the base-colour texture.

    Geometry cannot tell hair that rests on a sleeve from the sleeve, but their
    texture colours differ. The hair colour is learned from the crown (the top
    6% of the model by height, which is hair on almost every character) rather
    than assumed, vertices within a Lab colour distance of it are hair, and a
    neighbour-majority filter removes isolated texture specks. Returns a stats
    dict; masks are stored in ``hair_texture_masks``. When the texture, UVs or
    a consistent crown colour are missing, or the result is implausible, no
    mask is stored and the geometric heuristic is used instead.
    """
    stats = {"method": "texture_crown_colour_v1", "used": False}
    try:
        import numpy as np
    except ImportError:
        stats["reason"] = "numpy no disponible"
        return stats
    srgb_to_xyz = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    white = np.array([0.95047, 1.0, 1.08883])

    def to_lab(linear):
        xyz = linear @ srgb_to_xyz.T / white
        f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
        return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)

    for obj in meshes:
        mesh = obj.data
        images = {node.image.name: node.image for material in mesh.materials if material and material.use_nodes
                  for node in material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image}
        if len(images) != 1 or mesh.uv_layers.active is None:
            stats["reason"] = "se necesita exactamente una textura con UV"
            continue
        image = next(iter(images.values()))
        width, height_px = image.size
        if width < 2 or height_px < 2:
            continue
        pixels = np.empty(width * height_px * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(height_px, width, 4)
        loop_count = len(mesh.loops)
        uv = np.empty(loop_count * 2, dtype=np.float32)
        mesh.uv_layers.active.data.foreach_get("uv", uv)
        uv = uv.reshape(loop_count, 2)
        loop_vertex = np.empty(loop_count, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_vertex)
        columns = np.clip((uv[:, 0] % 1.0 * width).astype(int), 0, width - 1)
        rows = np.clip((uv[:, 1] % 1.0 * height_px).astype(int), 0, height_px - 1)
        vertex_count = len(mesh.vertices)
        total, seen = np.zeros((vertex_count, 3)), np.zeros(vertex_count)
        np.add.at(total, loop_vertex, pixels[rows, columns, :3])
        np.add.at(seen, loop_vertex, 1)
        lab = to_lab(total / np.maximum(seen, 1)[:, None])
        heights = np.array([(obj.matrix_world @ vertex.co).z for vertex in mesh.vertices])
        crown = lab[heights > heights.max() - 0.06 * (heights.max() - heights.min())]
        if len(crown) < 30:
            stats["reason"] = "corona con pocos vértices"
            continue
        crown_colour = np.median(crown, axis=0)
        spread = float(np.median(np.linalg.norm(crown - crown_colour, axis=1)))
        if spread > 10.0:
            stats["reason"] = f"la corona no tiene un color uniforme (dispersión {spread:.1f})"
            continue
        distances = np.linalg.norm(lab - crown_colour, axis=1)
        tolerance = min(22.0, max(12.0, 8.0 * spread))
        # Adaptive threshold: look for the valley between the hair-coloured mode
        # (near the crown colour) and everything else. Without a clear valley the
        # colour classifier cannot be trusted and the geometric method is used.
        histogram, _bin_edges = np.histogram(distances, bins=np.arange(0.0, 41.0, 1.0))
        density = np.convolve(histogram.astype(float), np.ones(3) / 3.0, mode="same")
        low, high = 6, 22
        valley = int(low + np.argmin(density[low:high + 1]))
        side_peak = float(density[:valley].max()) if valley > 0 else 0.0
        later_peak = float(density[valley + 1:].max()) if valley + 1 < len(density) else 0.0
        valley_ratio = float(density[valley] / max(1.0, min(side_peak, later_peak)))
        threshold_method = "crown_spread"
        if valley_ratio < 0.6 and low < valley < high:
            tolerance = float(valley) + 0.5
            threshold_method = "histogram_valley"
        elif float((distances < tolerance).mean()) > 0.45:
            stats["reason"] = (f"sin valle claro entre pelo y resto de colores y {float((distances < tolerance).mean()):.0%} "
                               f"de los vértices quedarían como pelo (dispersión {spread:.1f})")
            continue
        mask = distances < tolerance
        edges = np.empty(len(mesh.edges) * 2, dtype=np.int32)
        mesh.edges.foreach_get("vertices", edges)
        edges = edges.reshape(-1, 2)
        raw_hair = int(mask.sum())
        for _ in range(3):
            neighbours = np.zeros(vertex_count)
            hair_neighbours = np.zeros(vertex_count)
            np.add.at(neighbours, edges[:, 0], 1); np.add.at(neighbours, edges[:, 1], 1)
            np.add.at(hair_neighbours, edges[:, 0], mask[edges[:, 1]]); np.add.at(hair_neighbours, edges[:, 1], mask[edges[:, 0]])
            share = hair_neighbours / np.maximum(neighbours, 1)
            mask = np.where(share >= 0.7, True, np.where(share <= 0.3, False, mask))
        # Real hair is hair-coloured surface connected up to the crown. Brown
        # texture on a fingertip outline or a belt is not, so keep only the
        # hair-coloured regions reachable from the crown. Vertices are welded
        # first so texture seams do not cut a region in two.
        coordinates = np.empty(vertex_count * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", coordinates)
        _unique, welded = np.unique(np.round(coordinates.reshape(-1, 3) / 1e-5).astype(np.int64), axis=0, return_inverse=True)
        welded = welded.reshape(-1)
        neighbours_of = {}
        for first, second in edges:
            a, b = int(welded[first]), int(welded[second])
            if a != b and mask[first] and mask[second]:
                neighbours_of.setdefault(a, []).append(b)
                neighbours_of.setdefault(b, []).append(a)
        crown_mask = heights > heights.max() - 0.06 * (heights.max() - heights.min())
        reached = {int(welded[i]) for i in np.nonzero(crown_mask & mask)[0]}
        frontier = list(reached)
        while frontier:
            current = frontier.pop()
            for other in neighbours_of.get(current, ()):
                if other not in reached:
                    reached.add(other)
                    frontier.append(other)
        connected = np.fromiter((int(welded[i]) in reached for i in range(vertex_count)), dtype=bool, count=vertex_count)
        disconnected = int((mask & ~connected).sum())
        mask = mask & connected
        fraction = float(mask.mean())
        if not 0.03 <= fraction <= 0.40:
            stats["reason"] = f"fracción de pelo implausible ({fraction:.0%})"
            continue
        hair_texture_masks[obj.name] = mask
        stats.update({
            "used": True, "crown_lab": [round(float(v), 1) for v in crown_colour], "crown_spread": round(spread, 2),
            "tolerance": round(tolerance, 1), "threshold_method": threshold_method, "valley_ratio": round(valley_ratio, 3),
            "hair_vertices_before_filter": raw_hair,
            "hair_vertices": int(mask.sum()), "hair_fraction": round(fraction, 3),
            "hair_coloured_but_not_connected_to_crown": disconnected,
            "note": "Heurística de color: se aprende el color del pelo en la corona y se conservan las regiones de ese color conectadas a ella. No es separación semántica confirmada; revisa los contactos pelo/ropa.",
        })
        stats.pop("reason", None)
    return stats


hair_texture_stats = hair_masks_from_texture()

# Hair hides the neck and shoulders in the silhouette, so body proportions are
# measured on the vertices that are not hair whenever the texture says which are.
skin_points = points
if hair_texture_masks:
    skin_points = [
        obj.matrix_world @ vertex.co
        for obj in meshes
        for index, vertex in enumerate(obj.data.vertices)
        if obj.name not in hair_texture_masks or not hair_texture_masks[obj.name][index]
    ]
    if len(skin_points) < 30:
        skin_points = points

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
    values = sorted((point[side_index] - center[side_index]) * side_sign for point in skin_points if abs(point.z - z) <= band)
    if len(values) < 8:
        values = sorted((point[side_index] - center[side_index]) * side_sign for point in skin_points if abs(point.z - z) <= height * 0.035)
    if not values:
        return 0.0, 0.0
    return values[int((len(values) - 1) * 0.1)], values[int((len(values) - 1) * 0.9)]


# Detect a large stylized head from the vertical width profile. The adult
# fallback remains available for ordinary proportions and non-humanoids are
# reported with low confidence below.
profile_bins = 64
profile_samples = [[] for _ in range(profile_bins)]
for point in skin_points:
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
# The head pivots at the base of the skull, just above the narrowest point of
# the neck. Halfway up a large head would put the pivot at eye level.
head_ratio = neck_ratio + 0.03 if chibi_head_detected else 0.94

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
        for point in skin_points
        if abs(point.z - z) <= band
        and abs((point[side_index] - center[side_index]) * side_sign) <= torso_width * 0.72
    ]
    if len(values) < 8:
        values = [
            (point[forward_index] - center[forward_index]) * forward_sign
            for point in skin_points if abs(point.z - z) <= height * 0.04
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
hand_finger_separation = {}
hand_landmark_diagnostics = {}
leg_landmark_diagnostics = {}


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
        hand_finger_separation[side_name] = {"confidence": "low", "reason": "insufficient_distal_samples", "sample_count": len(candidates)}
        return []

    def quantile(values, fraction):
        values = sorted(values)
        return values[min(len(values) - 1, max(0, int((len(values) - 1) * fraction)))]

    depths = [item[2] for item in candidates]
    depth_low, depth_high = quantile(depths, 0.05), quantile(depths, 0.95)
    # Four quantiles below always produce four candidate chains. Measure the
    # actual distal occupancy separately so that this is never mistaken for
    # evidence that four fingers are geometrically separated.
    bin_count = 32
    span = max(depth_high - depth_low, height * 0.004)
    histogram = [0] * bin_count
    for depth in depths:
        index = min(bin_count - 1, max(0, int((depth - depth_low) / span * bin_count)))
        histogram[index] += 1
    smooth = [sum(histogram[max(0, i - 1):min(bin_count, i + 2)]) for i in range(bin_count)]
    peaks = [i for i in range(1, bin_count - 1) if smooth[i] >= smooth[i - 1] and smooth[i] > smooth[i + 1]]
    peak_floor = max(2, int(max(smooth, default=0) * 0.22))
    peaks = [i for i in peaks if smooth[i] >= peak_floor]
    selected_peaks = []
    for peak in sorted(peaks, key=lambda i: smooth[i], reverse=True):
        if all(abs(peak - kept) >= 2 for kept in selected_peaks):
            selected_peaks.append(peak)
    selected_peaks.sort()
    valley_ratios = []
    for first, second in zip(selected_peaks, selected_peaks[1:]):
        valley = min(smooth[first:second + 1])
        valley_ratios.append(round(valley / max(1, min(smooth[first], smooth[second])), 3))
    separated_gaps = sum(value <= 0.55 for value in valley_ratios)
    confidence = "medium" if len(selected_peaks) in (3, 4, 5) and separated_gaps >= 2 else "low"
    hand_finger_separation[side_name] = {
        "method": "distal_depth_occupancy_v1", "sample_count": len(candidates),
        "candidate_chains_generated": 4, "detected_occupancy_peaks": len(selected_peaks),
        "chain_label_order": "Index_to_Little_by_descending_depth_quantile",
        "peak_depth_ratios": [round(i / (bin_count - 1), 3) for i in selected_peaks],
        "valley_to_peak_ratios": valley_ratios, "clear_gaps": separated_gaps,
        "confidence": confidence,
        "note": "Los picos estiman valles visibles en la nube; no confirman separación topológica ni el orden anatómico de los dedos. Se usa orden descendente de profundidad para el modelo observado; requiere revisión en otros modelos."
    }
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
        # Each chain belongs to one depth lane. Interpolating between medians
        # of independently sampled base/tip bands made bones drift sideways
        # through the palm when the mesh was sparse or asymmetric.
        track_depth = center_depth
        base_z = quantile([item[0].z for item in base_band], 0.5)
        tip_z = quantile([item[0].z for item in tip_band], 0.5)

        def position(progress):
            lateral = base_side + (tip_side - base_side) * progress
            z = base_z + (tip_z - base_z) * progress
            return point_at(side_direction * lateral, track_depth, z)

        tracks.append((position(0.0), position(0.30), position(0.66), position(1.0)))
    hand_finger_track_counts[side_name] = {"candidate_vertices": len(candidates), "tracks": len(tracks), "geometry_confidence": confidence}
    # The viewer inspection of Meido established that increasing depth maps
    # from the thumb/index edge toward the little-finger edge in reverse of
    # the old labels. Keep this convention explicit in the diagnostic report.
    tracks.reverse()
    return tracks if len(tracks) == 4 else []


arm_axis_diagnostics = {}


def trace_arm_axis(sign, tip_lateral):
    """Follow an extended arm inward from the hand and fit its axis.

    Starts at the fingertips, where nothing but the hand is present, and walks
    toward the torso in narrow lateral slices, re-centring each slice on the
    cluster nearest the previous centre. When hair or the torso comes into the
    window the slice suddenly holds far more vertices than the arm slices before
    it; tracing stops there and the axis (a gently tilted straight line) is
    extrapolated from the clean slices only. Returns
    (height_at, depth_at, samples) or None when too few clean slices exist.
    """
    lateral_floor = shoulder_half * 1.1
    if tip_lateral <= lateral_floor * 1.25:
        return None
    candidates = []
    for point in points:
        lateral = (point[side_index] - center[side_index]) * side_sign * sign
        if lateral >= lateral_floor:
            candidates.append((lateral, (point[forward_index] - center[forward_index]) * forward_sign, point.z))
    tip = [item for item in candidates if item[0] >= tip_lateral * 0.93]
    if len(tip) < 8:
        return None
    centre_z = sorted(item[2] for item in tip)[len(tip) // 2]
    centre_depth = sorted(item[1] for item in tip)[len(tip) // 2]
    window = height * 0.04
    step = height * 0.02
    samples = []
    lateral = tip_lateral * 0.92
    while lateral > lateral_floor:
        band = [item for item in candidates if lateral - step <= item[0] < lateral]
        near = []
        for _ in range(4):
            near = [item for item in band if abs(item[2] - centre_z) <= window and abs(item[1] - centre_depth) <= window]
            if len(near) < 8:
                break
            centre_z = sorted(item[2] for item in near)[len(near) // 2]
            centre_depth = sorted(item[1] for item in near)[len(near) // 2]
        if len(near) < 8:
            break
        radial = sorted(math.hypot(item[1] - centre_depth, item[2] - centre_z) for item in near)
        # The first two slices are the hand and are denser than the forearm, so
        # the reference size comes from slices after them.
        reference = [item[3] for item in samples[2:]]
        if len(reference) >= 3 and len(near) > 1.7 * sorted(reference)[len(reference) // 2]:
            break
        samples.append((lateral - step / 2, centre_depth, centre_z, len(near), radial[int((len(radial) - 1) * 0.75)]))
        lateral -= step
    if len(samples) < 5:
        return None
    count = len(samples)
    mean_x = sum(item[0] for item in samples) / count
    mean_z = sum(item[2] for item in samples) / count
    spread = sum((item[0] - mean_x) ** 2 for item in samples)
    slope = sum((item[0] - mean_x) * (item[2] - mean_z) for item in samples) / spread if spread > 1e-12 else 0.0
    slope = max(-0.3, min(0.3, slope))  # about +-17 degrees; keeps a noisy fit from tilting the extrapolation
    depth = sorted(item[1] for item in samples)[count // 2]
    # Tube radius from the forearm slices (not the denser hand slices).
    radius = sorted(item[4] for item in samples[2:] or samples)[len(samples[2:] or samples) // 2]
    return (lambda x: mean_z + slope * (x - mean_x)), (lambda _x: depth), samples, radius


pelvis = body_point(0, hip_z)
add_bone("hips", pelvis, body_point(0, spine_z), None)
add_bone("spine", body_point(0, spine_z), body_point(0, chest_z), "hips")
add_bone("chest", body_point(0, chest_z), body_point(0, neck_z), "spine")
add_bone("neck", body_point(0, neck_z), body_point(0, head_z), "chest")
# A large head is a vertical axis from the skull base to the crown; taking the
# crown's own median depth would tilt it back by the thickness of the hair.
add_bone("head", body_point(0, head_z), point_at(0, body_depth_at(head_z), top_z) if chibi_head_detected else body_point(0, top_z), "neck")

for side_name, sign in (("left", 1), ("right", -1)):
    hand_landmark_diagnostics[side_name] = {
        "method": "proportional_fallback", "cross_section_samples": 0,
        "elbow_samples": 0, "confidence": "low",
    }
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

        # Find the wrist at the narrowest robust cross-section between forearm
        # and palm. A fixed lateral percentile often lands inside the palm on
        # stylized meshes, which makes the hand bone start too far outboard.
        lateral_min = sorted(component_lateral)[int((len(component_lateral) - 1) * 0.60)]
        lateral_max = sorted(component_lateral)[int((len(component_lateral) - 1) * 0.88)]
        bin_width = max(height * 0.012, (lateral_max - lateral_min) / 32.0)
        wrist_candidates = []
        for bin_index in range(32):
            low = lateral_min + (lateral_max - lateral_min) * bin_index / 32.0
            high = min(lateral_max, low + bin_width)
            band = [point for point, lateral in zip(arm_region, component_lateral) if low <= lateral <= high]
            if len(band) < 8:
                continue
            depths = sorted((point[forward_index] - center[forward_index]) * forward_sign for point in band)
            heights = sorted(point.z for point in band)
            q10 = lambda values: values[int((len(values) - 1) * 0.10)]
            q90 = lambda values: values[int((len(values) - 1) * 0.90)]
            depth_span = q90(depths) - q10(depths)
            vertical_span = q90(heights) - q10(heights)
            # Reject undersampled slices that look artificially narrow.
            area_proxy = max(depth_span, height * 0.006) * max(vertical_span, height * 0.006)
            lateral_center = sorted([lateral for lateral in component_lateral if low <= lateral <= high])[len(band) // 2]
            wrist_candidates.append((area_proxy, lateral_center, len(band), depth_span, vertical_span))
        best_wrist = min(wrist_candidates, key=lambda item: item[0]) if wrist_candidates else None
        best_wrist_ratio = (
            (best_wrist[1] - lateral_min) / max(lateral_max - lateral_min, 1e-8)
            if best_wrist else -1.0
        )
        # A minimum at the boundary is usually a clipped fingertip/sleeve
        # sample, not a wrist. Fall back to the stable arm quantile then.
        if best_wrist and 0.10 <= best_wrist_ratio <= 0.43 and best_wrist[2] >= 12:
            wrist_area, wrist_lateral, wrist_samples, wrist_depth_span, wrist_vertical_span = best_wrist
            wrist_band = [point for point, lateral in zip(arm_region, component_lateral) if abs(lateral - wrist_lateral) <= bin_width]
            wrist_depths = sorted((point[forward_index] - center[forward_index]) * forward_sign for point in wrist_band)
            wrist_heights = sorted(point.z for point in wrist_band)
            wrist = point_at(sign * wrist_lateral, wrist_depths[len(wrist_depths) // 2], wrist_heights[len(wrist_heights) // 2])
            wrist_method = "minimum_robust_cross_section_v1"
        else:
            wrist = component_landmark(0.65)
            wrist_lateral = (wrist[side_index] - center[side_index]) * side_sign * sign
            wrist_area = wrist_depth_span = wrist_vertical_span = 0.0
            wrist_samples = best_wrist[2] if best_wrist else 0
            wrist_method = "lateral_quantile_fallback_ambiguous_cross_section"
        hand_landmark_diagnostics[side_name] = {
            "method": wrist_method,
            "wrist_position": [round(value, 5) for value in wrist],
            "wrist_lateral_ratio": round((wrist_lateral - lateral_min) / max(lateral_max - lateral_min, 1e-8), 4),
            "cross_section_samples": wrist_samples,
            "depth_span_height_ratio": round(wrist_depth_span / height, 5),
            "vertical_span_height_ratio": round(wrist_vertical_span / height, 5),
        }
        outside = max(component_lateral)
        wrist_z = wrist.z
        wrist_side = sign * abs((wrist[side_index] - center[side_index]) * side_sign)
        wrist_z2 = wrist.z
        # Place the elbow halfway along the shoulder-to-wrist lateral axis,
        # then sample depth/height from the arm surface there. A global point
        # cloud median is biased toward the hand on this connected island.
        shoulder_lateral = (shoulder[side_index] - center[side_index]) * side_sign * sign
        elbow_target = shoulder_lateral + (abs(wrist_side) - shoulder_lateral) * 0.52
        elbow_band = [
            point for point, lateral in zip(arm_region, component_lateral)
            if abs(lateral - elbow_target) <= height * 0.018
        ]
        if len(elbow_band) < 8:
            elbow_band = sorted(arm_region, key=lambda point: abs(
                (point[side_index] - center[side_index]) * side_sign * sign - elbow_target
            ))[:max(8, len(arm_region) // 100)]
        elbow_depths = sorted((point[forward_index] - center[forward_index]) * forward_sign for point in elbow_band)
        elbow_heights = sorted(point.z for point in elbow_band)
        elbow = point_at(
            sign * elbow_target,
            elbow_depths[len(elbow_depths) // 2],
            elbow_heights[len(elbow_heights) // 2],
        )
        hand_landmark_diagnostics[side_name]["elbow_position"] = [round(value, 5) for value in elbow]
        hand_landmark_diagnostics[side_name]["elbow_samples"] = len(elbow_band)
        elbow_side = sign * abs((elbow[side_index] - center[side_index]) * side_sign)
        elbow_z = elbow.z
    # Put the shoulder, elbow and wrist on the measured arm axis. The fixed
    # chest-height shoulder landmark can sit far below an extended arm, which
    # tilts the upper-arm bone and makes the sleeve fold around the wrong pivot.
    traced = trace_arm_axis(sign, outside)
    if traced:
        height_at, depth_at, axis_samples, arm_radius = traced
        shoulder_lateral_value = abs((shoulder[side_index] - center[side_index]) * side_sign)
        elbow_lateral_value = abs((elbow[side_index] - center[side_index]) * side_sign)
        wrist_lateral_value = abs((wrist[side_index] - center[side_index]) * side_sign)
        previous = {"shoulder": [round(v, 5) for v in shoulder], "elbow": [round(v, 5) for v in elbow], "wrist": [round(v, 5) for v in wrist]}
        shoulder = point_at(sign * shoulder_lateral_value, depth_at(shoulder_lateral_value), height_at(shoulder_lateral_value))
        elbow = point_at(sign * elbow_lateral_value, depth_at(elbow_lateral_value), height_at(elbow_lateral_value))
        wrist = point_at(sign * wrist_lateral_value, depth_at(wrist_lateral_value), height_at(wrist_lateral_value))
        elbow_z, wrist_z = elbow.z, wrist.z
        arm_axis_diagnostics[side_name] = {
            "method": "traced_arm_axis_v1", "slices": len(axis_samples),
            "samples_per_slice_min": min(item[3] for item in axis_samples),
            "slices_detail": [[round(v, 4) for v in item] for item in axis_samples],
            "arm_radius": round(arm_radius, 5),
            "previous_landmarks": previous,
            "shoulder": [round(v, 5) for v in shoulder], "elbow": [round(v, 5) for v in elbow], "wrist": [round(v, 5) for v in wrist],
        }
    else:
        arm_axis_diagnostics[side_name] = {"method": "proportional_fallback", "slices": 0}
    # The collarbone runs from the base of the neck out to the shoulder joint.
    # Starting it at chest height made a long near-vertical bone whose rotation
    # swung the whole shoulder around a pivot far below the arm.
    if arm_axis_diagnostics.get(side_name, {}).get("method") == "traced_arm_axis_v1":
        clavicle_head = body_point(sign * torso_width * 0.14, (neck_z + shoulder.z) / 2)
    else:
        clavicle_head = body_point(sign * torso_width * 0.43, chest_z)
    add_bone(side_name + "Shoulder", clavicle_head, shoulder, "chest")
    add_bone(side_name + "UpperArm", shoulder, elbow, side_name + "Shoulder")
    add_bone(side_name + "LowerArm", elbow, wrist, side_name + "UpperArm")
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
    # End the Hand bone at the knuckle line (mean finger root), not at the
    # outermost fingertip. A wrist-to-fingertip Hand bone crosses the whole
    # palm and makes the wrist/hand mapping look offset in the viewer.
    hand = sum((track[0] for track in finger_tracks), Vector()) / max(1, len(finger_tracks))
    hand_landmark_diagnostics[side_name]["palm_center"] = [round(value, 5) for value in hand]
    add_bone(side_name + "Hand", wrist, hand, side_name + "LowerArm")
    for finger_name, track in zip(("Index", "Middle", "Ring", "Little"), finger_tracks):
        base, knuckle, middle, tip = track
        prefix = side_name + finger_name
        add_bone(prefix + "Proximal", base, knuckle, side_name + "Hand")
        add_bone(prefix + "Intermediate", knuckle, middle, prefix + "Proximal")
        add_bone(prefix + "Distal", middle, tip, prefix + "Intermediate")
    # Anchor and scale the thumb from the measured palm/finger geometry. Using
    # fractions of whole-body height made the thumb much longer than this
    # character's actual fingers and pushed its tip outside the hand.
    index_base, _index_knuckle, _index_middle, index_tip = finger_tracks[0]
    little_base = finger_tracks[-1][0]
    finger_axis = (index_tip - index_base).normalized()
    spread_axis = (little_base - index_base).normalized()
    if finger_axis.length_squared < 1e-8:
        finger_axis = (hand - wrist).normalized()
    if spread_axis.length_squared < 1e-8:
        spread_axis = Vector((0, 0, 1))
    thumb_out = -spread_axis
    measured_finger_length = (index_tip - index_base).length
    if measured_finger_length < height * 0.006:
        measured_finger_length = (hand - wrist).length * 0.42
    measured_finger_length = max(height * 0.008, min(measured_finger_length, height * 0.035))
    thumb_base = index_base + thumb_out * (measured_finger_length * 0.08)
    thumb_mid = thumb_base + finger_axis * (measured_finger_length * 0.28) + thumb_out * (measured_finger_length * 0.28)
    thumb_tip = thumb_mid + finger_axis * (measured_finger_length * 0.42) + thumb_out * (measured_finger_length * 0.18)
    thumb_end = thumb_tip + finger_axis * (measured_finger_length * 0.30) + thumb_out * (measured_finger_length * 0.06)
    add_bone(side_name + "ThumbMetacarpal", thumb_base, thumb_mid, side_name + "Hand")
    add_bone(side_name + "ThumbProximal", thumb_mid, thumb_tip, side_name + "ThumbMetacarpal")
    add_bone(side_name + "ThumbDistal", thumb_tip, thumb_end, side_name + "ThumbProximal")
    hip = body_point(sign * hip_half, hip_z)
    knee_z = zmin + height * 0.27
    ankle_z = zmin + height * 0.055

    def leg_slice_landmark(target_z, tolerance):
        band = []
        for point in points:
            lateral = (point[side_index] - center[side_index]) * side_sign * sign
            if lateral >= max(height * 0.018, hip_half * 0.18) and abs(point.z - target_z) <= tolerance:
                band.append((point, lateral, (point[forward_index] - center[forward_index]) * forward_sign))
        if len(band) < 8:
            return None
        lateral_values = sorted(item[1] for item in band)
        forward_values = sorted(item[2] for item in band)
        z_values = sorted(item[0].z for item in band)
        middle = (len(band) - 1) // 2
        return point_at(
            sign * lateral_values[middle],
            forward_values[middle],
            z_values[middle],
        ), len(band)

    knee_sample = leg_slice_landmark(knee_z, height * 0.035)
    ankle_sample = leg_slice_landmark(ankle_z, height * 0.025)
    knee = knee_sample[0] if knee_sample else body_point(sign * knee_half, knee_z)
    ankle = ankle_sample[0] if ankle_sample else body_point(sign * ankle_half, ankle_z)

    foot_points = []
    for point in points:
        lateral = (point[side_index] - center[side_index]) * side_sign * sign
        if lateral >= max(height * 0.018, hip_half * 0.18) and zmin + height * 0.008 <= point.z <= zmin + height * 0.12:
            forward = (point[forward_index] - center[forward_index]) * forward_sign
            foot_points.append((point, lateral, forward))
    if len(foot_points) >= 8:
        front_threshold = sorted(item[2] for item in foot_points)[int((len(foot_points) - 1) * 0.88)]
        ball_threshold = sorted(item[2] for item in foot_points)[int((len(foot_points) - 1) * 0.58)]
        toe_band = [item for item in foot_points if item[2] >= front_threshold]
        ball_band = [item for item in foot_points if ball_threshold <= item[2] < front_threshold]
        toe = point_at(
            sign * sorted(item[1] for item in toe_band)[(len(toe_band) - 1) // 2],
            sorted(item[2] for item in toe_band)[(len(toe_band) - 1) // 2],
            sorted(item[0].z for item in toe_band)[(len(toe_band) - 1) // 2],
        )
        ball = point_at(
            sign * sorted(item[1] for item in ball_band)[(len(ball_band) - 1) // 2],
            sorted(item[2] for item in ball_band)[(len(ball_band) - 1) // 2],
            sorted(item[0].z for item in ball_band)[(len(ball_band) - 1) // 2],
        )
        foot_method = "mesh_low_front_quartile_v1"
    else:
        toe = body_point(sign * ankle_half, zmin + height * 0.035, height * 0.12)
        ball = ankle.lerp(toe, 0.72)
        foot_method = "proportional_fallback"
    leg_landmark_diagnostics[side_name] = {
        "knee_method": "mesh_cross_section_median" if knee_sample else "proportional_fallback",
        "knee_samples": knee_sample[1] if knee_sample else 0,
        "knee_position": [round(value, 5) for value in knee],
        "ankle_method": "mesh_cross_section_median" if ankle_sample else "proportional_fallback",
        "ankle_samples": ankle_sample[1] if ankle_sample else 0,
        "ankle_position": [round(value, 5) for value in ankle],
        "foot_method": foot_method,
        "foot_samples": len(foot_points),
        "ball_position": [round(value, 5) for value in ball],
        "toe_position": [round(value, 5) for value in toe],
    }
    add_bone(side_name + "UpperLeg", hip, knee, "hips")
    add_bone(side_name + "LowerLeg", knee, ankle, side_name + "UpperLeg")
    add_bone(side_name + "Foot", ankle, ball, side_name + "LowerLeg")
    add_bone(side_name + "Toes", ball, toe, side_name + "Foot")

# Deform helpers add a controllable twist/volume correction inside long limb
# segments. They remain separate from the anatomical chain and require an
# animation or manual rotation to have an effect.
limb_twist_bones = {}
for segment_name in (
    "leftUpperArm", "leftLowerArm", "rightUpperArm", "rightLowerArm",
    "leftUpperLeg", "leftLowerLeg", "rightUpperLeg", "rightLowerLeg",
):
    segment = next((item for item in bones if item[0] == segment_name), None)
    if not segment:
        continue
    _name, head, tail, _parent = segment
    twist_name = segment_name + "Twist"
    add_bone(twist_name, head.lerp(tail, 0.28), head.lerp(tail, 0.72), segment_name)
    limb_twist_bones[twist_name] = {
        "parent": segment_name,
        "confidence": "medium" if (tail - head).length >= height * 0.075 else "low",
        "method": "deform_helper_on_measured_limb_segment",
        "note": "Control deformable manual; no distribuye torsión automáticamente.",
    }


def profile_sample_count(z):
    return sum(1 for point in points if abs(point.z - z) <= height * 0.018)


bone_landmark_diagnostics = {}


def record_landmark(name, position, method, sample_count, confidence, note=None):
    bone_landmark_diagnostics[name] = {
        "position": [round(float(value), 5) for value in position],
        "method": method,
        "sample_count": int(sample_count),
        "confidence": confidence,
    }
    if note:
        bone_landmark_diagnostics[name]["note"] = note


for name, z in (("hips", hip_z), ("spine", spine_z), ("chest", chest_z), ("neck", neck_z), ("head", head_z)):
    samples = profile_sample_count(z)
    profile_index = min(profile_bins - 1, max(0, int(((z - mins[2]) / height) * profile_bins)))
    profile_ok = bool(profile[profile_index]["width"])
    confidence = "medium" if samples >= 12 and profile_ok else "low"
    method = "vertical_silhouette_profile" if profile_ok else "proportional_fallback"
    bone = next((item for item in bones if item[0] == name), None)
    record_landmark(name, bone[1] if bone else body_point(0, z), method, samples, confidence)

for side in ("left", "right"):
    arm = hand_landmark_diagnostics.get(side, {})
    leg = leg_landmark_diagnostics.get(side, {})
    component_count = arm_component_sizes.get(side, 0)
    elbow_samples = arm.get("elbow_samples", 0)
    wrist_confidence = "medium" if arm.get("method") == "minimum_robust_cross_section_v1" and arm.get("cross_section_samples", 0) >= 12 else "low"
    for name, position, method, samples, confidence in (
        (side + "Shoulder", next((item[1] for item in bones if item[0] == side + "Shoulder"), body_point(0, shoulder_z)), "torso_width_and_arm_component", component_count, "medium" if component_count >= 80 else "low"),
        (side + "UpperArm", next((item[2] for item in bones if item[0] == side + "UpperArm"), body_point(0, shoulder_z)), "measured_arm_cross_section", elbow_samples, "medium" if elbow_samples >= 12 else "low"),
        (side + "LowerArm", next((item[2] for item in bones if item[0] == side + "LowerArm"), body_point(0, shoulder_z)), arm.get("method", "proportional_fallback"), arm.get("cross_section_samples", 0), wrist_confidence),
        (side + "Hand", next((item[2] for item in bones if item[0] == side + "Hand"), body_point(0, shoulder_z)), "palm_and_finger_tracks", hand_finger_track_counts.get(side, {}).get("candidate_vertices", 0), hand_finger_separation.get(side, {}).get("confidence", "low")),
    ):
        record_landmark(name, position, method, samples, confidence)

    for name, position_key, method_key, sample_key in (
        (side + "UpperLeg", "knee_position", "knee_method", "knee_samples"),
        (side + "LowerLeg", "ankle_position", "ankle_method", "ankle_samples"),
        (side + "Foot", "ball_position", "foot_method", "foot_samples"),
        (side + "Toes", "toe_position", "foot_method", "foot_samples"),
    ):
        position = leg.get(position_key, [0, 0, 0])
        samples = leg.get(sample_key, 0)
        method = leg.get(method_key, "proportional_fallback")
        confidence = "medium" if samples >= 12 and "fallback" not in method else "low"
        record_landmark(name, position, method, samples, confidence)
    # Reconstruct the reported finger landmarks from the already-created bone
    # endpoints so the reviewer can inspect every falange from the bone list.
    for digit in ("Index", "Middle", "Ring", "Little", "Thumb"):
        prefix = side + digit
        names = [prefix + suffix for suffix in ("Proximal", "Intermediate", "Distal")]
        if digit == "Thumb":
            names = [side + "ThumbMetacarpal", side + "ThumbProximal", side + "ThumbDistal"]
        for bone_name in names:
            bone = next((item for item in bones if item[0] == bone_name), None)
            if not bone:
                continue
            finger_confidence = hand_finger_separation.get(side, {}).get("confidence", "low")
            if hand_finger_layout_by_side.get(side) != "mesh_point_cloud":
                finger_confidence = "low"
            record_landmark(bone_name, bone[1], "measured_hand_track" if finger_confidence != "low" else "proportional_or_ambiguous_hand_track", hand_finger_track_counts.get(side, {}).get("candidate_vertices", 0), finger_confidence)
for name, info in limb_twist_bones.items():
    twist = next(item for item in bones if item[0] == name)
    record_landmark(name, twist[1].lerp(twist[2], 0.5), info["method"], 0, info["confidence"], info["note"])

arm_data = bpy.data.armatures.new("MeshingUp_AutoRig")
arm_obj = bpy.data.objects.new("MeshingUp_AutoRig", arm_data)
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
            proxy = bpy.data.objects.new(obj.name + "_MeshingUp_WeightProxy", proxy_mesh)
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
            modifier = source_obj.modifiers.new("MeshingUp AutoRig", "ARMATURE")
            modifier.object = arm_obj

        weight_proxy_stats["objects"] = len(proxies)
        return True
    except Exception as exc:
        weight_proxy_stats["failure"] = str(exc)
        print("MESHINGUP_AUTORIG_PROXY_WEIGHT_FALLBACK: " + str(exc), file=sys.stderr)
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
    print("MESHINGUP_AUTORIG_WEIGHT_FALLBACK: " + str(exc), file=sys.stderr)
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
                or candidate["parent"] == segment["name"]
                or segment["parent"] == candidate["name"]
                or (candidate["parent"] == segment["parent"] and (candidate["name"].endswith("Twist") or segment["name"].endswith("Twist")))
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
                modifier = obj.modifiers.new("MeshingUp AutoRig", "ARMATURE")
                modifier.object = arm_obj


def correct_head_and_arm_weights():
    """Apply conservative head and torso/arm corrections to skin weights."""
    bone_by_name = {name: (head, tail) for name, head, tail, _parent in bones}
    arm_names = [name for name in bone_by_name if name.endswith((
        "Shoulder", "UpperArm", "LowerArm", "Hand", "IndexProximal", "IndexIntermediate", "IndexDistal",
        "MiddleProximal", "MiddleIntermediate", "MiddleDistal", "RingProximal", "RingIntermediate",
        "RingDistal", "LittleProximal", "LittleIntermediate", "LittleDistal", "ThumbMetacarpal",
        "ThumbProximal", "ThumbDistal", "UpperArmTwist", "LowerArmTwist",
    ))]
    torso_names = [name for name in ("hips", "spine", "chest", "neck") if name in bone_by_name]
    head_corrected = 0
    arm_plane_corrected = 0
    hand_corrected = {"left": 0, "right": 0}
    wrist_stats = {
        side: {"sampled_vertices": 0, "corrected_vertices": 0, "hand_weight_sum": 0.0, "lower_arm_weight_sum": 0.0}
        for side in ("left", "right")
    }
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
    digit_suffixes = (
        "IndexProximal", "IndexIntermediate", "IndexDistal",
        "MiddleProximal", "MiddleIntermediate", "MiddleDistal",
        "RingProximal", "RingIntermediate", "RingDistal",
        "LittleProximal", "LittleIntermediate", "LittleDistal",
        "ThumbMetacarpal", "ThumbProximal", "ThumbDistal",
    )
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

            # Blend between forearm and palm using their geometric distances.
            # This avoids the previous hard 65% hand floor, which cut across
            # the wrist and made the sleeve/palm snap when the wrist rotated.
            for side, side_direction in (("left", 1), ("right", -1)):
                hand_name = side + "Hand"
                hand_index = next((index for index, group in groups_by_index.items() if group.name == hand_name), None)
                lower_name = side + "LowerArm"
                lower_index = next((index for index, group in groups_by_index.items() if group.name == lower_name), None)
                if hand_index is None or lower_index is None:
                    continue
                hand_head, hand_tail = bone_by_name[hand_name]
                lower_head, lower_tail = bone_by_name[lower_name]
                hand_distance = segment_distance(point, hand_head, hand_tail)
                lower_distance = segment_distance(point, lower_head, lower_tail)
                if min(hand_distance, lower_distance) > height * 0.05:
                    continue
                stats = wrist_stats[side]
                digit_indices = {
                    index for index, group in groups_by_index.items()
                    if group.name.startswith(side) and group.name.endswith(digit_suffixes)
                }
                stats["sampled_vertices"] += 1
                blend = max(0.0, min(1.0, 0.5 + (lower_distance - hand_distance) / max(height * 0.04, 1e-8)))
                target_hand = blend * blend * (3.0 - 2.0 * blend)
                # Keep existing finger influence, but always allocate the
                # remaining weight between Hand and LowerArm. Skipping these
                # vertices when fingers had >35% influence left a hole in the
                # wrist transition, especially on this mesh's connected palm.
                digit_weights = {index: weights[index] for index in digit_indices if weights.get(index, 0.0) > 1e-8}
                digit_total = min(1.0, sum(digit_weights.values()))
                wrist_total = 1.0 - digit_total
                target_hand *= wrist_total
                target_lower = wrist_total - target_hand
                for index in list(weights):
                    if index not in digit_indices:
                        weights.pop(index, None)
                weights[lower_index] = target_lower
                weights[hand_index] = target_hand
                hand_corrected[side] += 1
                stats["corrected_vertices"] += 1
                stats["hand_weight_sum"] += target_hand
                stats["lower_arm_weight_sum"] += target_lower

            total = sum(weights.values())
            if total > 1e-8 and weights != {index: weight for index, weight in existing}:
                for index, _weight in existing:
                    groups_by_index[index].remove([vertex.index])
                for index, weight in weights.items():
                    groups_by_index[index].add([vertex.index], weight / total, "REPLACE")
    return {
        "method": "distance_blended_wrist_weights_v2_preserve_digits",
        "head_corrected_vertex_count": head_corrected,
        "torso_arm_plane_corrected_vertex_count": arm_plane_corrected,
        "hand_region_corrected_vertex_count": hand_corrected,
        "wrist_weight_transition": "smoothstep_between_Hand_and_LowerArm_segment_distances",
        "wrist_transition_half_width_height_ratio": 0.02,
        "wrist_weight_statistics": {
            side: {
                **{key: value for key, value in stats.items() if not key.endswith("_sum")},
                "mean_hand_weight": round(stats["hand_weight_sum"] / max(1, stats["sampled_vertices"]), 4),
                "mean_lower_arm_weight": round(stats["lower_arm_weight_sum"] / max(1, stats["sampled_vertices"]), 4),
            }
            for side, stats in wrist_stats.items()
        },
        "upper_region_min_height_ratio": round((upper_limit - zmin) / height, 4),
        "torso_arm_plane_half_width_height_ratio": round(arm_plane / height, 4),
        "shoulder_weights_preserved": True,
    }


def coincident_vertex_groups(obj):
    """Group seam duplicates without modifying the source topology."""
    tolerance = max(height * 1e-6, 1e-9)
    tree = KDTree(len(obj.data.vertices))
    positions = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    for index, point in enumerate(positions):
        tree.insert(point, index)
    tree.balance()
    assigned = set()
    groups = []
    for index, point in enumerate(positions):
        if index in assigned:
            continue
        group = sorted(i for _p, i, _d in tree.find_range(point, tolerance) if i not in assigned)
        assigned.update(group)
        if len(group) > 1:
            groups.append(group)
    return groups


seam_groups = {obj.name: coincident_vertex_groups(obj) for obj in meshes}

weight_constraints = correct_head_and_arm_weights()


# Hair-like geometry that hangs below the neck line. The fixed neck-height cut
# above cannot tell a lock of hair beside the shoulder from the sleeve under it,
# and the region limiter below would snap such hair to the nearest limb bone.
# So it is classified by how it connects to the mesh instead.
hair_head_vertices = set()


def assign_hanging_hair_to_head():
    """Give head weights to hair that hangs beside the body.

    Surface (geodesic) distance is measured from two seed sets: vertices that
    are already clearly head, and vertices that lie on the surface of a body
    bone (within that bone's typical radius, measured from the vertices the
    heat weights assigned to it, so thick torsos and thin fingers both work).
    Hair hangs from the scalp, so it is surface-close to the head even when it
    sits next to an arm in space; a sleeve is the opposite. A body-side vertex
    that is also much farther from its bones than their own radius is treated
    as hair too, because hair tips can touch the sleeve and would otherwise
    join the arm. This is a geometric heuristic, not semantic separation:
    review hair/clothing contact areas.
    """
    parent_by_name = {name: parent for name, _head, _tail, parent in bones}
    head_family = {"head", "neck"}
    for name in parent_by_name:
        cursor = name
        while cursor:
            if cursor == "head":
                head_family.add(name)
            cursor = parent_by_name.get(cursor)
    body_bones = [(name, head, tail) for name, head, tail, _parent in bones if name not in head_family]
    stats = {"method": "surface_distance_head_vs_body_v2", "candidate_vertices": 0, "reassigned_vertices": 0,
             "reassigned_by_surface_distance": 0, "reassigned_by_texture": 0, "kept_head_vertices": 0,
             "released_non_hair_head_vertices": 0, "bone_radius_samples": {}}
    # Zone beside and below the shoulders where a non-hair vertex cannot belong to the head.
    traced_tops = [d["shoulder"][2] for d in arm_axis_diagnostics.values() if d.get("method") == "traced_arm_axis_v1"]
    arm_zone_top = (max(traced_tops) if traced_tops else shoulder_z) + height * 0.07

    def segment_distance(point, start, end):
        direction = end - start
        t = max(0.0, min(1.0, (point - start).dot(direction) / max(direction.length_squared, 1e-12)))
        return (point - (start + direction * t)).length

    for obj in meshes:
        groups = {index: group for index, group in enumerate(obj.vertex_groups)}
        head_index = next((index for index, group in groups.items() if group.name == "head"), None)
        if head_index is None or not body_bones:
            continue
        family_indices = {index for index, group in groups.items() if group.name in head_family}
        bone_geometry = {name: (head, tail) for name, head, tail in body_bones}
        count = len(obj.data.vertices)
        points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
        representative = list(range(count))
        for indices in seam_groups[obj.name]:
            for index in indices:
                representative[index] = indices[0]
        adjacency = {}
        for edge in obj.data.edges:
            first, second = representative[edge.vertices[0]], representative[edge.vertices[1]]
            if first != second:
                length = (points[first] - points[second]).length
                adjacency.setdefault(first, []).append((second, length))
                adjacency.setdefault(second, []).append((first, length))
        dominant = {}
        head_weight = {}
        for vertex in obj.data.vertices:
            if representative[vertex.index] != vertex.index:
                continue
            weights = {item.group: item.weight for item in vertex.groups if item.weight > 1e-8}
            head_weight[vertex.index] = weights.get(head_index, 0.0)
            if weights:
                dominant[vertex.index] = max(weights, key=weights.get)
        # Typical surface radius of each body bone: 30th percentile (x1.25) of the
        # distance from the vertices it dominates (robust to contamination).
        samples = {}
        for index, group_index in dominant.items():
            name = groups[group_index].name
            if name in bone_geometry:
                samples.setdefault(name, []).append(segment_distance(points[index], *bone_geometry[name]))
        radius = {}
        for name, values in samples.items():
            if len(values) >= 12:
                values.sort()
                # Low percentile: hair resting on a limb can be heat-weighted to
                # it and would inflate a high-percentile radius.
                radius[name] = max(values[int((len(values) - 1) * 0.30)] * 1.25, height * 0.004)
        stats["bone_radius_samples"] = {name: len(values) for name, values in samples.items() if name in radius}
        if not radius:
            continue
        # Bones that dominate too few vertices (e.g. a limb whose skin the twist
        # helpers took over) get the typical radius instead of none or an
        # inflated one.
        typical = sorted(radius.values())[len(radius) // 2]
        for name in bone_geometry:
            radius.setdefault(name, typical)
        normalized_distance = {}
        for index in dominant:
            normalized_distance[index] = min(
                segment_distance(points[index], *bone_geometry[name]) / radius[name] for name in radius
            )
        reach, nearest_source, heap = {}, {}, []
        for index, group_index in dominant.items():
            if head_weight[index] > 0.9:
                nearest_source[index] = "head"
            elif group_index not in family_indices and normalized_distance[index] <= 1.0:
                nearest_source[index] = "body"
            else:
                continue
            reach[index] = 0.0
            heapq.heappush(heap, (0.0, index))
        while heap:
            travelled, index = heapq.heappop(heap)
            if travelled > reach.get(index, float("inf")):
                continue
            for other, length in adjacency.get(index, ()):
                candidate = travelled + length
                if candidate < reach.get(other, float("inf")):
                    reach[other] = candidate
                    nearest_source[other] = nearest_source[index]
                    heapq.heappush(heap, (candidate, other))
        for vertex in obj.data.vertices:
            rep = representative[vertex.index]
            if rep not in dominant:
                continue
            texture_mask = hair_texture_masks.get(obj.name)
            if dominant[rep] == head_index:
                # The heat weights already attach this vertex to the head (for
                # example hair below the neck line). Keep it there: the region
                # limiter would otherwise swap it for the nearest limb bone.
                # With a texture mask, a non-hair vertex out beside the
                # shoulders (sleeve the heat weights gave to the head) is released.
                point = points[rep]
                beside_shoulder = (
                    abs((point[side_index] - center[side_index]) * side_sign) >= shoulder_half * 0.95 and point.z <= arm_zone_top
                )
                if texture_mask is not None and not texture_mask[vertex.index] and beside_shoulder:
                    stats["released_non_hair_head_vertices"] += 1
                    continue
                hair_head_vertices.add((obj.name, vertex.index))
                stats["kept_head_vertices"] += 1
                continue
            if dominant[rep] in family_indices:
                continue
            stats["candidate_vertices"] += 1
            by_texture = texture_mask is not None and bool(texture_mask[vertex.index])
            by_surface = texture_mask is None and nearest_source.get(rep) == "head"
            if not (by_texture or by_surface):
                continue
            for item in list(vertex.groups):
                groups[item.group].remove([vertex.index])
            groups[head_index].add([vertex.index], 1.0, "REPLACE")
            hair_head_vertices.add((obj.name, vertex.index))
            stats["reassigned_vertices"] += 1
            stats["reassigned_by_texture" if by_texture else "reassigned_by_surface_distance"] += 1
    stats["note"] = "Heurística geométrica: el pelo que cuelga junto a brazos o torso se asigna a la cabeza; revisa los contactos pelo/ropa."
    return stats


hair_head_weights = assign_hanging_hair_to_head()


local_bones_by_vertex = {}


def constrain_weights_to_local_bone_region():
    """Prune skin influence from bones outside the nearest joint neighborhood."""
    parent_by_name = {name: parent for name, _head, _tail, parent in bones}
    children_by_name = {name: set() for name, _head, _tail, _parent in bones}
    for name, parent in parent_by_name.items():
        if parent in children_by_name:
            children_by_name[parent].add(name)
    segments = [(name, head, tail, parent) for name, head, tail, parent in bones]
    corrected_vertices = 0
    removed_weight = 0.0
    total_vertices = 0
    local_bones_by_vertex.clear()
    head_segment_index = next((i for i, segment in enumerate(segments) if segment[0] == "head"), None)

    def distance(point, start, end):
        direction = end - start
        t = max(0.0, min(1.0, (point - start).dot(direction) / max(direction.length_squared, 1e-12)))
        return (point - (start + direction * t)).length

    def allowed_names(nearest_name):
        allowed = {nearest_name, parent_by_name.get(nearest_name), *children_by_name.get(nearest_name, set())}
        parent = parent_by_name.get(nearest_name)
        # A twist helper and the segment siblings it refines form one local
        # deformation neighborhood; unrelated branches never share weights.
        if nearest_name.endswith("Twist") and parent:
            allowed.update(children_by_name.get(parent, set()))
        else:
            for child in children_by_name.get(nearest_name, set()):
                if child.endswith("Twist"):
                    allowed.add(child)
        return {name for name in allowed if name}

    for obj in meshes:
        groups = {index: group.name for index, group in enumerate(obj.vertex_groups)}
        group_index = {name: index for index, name in groups.items()}
        for vertex in obj.data.vertices:
            total_vertices += 1
            point = obj.matrix_world @ vertex.co
            distances = [distance(point, head, tail) for _name, head, tail, _parent in segments]
            nearest_index = min(range(len(segments)), key=distances.__getitem__)
            allowed = allowed_names(segments[nearest_index][0])
            if (obj.name, vertex.index) in hair_head_vertices and head_segment_index is not None:
                # Hair hanging beside limbs stays in the head neighbourhood.
                nearest_index = head_segment_index
                allowed = allowed_names("head")
            local_bones_by_vertex[(obj.name, vertex.index)] = frozenset(allowed)
            original = {item.group: item.weight for item in vertex.groups if item.weight > 1e-10}
            kept = {index: value for index, value in original.items() if groups.get(index) in allowed}
            removed = sum(value for index, value in original.items() if groups.get(index) not in allowed)
            if removed <= 1e-6:
                continue
            removed_weight += removed
            if not kept:
                nearest_group = group_index.get(segments[nearest_index][0])
                if nearest_group is None:
                    continue
                kept = {nearest_group: 1.0}
            # glTF skinning uses four influences; keep the strongest local
            # influences and renormalize after removing distant regions.
            strongest = sorted(kept.items(), key=lambda item: item[1], reverse=True)[:4]
            total = sum(value for _index, value in strongest) or 1.0
            for index in original:
                if index not in {key for key, _value in strongest}:
                    obj.vertex_groups[index].remove([vertex.index])
            for index, value in strongest:
                obj.vertex_groups[index].add([vertex.index], value / total, "REPLACE")
            corrected_vertices += 1
    return {
        "method": "nearest_segment_topology_neighborhood_v1",
        "vertex_count": total_vertices,
        "corrected_vertex_count": corrected_vertices,
        "corrected_vertex_ratio": round(corrected_vertices / max(total_vertices, 1), 5),
        "removed_weight_sum": round(removed_weight, 5),
        "max_influences": 4,
        "note": "Los pesos se limitaron al hueso de segmento más cercano y a su vecindad padre/hijo; requiere revisión en solapes reales.",
    }


weight_region_validation = constrain_weights_to_local_bone_region()


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




def synchronize_seam_weights():
    """Keep duplicated UV/normal seam vertices moving together."""
    changed = 0
    for obj in meshes:
        for indices in seam_groups[obj.name]:
            weights = {}
            for index in indices:
                for element in obj.data.vertices[index].groups:
                    weights[element.group] = weights.get(element.group, 0.0) + element.weight
            total = sum(weights.values())
            if total <= 1e-10:
                continue
            normalized = {index: value / total for index, value in weights.items() if value > 0}
            for index in indices:
                vertex = obj.data.vertices[index]
                original = {item.group: item.weight for item in vertex.groups}
                if any(abs(original.get(group, 0) - value) > 1e-6 for group, value in normalized.items()):
                    changed += 1
                for group in original:
                    obj.vertex_groups[group].remove([index])
                for group, value in normalized.items():
                    obj.vertex_groups[group].add([index], value, 'REPLACE')
    return {"method": "coincident_seam_weights_v1", "group_count": sum(map(len, seam_groups.values())),
            "changed_vertex_count": changed, "tolerance": max(height * 1e-6, 1e-9)}


weight_coverage = ensure_complete_vertex_weights()
seam_weight_repair = synchronize_seam_weights()

WEIGHT_SMOOTH_ITERATIONS = 80
WEIGHT_SMOOTH_FACTOR = 0.5


def smooth_weights_over_topology(iterations=WEIGHT_SMOOTH_ITERATIONS, factor=WEIGHT_SMOOTH_FACTOR):
    """Blend skin weights along mesh edges so bone boundaries become gradients.

    The nearest-segment limiter above keeps each vertex inside its bone
    neighbourhood but cuts weights abruptly where two neighbourhoods meet,
    which tears edges when a joint rotates. Smoothing runs on welded vertices
    (UV/normal seam duplicates share one entry, so seams cannot open) and a
    vertex may only receive bones from its own local neighbourhood, so hair or
    torso weights never leak into limbs. Results keep at most four influences.
    """
    changed_vertices = 0
    for obj in meshes:
        group_names = {group.index: group.name for group in obj.vertex_groups}
        vertex_count = len(obj.data.vertices)
        representative = list(range(vertex_count))
        for indices in seam_groups[obj.name]:
            for index in indices:
                representative[index] = indices[0]
        members = {}
        for index in range(vertex_count):
            members.setdefault(representative[index], []).append(index)
        neighbours = {rep: set() for rep in members}
        for edge in obj.data.edges:
            first, second = representative[edge.vertices[0]], representative[edge.vertices[1]]
            if first != second:
                neighbours[first].add(second)
                neighbours[second].add(first)
        weights = {}
        allowed = {}
        for rep in members:
            weights[rep] = {item.group: item.weight for item in obj.data.vertices[rep].groups if item.weight > 1e-10}
            names = local_bones_by_vertex.get((obj.name, rep))
            allowed[rep] = None if names is None else {index for index, name in group_names.items() if name in names}
        for _ in range(iterations):
            updated = {}
            for rep, own in weights.items():
                around = neighbours[rep]
                if not around:
                    updated[rep] = own
                    continue
                mixed = {index: value * (1.0 - factor) for index, value in own.items()}
                share = factor / len(around)
                limit = allowed[rep]
                for other in around:
                    for index, value in weights[other].items():
                        if limit is None or index in limit:
                            mixed[index] = mixed.get(index, 0.0) + value * share
                strongest = sorted(((i, v) for i, v in mixed.items() if v > 0.005), key=lambda item: item[1], reverse=True)[:4]
                total = sum(v for _i, v in strongest)
                updated[rep] = {i: v / total for i, v in strongest} if total > 1e-10 else own
            weights = updated
        for rep, final in weights.items():
            for index in members[rep]:
                vertex = obj.data.vertices[index]
                current = {item.group: item.weight for item in vertex.groups}
                if any(abs(current.get(group, 0.0) - final.get(group, 0.0)) > 1e-6 for group in set(current) | set(final)):
                    changed_vertices += 1
                for group in current:
                    if group not in final:
                        obj.vertex_groups[group].remove([index])
                for group, value in final.items():
                    obj.vertex_groups[group].add([index], value, "REPLACE")
    return {
        "method": "topology_laplacian_local_bone_weights_v1",
        "iterations": iterations, "factor": factor,
        "changed_vertex_count": changed_vertices, "max_influences": 4,
    }


weight_smoothing = smooth_weights_over_topology()





intentional_split_vertices = {}


def separate_hair_from_clothing():
    """Cut the mesh where hair that hangs beside the body is fused to clothing.

    Hair resting on a sleeve is often sculpted as one surface with it, so a
    strip of triangles joins a lock that follows the head to a sleeve that
    follows the arm, and that strip stretches into a sliver as soon as the arm
    moves. Faces are classed by the texture-colour hair mask (hair when most of
    their vertices are hair-coloured); edges between a hair face and a clothing
    face beside and below the shoulders are split, so each side keeps its own
    vertices. Nothing moves, so the rest pose is unchanged. A vertex left on
    the hair side takes head weights; one left on the clothing side takes the
    weights of the nearest clothing vertex. Needs the texture mask.
    """
    stats = {"method": "split_hair_clothing_boundary_texture_v1", "split_edges": 0, "new_vertices": 0, "reweighted_vertices": 0}
    if not hair_texture_masks:
        return stats
    traced_tops = [d["shoulder"][2] for d in arm_axis_diagnostics.values() if d.get("method") == "traced_arm_axis_v1"]
    zone_top = (max(traced_tops) if traced_tops else shoulder_z) + height * 0.07
    zone_side = shoulder_half * 0.95
    for obj in meshes:
        mask = hair_texture_masks.get(obj.name)
        groups = {index: group for index, group in enumerate(obj.vertex_groups)}
        head_index = next((i for i, group in groups.items() if group.name == "head"), None)
        if mask is None or head_index is None:
            continue
        hair_set = {int(index) for index in mask.nonzero()[0]}
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        world = [obj.matrix_world @ vertex.co for vertex in bm.verts]

        def in_zone(point):
            return abs((point[side_index] - center[side_index]) * side_sign) >= zone_side and point.z <= zone_top

        def is_hair(face):
            return sum(1 for vertex in face.verts if vertex.index in hair_set) >= 2

        boundary = []
        for edge in bm.edges:
            if len(edge.link_faces) != 2:
                continue
            first, second = edge.link_faces
            if is_hair(first) != is_hair(second) and in_zone(world[edge.verts[0].index]) and in_zone(world[edge.verts[1].index]):
                boundary.append(edge)
        if not boundary:
            bm.free()
            continue
        # Face identities survive splitting; vertex indices do not.
        hair_faces = {face for face in bm.faces if is_hair(face)}
        original_count = len(bm.verts)
        on_cut = {vertex.index for edge in boundary for vertex in edge.verts}
        bmesh.ops.split_edges(bm, edges=boundary)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        deform = bm.verts.layers.deform.active
        hair_side, clothing_side = [], []
        for vertex in bm.verts:
            if (vertex.index not in on_cut and vertex.index < original_count) or not vertex.link_faces:
                continue
            kinds = {face in hair_faces for face in vertex.link_faces}
            if kinds == {True}:
                hair_side.append(vertex)
            elif kinds == {False}:
                clothing_side.append(vertex)
        if deform is not None:
            for vertex in hair_side:
                if set(vertex[deform].keys()) != {head_index}:
                    vertex[deform].clear()
                    vertex[deform][head_index] = 1.0
                    stats["reweighted_vertices"] += 1
            donors = [vertex for vertex in clothing_side if vertex.index < original_count and vertex.index not in hair_set]
            if donors:
                tree = KDTree(len(donors))
                for position, vertex in enumerate(donors):
                    tree.insert(world[vertex.index], position)
                tree.balance()
                for vertex in clothing_side:
                    if set(vertex[deform].keys()) == {head_index}:
                        _co, position, _dist = tree.find(obj.matrix_world @ vertex.co)
                        source = donors[position][deform]
                        vertex[deform].clear()
                        for key, value in source.items():
                            vertex[deform][key] = value
                        stats["reweighted_vertices"] += 1
        intentional_split_vertices[obj.name] = on_cut | set(range(original_count, len(bm.verts)))
        stats["split_edges"] += len(boundary)
        stats["new_vertices"] += len(bm.verts) - original_count
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
    bpy.context.view_layer.update()
    return stats


hair_clothing_separation = separate_hair_from_clothing()


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
        if name.endswith(("Shoulder", "UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg", "Foot", "Toes", "Twist")):
            # Probe each local rotation axis in both directions. This is a
            # structural stress test, not a claim that every pose is natural.
            angle = math.radians(
                20 if name.endswith("Twist")
                else 35 if name.endswith(("Shoulder", "UpperArm", "UpperLeg"))
                else 30 if name.endswith("Hand")
                else 25 if name.endswith(("Foot", "Toes"))
                else 55
            )
            for axis in range(3):
                for sign in (-1, 1):
                    pose_specs.append((name, "XYZ"[axis], [(name, axis, sign * angle)]))

    # Local-axis combinations expose failures that isolated joint tests miss.
    combo_specs = [
        ("left_arm_chain", "combo", [("leftShoulder", 0, math.radians(18)), ("leftUpperArm", 1, math.radians(-32)), ("leftLowerArm", 0, math.radians(48))]),
        ("right_arm_chain", "combo", [("rightShoulder", 0, math.radians(-18)), ("rightUpperArm", 1, math.radians(32)), ("rightLowerArm", 0, math.radians(-48))]),
        ("left_leg_chain", "combo", [("leftUpperLeg", 0, math.radians(24)), ("leftLowerLeg", 0, math.radians(38)), ("leftFoot", 1, math.radians(-12))]),
        ("right_leg_chain", "combo", [("rightUpperLeg", 0, math.radians(-24)), ("rightLowerLeg", 0, math.radians(-38)), ("rightFoot", 1, math.radians(12))]),
    ]
    pose_specs.extend(combo_specs)

    results = []
    for bone_name, axis_name, rotations in pose_specs:
        for name, axis, angle in rotations:
            pose_bone = arm_obj.pose.bones.get(name)
            if pose_bone is None:
                continue
            pose_bone.rotation_mode = "XYZ"
            rotation = [0.0, 0.0, 0.0]
            rotation[axis] = angle
            pose_bone.rotation_euler = rotation
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ratios = []
        affected_ratios = []
        seam_gap = 0.0
        for obj, edges, baseline in rest_edges:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            current = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
            skipped = intentional_split_vertices.get(obj.name, ())
            for indices in seam_groups[obj.name]:
                if skipped and any(index in skipped for index in indices):
                    continue  # the hair/clothing cut is deliberate, not a seam that opened
                anchor = indices[0]
                for index in indices[1:]:
                    gap_growth = (current[index] - current[anchor]).length - (baseline[index] - baseline[anchor]).length
                    seam_gap = max(seam_gap, gap_growth)
            for first, second in edges:
                old_length = (baseline[first] - baseline[second]).length
                if old_length <= height * 1e-7:
                    continue
                new_length = (current[first] - current[second]).length
                ratio = new_length / old_length
                ratios.append(ratio)
                if max((current[first] - baseline[first]).length, (current[second] - baseline[second]).length) > height * 1e-5:
                    affected_ratios.append(ratio)
            evaluated.to_mesh_clear()
        for name, _axis, _angle in rotations:
            pose_bone = arm_obj.pose.bones.get(name)
            if pose_bone:
                pose_bone.rotation_euler = (0.0, 0.0, 0.0)
        if ratios:
            ratios.sort()
            severe = sum(value < 0.65 or value > 1.55 for value in ratios)
            p95 = ratios[min(len(ratios) - 1, int((len(ratios) - 1) * 0.95))]
            p99 = ratios[min(len(ratios) - 1, int((len(ratios) - 1) * 0.99))]
            severe_fraction = severe / len(ratios)
            affected_severe = sum(value < 0.65 or value > 1.55 for value in affected_ratios) / max(1, len(affected_ratios))
            results.append({
                "bone": bone_name,
                "axis": axis_name,
                "degrees": [round(math.degrees(angle), 1) for _name, _axis, angle in rotations] if len(rotations) > 1 else round(math.degrees(rotations[0][2]), 1),
                "rotations": [{"bone": name, "axis": "XYZ"[axis], "degrees": round(math.degrees(angle), 1)} for name, axis, angle in rotations],
                "edge_ratio_p95": round(p95, 3),
                "edge_ratio_p99": round(p99, 3),
                "severe_edge_fraction": round(severe_fraction, 5),
                "affected_edge_count": len(affected_ratios),
                "affected_severe_edge_fraction": round(affected_severe, 5),
                "seam_gap_growth_height_ratio": round(seam_gap / height, 7),
                "passed": severe_fraction <= 0.02 and p99 <= 3.0 and affected_severe <= 0.10 and seam_gap <= height * 1e-4,
            })
    bpy.context.view_layer.update()
    failed = [item for item in results if not item["passed"]]
    return {
        "method": "blender_pose_edge_seam_stress_v5_combined_twist",
        "seam_group_count": sum(map(len, seam_groups.values())),
        "limitations": ["No detecta penetraciones entre superficies ni separación entre objetos distintos.",
                        "Las uniones medidas son vértices coincidentes en reposo; no equivalen a una revisión visual.",
                        "La prueba de muñeca rota el hueso Hand en tres ejes; no valida por sí sola la ubicación anatómica del pivote.",
                        "Las poses combinadas son estrés local por ejes XYZ, no capturas de movimiento anatómicamente validadas."],
        "pose_count": len(results),
        "passed": bool(results) and not failed,
        "failed_pose_count": len(failed),
        "poses": results,
        "warnings": [
            f"La prueba detectó deformación anómala o apertura de costuras en {len(failed)} de {len(results)} poses; revisa pesos/huesos: "
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
hand_geometry_ok = bool(hand_finger_separation) and all(
    item.get("confidence") != "low" for item in hand_finger_separation.values()
)
wrist_geometry_ok = all(
    values.get("sampled_vertices", 0) >= 12
    for values in weight_constraints.get("wrist_weight_statistics", {}).values()
)
landmark_quality_ok = all(item.get("confidence") != "low" for name, item in bone_landmark_diagnostics.items() if not name.endswith("Twist"))
weight_regions_ok = weight_region_validation["corrected_vertex_ratio"] <= 0.12
report = {
    "algorithm": "silhouette_landmarks_with_confidence_v3",
    "weighting": weights_method,
    "hand_bone_layout": "connected_arm_surface_v3",
    "hand_finger_layout_by_side": hand_finger_layout_by_side,
    "arm_component_vertex_count": arm_component_sizes,
    "hand_landmark_diagnostics": hand_landmark_diagnostics,
    "leg_landmark_diagnostics": leg_landmark_diagnostics,
    "bone_landmark_diagnostics": bone_landmark_diagnostics,
    "limb_twist_bones": limb_twist_bones,
    "hand_finger_track_detection": hand_finger_track_counts,
    "hand_finger_separation": hand_finger_separation,
    "thumb_layout_method": "palm_local_measured_finger_scale_v2",
    "forward_axis": input_forward_axis,
    "normalized_forward_axis": "-Y",
    "confidence": "medium" if shape_ok and hand_geometry_ok and wrist_geometry_ok and landmark_quality_ok and weight_regions_ok and weights_method in {"blender_automatic_weights", "blender_automatic_weights_welded_proxy"} and deformation_validation["passed"] and weight_coverage["repaired_vertex_count"] == 0 else "low",
    "deformation_validation": deformation_validation,
    "weight_constraints": weight_constraints,
    "weight_region_validation": weight_region_validation,
    "weight_smoothing": weight_smoothing,
    "hair_texture": hair_texture_stats,
    "hair_clothing_separation": hair_clothing_separation,
    "hair_head_weights": hair_head_weights,
    "weight_coverage": weight_coverage,
    "seam_weight_repair": seam_weight_repair,
    "weight_proxy": weight_proxy_stats,
    "height": round(height, 5),
    "mesh_count": len(meshes),
    "vertex_count": len(points),
    "bone_count": len(bones),
    "arm_axis": arm_axis_diagnostics,
    "bone_positions": {
        name: {"head": [round(v, 5) for v in head], "tail": [round(v, 5) for v in tail], "parent": parent}
        for name, head, tail, parent in bones
    },
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
if any(item.get("confidence") == "low" for item in hand_finger_separation.values()):
    report["warnings"].append("La geometría de una o ambas manos no muestra valles claros entre dedos; las cuatro cadenas son propuestas y requieren inspección ortográfica y ajuste manual.")
    report["warnings"].append("El orden índice-meñique se asigna por cuantiles de profundidad descendentes según la inspección de Meido; revísalo en cada modelo antes de usar gestos de mano.")
    report["warnings"].append("La confianza global se redujo porque la detección de dedos no pudo confirmar regiones anatómicas separadas.")
if not wrist_geometry_ok:
    report["warnings"].append("La geometría no aportó suficientes vértices cerca de una o ambas muñecas; verifica manualmente los pesos de palma y antebrazo.")
low_landmarks = [name for name, item in bone_landmark_diagnostics.items() if item.get("confidence") == "low" and not name.endswith("Twist")]
if low_landmarks:
    report["warnings"].append("Pivotes con evidencia geométrica baja: " + ", ".join(low_landmarks) + ". Revísalos en el visor antes de animar.")
if not weight_regions_ok:
    report["warnings"].append("Se encontraron influencias fuera de la vecindad anatómica local en más del 12% de vértices; la corrección geométrica necesita revisión visual.")
report["warnings"].append("Se añadieron controles deformables Twist para segmentos largos; son opcionales y requieren animación manual para distribuir torsión.")
if any("ambiguous_cross_section" in item.get("method", "") for item in hand_landmark_diagnostics.values()):
    report["warnings"].append("La sección geométrica de la muñeca no fue concluyente; el pivote usa el percentil 65 del brazo conectado y requiere comprobación visual contra el puño de la manga.")
report["warnings"].extend(deformation_validation["warnings"])
if weight_coverage["repaired_vertex_count"]:
    report["warnings"].append(
        f"Se reasignaron los pesos de {weight_coverage['repaired_vertex_count']} vértices sin influencia a su hueso más cercano."
    )
with open(report_path, "w", encoding="utf-8") as report_file:
    json.dump(report, report_file, ensure_ascii=False, indent=2)
print("MESHINGUP_AUTORIG_COMPLETE: " + json.dumps(report, ensure_ascii=False))
