"""Inventory disconnected GLB geometry and propose editable body-part regions."""

import bpy
import json
import sys
from mathutils import Vector


def fail(message):
    print("RIGM_PART_ANALYSIS_ERROR: " + message, file=sys.stderr)
    raise RuntimeError(message)


args = sys.argv[sys.argv.index("--") + 1:]
source_path, report_path, forward_axis = args

try:
    bpy.ops.import_scene.gltf(filepath=source_path)
except Exception as exc:
    fail("Blender no pudo importar el GLB: " + str(exc))

meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
if not meshes:
    fail("El GLB no contiene mallas analizables.")

if forward_axis not in {"-Y", "+Y", "-X", "+X"}:
    fail("El eje frontal debe ser -Y, +Y, -X o +X.")
forward_index = 1 if forward_axis in {"-Y", "+Y"} else 0
side_index = 1 - forward_index
forward_sign = -1 if forward_axis.startswith("-") else 1
points = [obj.matrix_world @ vertex.co for obj in meshes for vertex in obj.data.vertices]
if not points:
    fail("Las mallas no contienen vértices.")

mins = [min(point[index] for point in points) for index in range(3)]
maxs = [max(point[index] for point in points) for index in range(3)]
height = maxs[2] - mins[2]
if height <= 1e-8:
    fail("La geometría no tiene altura suficiente para proponer regiones humanoides.")
center = [(mins[index] + maxs[index]) * 0.5 for index in range(3)]
side_sign = {"-Y": 1, "+Y": -1, "-X": -1, "+X": 1}[forward_axis]
side_min = min((point[side_index] - center[side_index]) * side_sign for point in points)
side_max = max((point[side_index] - center[side_index]) * side_sign for point in points)
side_width = max(side_max - side_min, height * 0.05)


def region_for_point(point):
    z_ratio = (point.z - mins[2]) / height
    lateral = (point[side_index] - center[side_index]) * side_sign
    if z_ratio < 0.10:
        return "feet_or_ground_accessory"
    if z_ratio < 0.31:
        return "legs_or_lower_body"
    if z_ratio < 0.46:
        return "hips_or_torso"
    if z_ratio < 0.62 and abs(lateral) > side_width * 0.19:
        return "arms_or_hands"
    if z_ratio < 0.64:
        return "torso_or_neck"
    return "head_hair_or_face"

components = []
material_names = sorted({slot.material.name for obj in meshes for slot in obj.material_slots if slot.material})
textured_materials = []
for obj in meshes:
    for slot in obj.material_slots:
        material = slot.material
        if not material or not material.use_nodes or material.name in textured_materials:
            continue
        if any(node.type == "TEX_IMAGE" and node.image for node in material.node_tree.nodes):
            textured_materials.append(material.name)

for mesh_index, obj in enumerate(meshes):
    vertex_count = len(obj.data.vertices)
    parent = list(range(vertex_count))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for edge in obj.data.edges:
        first, second = edge.vertices
        root_a, root_b = find(first), find(second)
        if root_a != root_b:
            parent[root_b] = root_a
    members = {}
    for vertex in obj.data.vertices:
        members.setdefault(find(vertex.index), []).append(vertex.index)
    face_counts = {}
    component_materials = {}
    for polygon in obj.data.polygons:
        root = find(polygon.vertices[0])
        face_counts[root] = face_counts.get(root, 0) + 1
        component_materials.setdefault(root, set()).add(polygon.material_index)
    for root, indices in members.items():
        if not indices:
            continue
        local_points = [obj.matrix_world @ obj.data.vertices[index].co for index in indices]
        centroid = sum(local_points, Vector((0.0, 0.0, 0.0))) / len(local_points)
        z_ratio = (centroid.z - mins[2]) / height
        lateral = (centroid[side_index] - center[side_index]) * side_sign
        front_depth = (centroid[forward_index] - center[forward_index]) * forward_sign
        region = region_for_point(centroid)
        component_regions = sorted({region_for_point(point) for point in local_points})
        vertical_span_ratio = (max(point.z for point in local_points) - min(point.z for point in local_points)) / height
        side_span_ratio = (
            max(point[side_index] for point in local_points) - min(point[side_index] for point in local_points)
        ) / side_width

        face_candidate = None
        if z_ratio >= 0.64 and front_depth > 0:
            normalized_side = lateral / side_width
            if 0.72 <= z_ratio <= 0.90 and 0.025 <= abs(normalized_side) <= 0.24:
                face_candidate = "eye_candidate"
            elif 0.64 <= z_ratio < 0.79 and abs(normalized_side) <= 0.12:
                face_candidate = "mouth_candidate"
        components.append({
            "id": f"mesh{mesh_index}-part{len(components) + 1:04d}",
            "source_object": obj.name,
            "vertices": len(indices),
            "faces": face_counts.get(root, 0),
            "center": [round(float(value), 5) for value in centroid],
            "height_ratio": round(z_ratio, 4),
            "side_ratio": round(lateral / side_width, 4),
            "front_depth": round(float(front_depth), 5),
            "material_indices": sorted(component_materials.get(root, set())),
            "region_proposal": region,
            "spans_regions": component_regions,
            "requires_semantic_cut": len(component_regions) > 1,
            "vertical_span_ratio": round(vertical_span_ratio, 4),
            "side_span_ratio": round(side_span_ratio, 4),
            "facial_feature_candidate": face_candidate,
            "extractable_as_disconnected_piece": face_counts.get(root, 0) >= 1,
            "confidence": "low",
        })

