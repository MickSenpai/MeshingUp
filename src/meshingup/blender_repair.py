"""Clean a static GLB before rigging: weld seam duplicates and drop dead geometry.

Runs inside Blender (``--background --python``). Arguments after ``--``:
source GLB, output GLB, report JSON, minimum island vertex count (0 = never
delete islands, only report them). The source file is never modified.
"""

import bmesh
import bpy
import json
import sys
from collections import Counter
from mathutils import Vector


def fail(message):
    print("MESHINGUP_REPAIR_ERROR: " + message, file=sys.stderr)
    raise RuntimeError(message)


args = sys.argv[sys.argv.index("--") + 1:]
source_path, output_path, report_path, min_island_arg = args
min_island_vertices = max(0, int(min_island_arg))

# --factory-startup adds a default Cube/Light/Camera that is not part of the model.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
try:
    bpy.ops.import_scene.gltf(filepath=source_path)
except Exception as exc:
    fail("Blender no pudo importar el GLB: " + str(exc))

meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
if not meshes:
    fail("El GLB no contiene mallas.")
if any(obj.type == "ARMATURE" for obj in bpy.context.scene.objects):
    fail("El GLB ya tiene un armature; la reparación sólo se aplica a modelos sin rig.")
if any(obj.data.shape_keys for obj in meshes):
    fail("El GLB usa shape keys; reparar la malla los invalidaría. Usa el modelo sin reparar.")


def world_bounds():
    points = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
    return (
        [min(point[axis] for point in points) for axis in range(3)],
        [max(point[axis] for point in points) for axis in range(3)],
    )


def island_sizes(bm):
    parent = list(range(len(bm.verts)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    bm.verts.ensure_lookup_table()
    for edge in bm.edges:
        first, second = find(edge.verts[0].index), find(edge.verts[1].index)
        if first != second:
            parent[first] = second
    return sorted(Counter(find(vertex.index) for vertex in bm.verts).values(), reverse=True)


def measure(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    sizes = island_sizes(bm)
    stats = {
        "vertices": len(bm.verts), "faces": len(bm.faces),
        "islands": len(sizes), "largest_islands": sizes[:5],
        "islands_under_20_vertices": sum(1 for size in sizes if size < 20),
        "boundary_edges": sum(1 for edge in bm.edges if edge.is_boundary),
        "non_manifold_edges": sum(1 for edge in bm.edges if not edge.is_manifold),
        "loose_vertices": sum(1 for vertex in bm.verts if not vertex.link_faces),
        "zero_area_faces": sum(1 for face in bm.faces if face.calc_area() < 1e-10),
    }
    bm.free()
    return stats


mins, maxs = world_bounds()
height = max(maxs[2] - mins[2], 1e-6)
weld_distance = max(height * 5e-6, 1e-7)

before, after, changes, warnings = {}, {}, {}, []
for obj in meshes:
    before[obj.name] = measure(obj)
    had_custom_normals = obj.data.has_custom_normals
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    merged_before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=weld_distance)
    welded = merged_before - len(bm.verts)
    zero_faces = [face for face in bm.faces if face.calc_area() < 1e-10]
    if zero_faces:
        bmesh.ops.delete(bm, geom=zero_faces, context="FACES")
    removed_islands = removed_island_vertices = 0
    if min_island_vertices:
        bm.verts.ensure_lookup_table()
        parent = list(range(len(bm.verts)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        for edge in bm.edges:
            first, second = find(edge.verts[0].index), find(edge.verts[1].index)
            if first != second:
                parent[first] = second
        members = {}
        for vertex in bm.verts:
            members.setdefault(find(vertex.index), []).append(vertex)
        small = [vertices for vertices in members.values() if len(vertices) < min_island_vertices]
        # Never delete the whole model: keep at least the largest island.
        if len(small) < len(members):
            doomed = [vertex for vertices in small for vertex in vertices]
            removed_islands, removed_island_vertices = len(small), len(doomed)
            bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    loose = [vertex for vertex in bm.verts if not vertex.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    if had_custom_normals and not obj.data.has_custom_normals:
        warnings.append(f"{obj.name}: la reparación perdió las normales personalizadas; revisa el sombreado.")
    after[obj.name] = measure(obj)
    changes[obj.name] = {
        "welded_vertices": welded, "zero_area_faces_removed": len(zero_faces),
        "loose_vertices_removed": len(loose),
        "small_islands_removed": removed_islands, "small_island_vertices_removed": removed_island_vertices,
        "had_custom_normals": had_custom_normals, "has_custom_normals": obj.data.has_custom_normals,
    }

new_mins, new_maxs = world_bounds()
drift = max(
    [abs(new_mins[axis] - mins[axis]) for axis in range(3)] + [abs(new_maxs[axis] - maxs[axis]) for axis in range(3)]
)
if drift > height * 1e-3:
    fail(f"La reparación movió la geometría ({drift:.5f}); descartada.")

try:
    bpy.ops.export_scene.gltf(filepath=output_path, export_format="GLB", use_selection=False)
except Exception as exc:
    fail("Blender no pudo exportar el GLB reparado: " + str(exc))

remaining_tiny = sum(stats["islands_under_20_vertices"] for stats in after.values())
if remaining_tiny and not min_island_vertices:
    warnings.append(
        f"Quedan {remaining_tiny} islas de menos de 20 vértices. No se eliminaron; "
        "pueden ser accesorios sueltos. Usa --min-island-vertices para limpiarlas."
    )
open_edges = sum(stats["boundary_edges"] for stats in after.values())
if open_edges:
    warnings.append(f"La malla conserva {open_edges} aristas abiertas; MeshingUp no rellena agujeros automáticamente.")

report = {
    "method": "weld_and_clean_v1", "weld_distance": weld_distance, "min_island_vertices": min_island_vertices,
    "before": before, "after": after, "changes": changes,
    "bounds_drift": drift, "height": height, "warnings": warnings,
}
with open(report_path, "w", encoding="utf-8") as handle:
    json.dump(report, handle, ensure_ascii=False)
print("MESHINGUP_REPAIR_COMPLETE")