components.sort(key=lambda item: (-item["vertices"], item["id"]))
region_summary = {}
for item in components:
    summary = region_summary.setdefault(item["region_proposal"], {"component_count": 0, "vertices": 0, "faces": 0})
    summary["component_count"] += 1
    summary["vertices"] += item["vertices"]
    summary["faces"] += item["faces"]
feature_summary = {}
for item in components:
    feature = item["facial_feature_candidate"]
    if feature:
        entry = feature_summary.setdefault(feature, {"component_count": 0, "vertices": 0})
        entry["component_count"] += 1
        entry["vertices"] += item["vertices"]
for feature in ("eye_candidate", "mouth_candidate"):
    feature_summary.setdefault(feature, {"component_count": 0, "vertices": 0})

warnings = [
    "Las regiones corporales se proponen por posición y conectividad; la confianza es baja y deben revisarse antes de extraer o riggear.",
    "Los ojos y la boca sólo se detectan como candidatos geométricos; si están pintados en una textura compartida no son piezas separables de la malla.",
]
if len(components) > 500:
    warnings.append("Hay muchas islas desconectadas; el informe las agrupa por región sin crear cientos de objetos en el GLB.")
cross_region_components = [item for item in components if item["requires_semantic_cut"]]
if cross_region_components:
    warnings.append(
        f"{len(cross_region_components)} piezas conectadas cruzan varias regiones espaciales; no se pueden separar sólo por islas y requieren un corte semántico revisable."
    )
if textured_materials:
    warnings.append("El GLB contiene materiales con textura: algunos rasgos faciales pueden estar en el mapa UV y no en geometría independiente.")

report = {
    "method": "connected_components_and_spatial_regions_v1",
    "source_file": source_path,
    "forward_axis": forward_axis,
    "mesh_object_count": len(meshes),
    "material_count": len(material_names),
    "material_names": material_names,
    "textured_materials": sorted(textured_materials),
    "vertex_count": len(points),
    "component_count": len(components),
    "extractable_component_count": sum(1 for item in components if item["extractable_as_disconnected_piece"]),
    "single_region_component_count": sum(1 for item in components if not item["requires_semantic_cut"]),
    "cross_region_component_count": sum(1 for item in components if item["requires_semantic_cut"]),
    "large_cross_region_components": [
        item["id"] for item in components
        if item["requires_semantic_cut"] and item["vertices"] >= 100
    ][:100],
    "region_summary": region_summary,
    "facial_feature_candidates": feature_summary,
    "components": components[:50],
    "components_truncated": max(0, len(components) - 50),
    "warnings": warnings,
}
with open(report_path, "w", encoding="utf-8") as output:
    json.dump(report, output, ensure_ascii=False, indent=2)
print("RIGM_PART_ANALYSIS_COMPLETE: " + json.dumps({key: value for key, value in report.items() if key != "components"}, ensure_ascii=False))
