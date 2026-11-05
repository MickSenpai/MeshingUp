"""Portable GLB/VRM avatar import, validation and optional Blender auto-rig."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from .project import ProjectError, load_project, save_project, update_workflow


VRM_REQUIRED_HUMAN_BONES = (
    "hips", "spine", "head",
    "leftUpperArm", "leftLowerArm", "leftHand",
    "rightUpperArm", "rightLowerArm", "rightHand",
    "leftUpperLeg", "leftLowerLeg", "leftFoot",
    "rightUpperLeg", "rightLowerLeg", "rightFoot",
)
VRM_REQUIRED_EXPRESSIONS = ("blink", "aa", "ih", "ou", "ee", "oh")


def read_glb_document(path: str | Path) -> dict[str, Any]:
    """Read the JSON chunk of a GLB/VRM without loading mesh buffers."""
    source = Path(path).expanduser().resolve()
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ProjectError(f"No pude leer el avatar 3D {source}: {exc}") from exc
    if len(raw) < 20 or raw[:4] != b"glTF":
        raise ProjectError("El avatar no es un contenedor GLB/VRM válido (falta la cabecera glTF).")
    version = int.from_bytes(raw[4:8], "little")
    declared_length = int.from_bytes(raw[8:12], "little")
    if version != 2 or declared_length != len(raw):
        raise ProjectError(f"GLB no compatible: versión {version}, tamaño declarado {declared_length}, tamaño real {len(raw)}.")
    json_length = int.from_bytes(raw[12:16], "little")
    json_type = raw[16:20]
    if json_type != b"JSON" or 20 + json_length > len(raw):
        raise ProjectError("GLB inválido: el primer chunk no es JSON o está truncado.")
    try:
        document = json.loads(raw[20:20 + json_length].decode("utf-8").rstrip(" \t\r\n\0"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"El chunk JSON del GLB no se puede leer: {exc}") from exc
    asset = document.get("asset") if isinstance(document, dict) else None
    if not isinstance(asset, dict) or asset.get("version") != "2.0":
        raise ProjectError("El documento no declara glTF 2.0.")
    return document


def inspect_vrm(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    document = read_glb_document(source)
    extensions = document.get("extensions", {})
    if not isinstance(extensions, dict):
        raise ProjectError("El objeto extensions del GLB debe ser un mapa JSON.")
    vrm = extensions.get("VRMC_vrm")
    if not isinstance(vrm, dict):
        legacy = "VRM" in extensions or "VRM" in document.get("extensionsUsed", [])
        if legacy:
            raise ProjectError("Este modelo usa VRM 0.x. MeshingUp requiere VRM 1.0 para mantener una asignación humanoide y expresiones interoperables.")
        raise ProjectError("El GLB no contiene la extensión VRMC_vrm; importa un avatar VRM 1.0.")
    meta = vrm.get("meta", {})
    if not isinstance(meta, dict):
        raise ProjectError("VRMC_vrm.meta no tiene el formato esperado.")
    spec_version = str(vrm.get("specVersion", ""))
    if spec_version != "1.0":
        raise ProjectError(f"Versión VRM no compatible: {spec_version or 'sin declarar'}; se requiere 1.0.")
    humanoid_data = vrm.get("humanoid", {})
    if not isinstance(humanoid_data, dict) or not isinstance(humanoid_data.get("humanBones", {}), dict):
        raise ProjectError("VRMC_vrm.humanoid.humanBones no tiene el formato esperado.")
    humanoid = humanoid_data.get("humanBones", {})
    nodes = document.get("nodes", [])
    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        raise ProjectError("El arreglo nodes del GLB no tiene el formato esperado.")
    assigned = {
        name: int(entry["node"])
        for name, entry in humanoid.items()
        if isinstance(entry, dict) and isinstance(entry.get("node"), int)
    }
    invalid_nodes = [name for name, index in assigned.items() if index < 0 or index >= len(nodes)]
    missing_bones = [bone for bone in VRM_REQUIRED_HUMAN_BONES if bone not in assigned]
    expressions = vrm.get("expressions", {})
    preset_expressions = expressions.get("preset", {}) if isinstance(expressions, dict) else {}
    if not isinstance(preset_expressions, dict):
        raise ProjectError("VRMC_vrm.expressions.preset no tiene el formato esperado.")
    animation_data = document.get("animations", [])
    if not isinstance(animation_data, list):
        raise ProjectError("El arreglo animations del GLB no tiene el formato esperado.")
    animations = [
        str(clip.get("name") or f"Animation {index + 1}")
        for index, clip in enumerate(animation_data)
        if isinstance(clip, dict)
    ]
    scene_index = document.get("scene", 0)
    if not isinstance(scene_index, int):
        raise ProjectError("La escena predeterminada del GLB debe ser un índice entero.")
    scenes = document.get("scenes", [])
    if not isinstance(scenes, list):
        raise ProjectError("El arreglo scenes del GLB no tiene el formato esperado.")
    if any(not isinstance(scene, dict) for scene in scenes):
        raise ProjectError("Cada escena del GLB debe ser un objeto JSON.")
    root_nodes = scenes[scene_index].get("nodes", []) if 0 <= scene_index < len(scenes) else []
    skins = document.get("skins", [])
    if not isinstance(skins, list):
        raise ProjectError("El arreglo skins del GLB no tiene el formato esperado.")
    return {
        "path": str(source),
        "name": str(meta.get("name") or source.stem),
        "spec_version": spec_version,
        "node_count": len(nodes),
        "skin_count": len(skins),
        "root_node_count": len(root_nodes),
        "human_bones": sorted(assigned),
        "missing_required_bones": missing_bones,
        "invalid_bone_nodes": invalid_nodes,
        "expressions": sorted(preset_expressions),
        "missing_required_expressions": [name for name in VRM_REQUIRED_EXPRESSIONS if name not in preset_expressions],
        "animations": animations,
        "look_at": bool(vrm.get("lookAt")),
        "spring_bones": "VRMC_springBone" in extensions,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }


def inspect_glb(path: str | Path) -> dict[str, Any]:
    """Inspect a plain GLB, with or without an existing humanoid skeleton."""
    source = Path(path).expanduser().resolve()
    document = read_glb_document(source)
    nodes = document.get("nodes", [])
    meshes = document.get("meshes", [])
    skins = document.get("skins", [])
    animations = document.get("animations", [])
    if (
        not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes)
        or not isinstance(meshes, list) or not all(isinstance(mesh, dict) for mesh in meshes)
        or not isinstance(skins, list) or not isinstance(animations, list)
    ):
        raise ProjectError("El GLB contiene arreglos glTF con formato inválido.")
    primitive_count = sum(len(mesh.get("primitives", [])) for mesh in meshes if isinstance(mesh.get("primitives", []), list))
    skin_nodes = set()
    invalid_skin_joints = []
    empty_skins = 0
    for skin in skins:
        if not isinstance(skin, dict) or not isinstance(skin.get("joints", []), list):
            raise ProjectError("Una skin del GLB no declara una lista válida de joints.")
        joints = skin["joints"]
        if not joints:
            empty_skins += 1
        for index in joints:
            if not isinstance(index, int) or index < 0 or index >= len(nodes):
                invalid_skin_joints.append(index)
            else:
                skin_nodes.add(index)
    bone_names = sorted(
        str(nodes[index].get("name", f"node-{index}"))
        for index in skin_nodes
        if 0 <= index < len(nodes) and isinstance(nodes[index], dict)
    )
    return {
        "path": str(source), "name": source.stem,
        "node_count": len(nodes), "mesh_count": len(meshes), "primitive_count": primitive_count,
        "skin_count": len(skins), "rigged": bool(skin_nodes),
        "invalid_skin_joints": invalid_skin_joints, "empty_skins": empty_skins,
        "human_bones": bone_names,
        "animations": [str(item.get("name") or f"Animation {i + 1}") for i, item in enumerate(animations) if isinstance(item, dict)],
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }


def resolve_blender_launcher(blender_path: str | None = None) -> list[str] | None:
    """Find a native Blender executable or a user/system Flatpak launcher."""
    if blender_path:
        return [blender_path]
    native = shutil.which("blender")
    if native:
        return [native]
    flatpak = shutil.which("flatpak")
    if flatpak:
        for scope in ("--user", "--system"):
            installed = subprocess.run([flatpak, "info", scope, "org.blender.Blender"], capture_output=True, check=False)
            if installed.returncode == 0:
                return [flatpak, "run", scope, "--command=blender", "org.blender.Blender"]
    return None


def blender_available() -> bool:
    """Return whether Blender is available directly or as a user/system Flatpak."""
    return resolve_blender_launcher() is not None


def validate_glb(path: str | Path) -> dict[str, Any]:
    info = inspect_glb(path)
    errors: list[str] = []
    warnings: list[str] = []
    if not info["mesh_count"] or not info["primitive_count"]:
        errors.append("El GLB no contiene mallas que MeshingUp pueda mostrar o riggear.")
    if info["invalid_skin_joints"]:
        errors.append("Una skin referencia nodos inexistentes o índices de hueso inválidos.")
    if info["empty_skins"]:
        errors.append("El GLB contiene skins sin huesos asignados.")
    if not info["rigged"]:
        warnings.append("Este GLB aún no tiene esqueleto. Puedes previsualizarlo o pedir a MeshingUp que proponga un auto-rig con Blender.")
    if info["rigged"] and not info["animations"]:
        warnings.append("El GLB tiene skin, pero no trae clips de animación.")
    return {"valid": not errors, "errors": errors, "warnings": warnings, **info}


def attach_avatar(project_path: str | Path, model_path: str | Path, *, actor: str = "user", extra_metadata: dict[str, Any] | None = None, action: str = "avatar3d_attached") -> dict[str, Any]:
    """Copy a VRM or GLB avatar into a project without altering the source."""
    source = Path(model_path).expanduser().resolve()
    suffix = source.suffix.casefold()
    if suffix == ".vrm":
        info = validate_vrm(source)
        resource_format, version = "vrm", "1.0"
    elif suffix == ".glb":
        info = validate_glb(source)
        resource_format, version = "glb", "2.0"
    else:
        raise ProjectError("Formato no compatible. Importa un archivo .glb o .vrm.")
    if info["errors"]:
        raise ProjectError("El avatar no pasó la validación: " + " ".join(info["errors"]))
    project_file = Path(project_path).expanduser().resolve()
    data = load_project(project_file)
    previous_avatar = data.get("avatar3d", {})
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", source.stem).strip("-").lower() or "avatar"
    relative = Path("assets") / "avatars" / f"{slug}-{info['sha256'][:10]}{suffix}"
    destination = project_file.parent / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    created_resource = not destination.exists()
    if created_resource:
        temporary = destination.with_name(f".{destination.name}.tmp")
        try:
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    avatar_metadata = {
        "format": resource_format, "version": version, "path": relative.as_posix(),
        "sha256": info["sha256"], "name": info["name"],
        "rigged": info.get("rigged", True), "human_bones": info["human_bones"],
        "animations": info["animations"],
        **(extra_metadata or {}),
    }
    paths_to_prune: list[dict[str, str]] = []
    incoming_source_hash = avatar_metadata.get("source_sha256")
    same_lineage = (
        isinstance(previous_avatar, dict)
        and previous_avatar.get("rigged")
        and incoming_source_hash
        and previous_avatar.get("source_sha256") == incoming_source_hash
    )
    if same_lineage:
        previous_versions = previous_avatar.get("versions", [])
        if not isinstance(previous_versions, list):
            previous_versions = []
        current_version = {key: value for key, value in previous_avatar.items() if key != "versions"}
        history = []
        if current_version.get("path") != relative.as_posix():
            history.append(current_version)
        history.extend(item for item in previous_versions if isinstance(item, dict) and item.get("path") != relative.as_posix())
        avatar_metadata["versions"] = history[:2]
        paths_to_prune = [item for item in history[2:] if item.get("path") and item.get("sha256")]
    data["avatar3d"] = avatar_metadata
    data["status"] = "needs_review"
    data["stage"] = "avatar3d"
    warning_summary = " ".join(info["warnings"])
    data["last_result"] = "Avatar 3D importado; requiere revisión." + (" " + warning_summary if warning_summary else "")
    try:
        save_project(project_file, data, action=action, actor=actor)
    except Exception:
        if created_resource and destination.exists():
            destination.unlink()
        raise
    # Only prune files explicitly recorded as older versions of this same
    # source lineage. Keep source_path and never scan/delete arbitrary assets.
    source_relative = avatar_metadata.get("source_path")
    for old in paths_to_prune:
        try:
            old_relative = Path(str(old["path"]))
            if old_relative.is_absolute() or ".." in old_relative.parts or old_relative == Path(str(source_relative or "")):
                continue
            old_path = (project_file.parent / old_relative).resolve()
            avatar_folder = (project_file.parent / "assets" / "avatars").resolve()
            old_path.relative_to(avatar_folder)
            if old_path.is_file() and hashlib.sha256(old_path.read_bytes()).hexdigest() == old["sha256"]:
                old_path.unlink()
        except (OSError, ValueError):
            continue
    return {"project": str(project_file), "avatar": data["avatar3d"], "validation": info}


def create_avatar_project(model_path: str | Path, project_path: str | Path, *, name: str | None = None, actor: str = "user") -> dict[str, Any]:
    """Create a portable 3D-only MeshingUp project from a GLB or VRM avatar."""
    source = Path(model_path).expanduser().resolve()
    if source.suffix.casefold() == ".vrm":
        validation = validate_vrm(source)
    elif source.suffix.casefold() == ".glb":
        validation = validate_glb(source)
    else:
        raise ProjectError("El proyecto 3D necesita un archivo .glb o .vrm.")
    if validation["errors"]:
        raise ProjectError("El modelo 3D no pasó la validación: " + " ".join(validation["errors"]))

    destination = Path(project_path).expanduser().resolve()
    if destination.exists():
        raise ProjectError(f"Ya existe un proyecto en {destination}; elige otro nombre para no sobrescribirlo.")
    data: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "name": name or destination.stem,
        "joints": [], "bones": [], "layers": [],
        "stage": "avatar3d", "status": "needs_review", "progress": 0,
    }
    save_project(destination, data, action="avatar3d_project_created", actor=actor)
    try:
        return attach_avatar(destination, source, actor=actor)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def validate_vrm(path: str | Path) -> dict[str, Any]:
    info = inspect_vrm(path)
    errors = []
    warnings = []
    if info["invalid_bone_nodes"]:
        errors.append("Huesos humanoides apuntan a nodos inexistentes: " + ", ".join(info["invalid_bone_nodes"]) + ".")
    if info["missing_required_bones"]:
        errors.append("Faltan huesos humanoides requeridos: " + ", ".join(info["missing_required_bones"]) + ".")
    if info["root_node_count"] == 0:
        errors.append("La escena glTF predeterminada no tiene nodo raíz.")
    if not info["skin_count"]:
        errors.append("El avatar no tiene skin esquelético.")
    if info["missing_required_expressions"]:
        warnings.append("Faltan expresiones faciales estándar: " + ", ".join(info["missing_required_expressions"]) + ".")
    if not info["look_at"]:
        warnings.append("El avatar no configura control VRM LookAt; la mirada requerirá huesos o expresiones compatibles.")
    if not info["spring_bones"]:
        warnings.append("El avatar no incluye VRMC_springBone; pelo y ropa no tendrán movimiento secundario VRM.")
    if not info["animations"]:
        warnings.append("El avatar no trae clips de movimiento. El visor usará movimiento procedural acotado; caminar con naturalidad requiere un clip de locomoción revisado.")
    return {"valid": not errors, "errors": errors, "warnings": warnings, **info}


def attach_vrm(project_path: str | Path, model_path: str | Path, *, actor: str = "user") -> dict[str, Any]:
    """Backward-compatible VRM-specific import entrypoint."""
    return attach_avatar(project_path, model_path, actor=actor)


def autorig_glb(project_path: str | Path, *, blender_path: str | None = None, forward_axis: str = "-Y", actor: str = "user", expected_mtime_ns: int | None = None) -> dict[str, Any]:
    """Generate an editable humanoid skeleton/weights for the project's unrigged GLB using Blender."""
    if forward_axis not in {"-Y", "+Y", "-X", "+X"}:
        raise ProjectError("El eje frontal debe ser -Y, +Y, -X o +X.")
    launcher = resolve_blender_launcher(blender_path)
    flatpak = shutil.which("flatpak")
    if not launcher:
        raise ProjectError("No encuentro Blender. Instala Blender y vuelve a intentarlo, o indica su ejecutable con --blender.")
    project_file = Path(project_path).expanduser().resolve()
    data = load_project(project_file)
    avatar = data.get("avatar3d", {})
    if not isinstance(avatar, dict) or avatar.get("format") != "glb":
        raise ProjectError("Primero importa un archivo GLB al proyecto.")
    is_rebuild = bool(avatar.get("rigged") and avatar.get("source_path"))
    source_relative = Path(str(avatar.get("source_path") if is_rebuild else avatar.get("path", "")))
    if source_relative.is_absolute() or ".." in source_relative.parts or not source_relative.parts:
        raise ProjectError("La ruta del GLB fuente debe ser relativa y permanecer dentro del proyecto.")
    source = (project_file.parent / source_relative).resolve()
    try:
        source.relative_to(project_file.parent.resolve())
    except ValueError as exc:
        raise ProjectError("La ruta del GLB sale de la carpeta del proyecto.") from exc
    expected_source_hash = avatar.get("source_sha256") if is_rebuild else avatar.get("sha256")
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected_source_hash:
        raise ProjectError("El GLB original falta o cambió desde que se importó; vuelve a importarlo antes de riggear.")
    script = Path(__file__).with_name("blender_autorig.py")
    with tempfile.TemporaryDirectory(prefix=".meshingup-autorig-", dir=project_file.parent) as temporary_dir:
        output = Path(temporary_dir) / "rigged.glb"
        report_file = Path(temporary_dir) / "analysis.json"
        command = [*launcher]
        if launcher[0] == flatpak:
            command[3:3] = [f"--filesystem={project_file.parent}", f"--filesystem={script.parent}"]
        command.extend(["--background", "--factory-startup", "--python-exit-code", "1", "--python", str(script), "--", str(source), str(output), str(report_file), forward_axis])
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProjectError(f"No pude ejecutar el auto-rig de Blender: {exc}") from exc
        if process.returncode != 0 or not output.is_file() or not report_file.is_file():
            detail = process.stderr or process.stdout or "Blender terminó sin generar el GLB riggeado."
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            detail = detail[-3500:]
            raise ProjectError("Falló el auto-rig en Blender. El archivo fuente sigue intacto. Detalle: " + detail)
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"Blender generó el modelo, pero no pude leer su informe: {exc}") from exc
        result = validate_glb(output)
        if result["errors"] or not result["rigged"]:
            raise ProjectError("El GLB resultante no contiene una skin utilizable: " + " ".join(result["errors"]))
        if expected_mtime_ns is not None and project_file.stat().st_mtime_ns != expected_mtime_ns:
            raise ProjectError("El proyecto cambió mientras Blender trabajaba. No guardé el auto-rig; recárgalo e inténtalo de nuevo.")
        source_hash = expected_source_hash
        source_path = source_relative.as_posix()
        attached = attach_avatar(project_file, output, actor=actor, action="avatar3d_autorig_generated", extra_metadata={
            "name": avatar.get("name", source.stem),
            "source_path": source_path, "source_sha256": source_hash,
            "rigging": {"method": "blender_geometry_autorig", "forward_axis": forward_axis, **report},
            **_repair_lineage(avatar),
        })
    return {**attached, "analysis": report}


def analyze_avatar_parts(
    project_path: str | Path,
    *,
    blender_path: str | None = None,
    forward_axis: str = "-Y",
    actor: str = "user",
    expected_mtime_ns: int | None = None,
) -> dict[str, Any]:
    """Inventory extractable mesh islands and persist editable body-region proposals."""
    if forward_axis not in {"-Y", "+Y", "-X", "+X"}:
        raise ProjectError("El eje frontal debe ser -Y, +Y, -X o +X.")
    launcher = resolve_blender_launcher(blender_path)
    flatpak = shutil.which("flatpak")
    if not launcher:
        raise ProjectError("No encuentro Blender para analizar las piezas 3D.")
    project_file = Path(project_path).expanduser().resolve()
    data = load_project(project_file)
    avatar = data.get("avatar3d", {})
    if not isinstance(avatar, dict) or avatar.get("format") != "glb":
        raise ProjectError("El análisis de piezas requiere un GLB importado al proyecto.")
    source_relative = Path(str(avatar.get("source_path") if avatar.get("rigged") and avatar.get("source_path") else avatar.get("path", "")))
    if source_relative.is_absolute() or ".." in source_relative.parts or not source_relative.parts:
        raise ProjectError("La ruta GLB debe ser relativa y permanecer dentro del proyecto.")
    source = (project_file.parent / source_relative).resolve()
    try:
        source.relative_to(project_file.parent.resolve())
    except ValueError as exc:
        raise ProjectError("La ruta del GLB sale de la carpeta del proyecto.") from exc
    source_hash = avatar.get("source_sha256") if avatar.get("rigged") and avatar.get("source_path") else avatar.get("sha256")
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise ProjectError("El GLB fuente falta o cambió; vuelve a importarlo antes de analizar sus piezas.")
    script = Path(__file__).with_name("blender_analyze_parts.py")
    with tempfile.TemporaryDirectory(prefix=".meshingup-parts-analysis-", dir=project_file.parent) as temporary_dir:
        report_file = Path(temporary_dir) / "parts.json"
        command = [*launcher]
        if launcher[0] == flatpak:
            command[3:3] = [f"--filesystem={project_file.parent}", f"--filesystem={script.parent}"]
        command.extend([
            "--background", "--factory-startup", "--python-exit-code", "1",
            "--python", str(script), "--", str(source), str(report_file), forward_axis,
        ])
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProjectError(f"No pude ejecutar el análisis de piezas con Blender: {exc}") from exc
        if process.returncode != 0 or not report_file.is_file():
            detail = (process.stderr or process.stdout or "Blender terminó sin generar el informe.")[-3500:]
            raise ProjectError("Falló el análisis de piezas. El GLB fuente sigue intacto. Detalle: " + detail)
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"Blender generó un informe de piezas ilegible: {exc}") from exc
    if expected_mtime_ns is not None and project_file.stat().st_mtime_ns != expected_mtime_ns:
        raise ProjectError("El proyecto cambió mientras Blender analizaba las piezas. Recárgalo e inténtalo de nuevo.")
    report["source_file"] = source_relative.as_posix()
    report["source_sha256"] = source_hash
    data = load_project(project_file)
    data.setdefault("avatar3d", {})["part_analysis"] = report
    data["stage"] = "avatar3d_analysis"
    data["status"] = "needs_review"
    data["last_result"] = (
        f"Análisis de piezas: {report.get('component_count', 0)} componentes; "
        f"{report.get('extractable_component_count', 0)} candidatos geométricos extraíbles."
    )
    save_project(project_file, data, action="avatar3d_parts_analyzed", actor=actor)
    return {"project": str(project_file), "analysis": report}


def animate_avatar3d(
    project_path: str | Path,
    name: str,
    duration: int,
    fps: int,
    keyframes: list[dict[str, Any]],
    *,
    blender_path: str | None = None,
    actor: str = "agent",
    expected_mtime_ns: int | None = None,
) -> dict[str, Any]:
    """Create an editable GLB action from structured semantic rig-axis rotations."""
    launcher = resolve_blender_launcher(blender_path)
    if not launcher:
        raise ProjectError("No encuentro Blender. Instálalo para generar animaciones GLB con `meshingupctl avatar3d animate`.")
    clip_name = str(name).strip()
    if not clip_name or len(clip_name) > 80:
        raise ProjectError("El nombre de la animación debe tener entre 1 y 80 caracteres.")
    if not isinstance(duration, int) or duration < 2 or duration > 3600:
        raise ProjectError("La duración debe estar entre 2 y 3600 fotogramas.")
    if not isinstance(fps, int) or fps < 1 or fps > 120:
        raise ProjectError("Los FPS deben estar entre 1 y 120.")
    if not isinstance(keyframes, list) or not keyframes:
        raise ProjectError("Añade al menos una clave con hueso, fotograma y rotación XYZ.")
    normalized_keys = []
    project_file = Path(project_path).expanduser().resolve()
    starting_mtime_ns = project_file.stat().st_mtime_ns
    if expected_mtime_ns is not None and starting_mtime_ns != expected_mtime_ns:
        raise ProjectError("El proyecto cambió desde que se abrió; recárgalo antes de generar la animación.")
    data = load_project(project_file)
    info = data.get("avatar3d", {})
    if not isinstance(info, dict) or info.get("format") != "glb" or not info.get("rigged"):
        raise ProjectError("Primero crea un rig Blender en un GLB del proyecto.")
    avatar_path = Path(str(info.get("path", "")))
    if avatar_path.is_absolute() or ".." in avatar_path.parts or not avatar_path.parts:
        raise ProjectError("La ruta del GLB debe permanecer dentro del proyecto.")
    source = (project_file.parent / avatar_path).resolve()
    try:
        source.relative_to(project_file.parent.resolve())
    except ValueError as exc:
        raise ProjectError("La ruta del GLB sale de la carpeta del proyecto.") from exc
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != info.get("sha256"):
        raise ProjectError("El GLB del avatar falta o cambió desde que se abrió el proyecto; recárgalo antes de animar.")
    bone_names = set(inspect_glb(source)["human_bones"])
    seen_keys: set[tuple[int, str]] = set()
    for key in keyframes:
        if not isinstance(key, dict):
            raise ProjectError("Cada clave debe ser un objeto con frame, bone y rotation.")
        frame, bone, rotation = key.get("frame"), str(key.get("bone", "")), key.get("rotation")
        if not isinstance(frame, int) or frame < 0 or frame > duration:
            raise ProjectError(f"El fotograma de {bone or 'una clave'} debe estar entre 0 y {duration}.")
        if bone not in bone_names:
            raise ProjectError(f"El GLB no contiene el hueso `{bone}`.")
        identity = (frame, bone)
        if identity in seen_keys:
            raise ProjectError(f"Hay más de una clave para `{bone}` en el fotograma {frame}.")
        seen_keys.add(identity)
        if not isinstance(rotation, (list, tuple)) or len(rotation) != 3:
            raise ProjectError(f"La rotación de `{bone}` debe contener tres ángulos XYZ en grados.")
        try:
            angles = [float(value) for value in rotation]
        except (TypeError, ValueError) as exc:
            raise ProjectError(f"La rotación de `{bone}` no es numérica.") from exc
        if not all(math.isfinite(value) and abs(value) <= 3600 for value in angles):
            raise ProjectError(f"Los ángulos de `{bone}` deben ser finitos y estar dentro de ±3600 grados.")
        normalized_keys.append({"frame": frame, "bone": bone, "rotation": angles})
    if clip_name in inspect_glb(source)["animations"]:
        raise ProjectError(f"Ya existe una animación llamada `{clip_name}`. Elige otro nombre para conservar la anterior.")
    script = Path(__file__).with_name("blender_animate.py")
    with tempfile.TemporaryDirectory(prefix=".meshingup-animation-", dir=project_file.parent) as temporary_dir:
        temporary = Path(temporary_dir)
        output, report_path, request_path = temporary / "animated.glb", temporary / "animation.json", temporary / "request.json"
        request_path.write_text(json.dumps({"name": clip_name, "duration": duration, "fps": fps, "keyframes": normalized_keys}), encoding="utf-8")
        command = [*launcher]
        flatpak = shutil.which("flatpak")
        if flatpak and launcher[0] == flatpak:
            command[3:3] = [f"--filesystem={project_file.parent}", f"--filesystem={script.parent}"]
        command.extend(["--background", "--factory-startup", "--python-exit-code", "1", "--python", str(script), "--", str(source), str(output), str(report_path), str(request_path)])
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProjectError(f"No pude ejecutar la animación en Blender: {exc}") from exc
        if process.returncode != 0 or not output.is_file() or not report_path.is_file():
            detail = (process.stderr or process.stdout or "Blender terminó sin generar el GLB animado.")[-3500:]
            raise ProjectError("Falló la generación de animación en Blender. La versión anterior sigue intacta. Detalle: " + detail)
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"Blender generó el modelo, pero no pude leer el informe de animación: {exc}") from exc
        result = validate_glb(output)
        if result["errors"] or clip_name not in result["animations"]:
            raise ProjectError("El GLB exportado no contiene la animación solicitada: " + "; ".join(result["errors"]))
        if project_file.stat().st_mtime_ns != starting_mtime_ns:
            raise ProjectError("El proyecto cambió mientras Blender trabajaba. No guardé la animación; recárgalo e inténtalo de nuevo.")
        rigging = dict(info.get("rigging", {}))
        rigging["last_generated_animation"] = report
        attached = attach_avatar(project_file, output, actor=actor, action="avatar3d_animation_generated", extra_metadata={
            "name": info.get("name", source.stem),
            "source_path": info.get("source_path", ""),
            "source_sha256": info.get("source_sha256", ""),
            "rigging": rigging,
            **_repair_lineage(info),
        })
    return {**attached, "animation": report}


def _repair_lineage(avatar: dict[str, Any]) -> dict[str, Any]:
    """Metadata that must survive every re-attach so the untouched original stays traceable."""
    return {key: avatar[key] for key in ("repair", "original_path", "original_sha256") if avatar.get(key)}


def _blender_command(launcher: list[str], script: Path, project_dir: Path, arguments: list[str]) -> list[str]:
    command = [*launcher]
    flatpak = shutil.which("flatpak")
    if flatpak and launcher[0] == flatpak:
        command[3:3] = [f"--filesystem={project_dir}", f"--filesystem={script.parent}"]
    command.extend(["--background", "--factory-startup", "--python-exit-code", "1", "--python", str(script), "--", *arguments])
    return command


def _project_relative_file(project_file: Path, relative: str, what: str) -> Path:
    path = Path(str(relative))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ProjectError(f"La ruta de {what} debe ser relativa y permanecer dentro del proyecto.")
    resolved = (project_file.parent / path).resolve()
    try:
        resolved.relative_to(project_file.parent.resolve())
    except ValueError as exc:
        raise ProjectError(f"La ruta de {what} sale de la carpeta del proyecto.") from exc
    return resolved


def repair_avatar_mesh(
    project_path: str | Path,
    *,
    blender_path: str | None = None,
    min_island_vertices: int = 0,
    actor: str = "agent",
    expected_mtime_ns: int | None = None,
    rebuild_rigged: bool = False,
) -> dict[str, Any]:
    """Weld seam duplicates and drop dead geometry from the project's GLB using Blender.

    The original GLB is never modified: the repaired mesh is attached as a new
    file and the original stays in the project (``original_path``/``original_sha256``),
    so the rig can always be rebuilt from it. Islands are only deleted when
    ``min_island_vertices`` asks for it; otherwise they are just reported.
    A rigged avatar is only replaced when ``rebuild_rigged`` is set, because the
    repaired mesh has no skin.
    """
    if not isinstance(min_island_vertices, int) or not 0 <= min_island_vertices <= 100000:
        raise ProjectError("min_island_vertices debe ser un entero entre 0 y 100000.")
    launcher = resolve_blender_launcher(blender_path)
    if not launcher:
        raise ProjectError("No encuentro Blender. Instala Blender y vuelve a intentarlo, o indica su ejecutable con --blender.")
    project_file = Path(project_path).expanduser().resolve()
    starting_mtime_ns = project_file.stat().st_mtime_ns
    if expected_mtime_ns is not None and starting_mtime_ns != expected_mtime_ns:
        raise ProjectError("El proyecto cambió desde que se abrió; recárgalo antes de reparar la malla.")
    data = load_project(project_file)
    avatar = data.get("avatar3d", {})
    if not isinstance(avatar, dict) or avatar.get("format") != "glb":
        raise ProjectError("Primero importa un archivo GLB al proyecto.")
    if avatar.get("rigged") and not rebuild_rigged:
        raise ProjectError("El avatar ya tiene rig y la reparación crearía un modelo sin esqueleto. Usa «Preparar avatar completo» para repararlo y volver a riggear.")
    if avatar.get("original_path"):
        input_relative, expected_hash = avatar["original_path"], avatar.get("original_sha256")
    elif avatar.get("rigged"):
        if not avatar.get("source_path"):
            raise ProjectError("Este rig no conserva el GLB original, así que no puedo repararlo y rehacerlo.")
        input_relative, expected_hash = avatar["source_path"], avatar.get("source_sha256")
    else:
        input_relative, expected_hash = avatar.get("path", ""), avatar.get("sha256")
    source = _project_relative_file(project_file, input_relative, "el GLB original")
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash:
        raise ProjectError("El GLB original falta o cambió desde que se importó; vuelve a importarlo antes de repararlo.")
    script = Path(__file__).with_name("blender_repair.py")
    with tempfile.TemporaryDirectory(prefix=".meshingup-repair-", dir=project_file.parent) as temporary_dir:
        output, report_file = Path(temporary_dir) / "repaired.glb", Path(temporary_dir) / "repair.json"
        command = _blender_command(launcher, script, project_file.parent, [str(source), str(output), str(report_file), str(min_island_vertices)])
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProjectError(f"No pude ejecutar la reparación de Blender: {exc}") from exc
        if process.returncode != 0 or not output.is_file() or not report_file.is_file():
            detail = (process.stderr or process.stdout or "Blender terminó sin generar el GLB reparado.")[-3500:]
            raise ProjectError("Falló la reparación en Blender. El archivo original sigue intacto. Detalle: " + detail)
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"Blender generó el modelo, pero no pude leer su informe: {exc}") from exc
        result = validate_glb(output)
        if result["errors"] or result["rigged"]:
            raise ProjectError("El GLB reparado no es válido: " + " ".join(result["errors"] or ["tiene un esqueleto inesperado."]))
        if project_file.stat().st_mtime_ns != starting_mtime_ns:
            raise ProjectError("El proyecto cambió mientras Blender trabajaba. No guardé la reparación; recárgalo e inténtalo de nuevo.")
        attached = attach_avatar(project_file, output, actor=actor, action="avatar3d_mesh_repaired", extra_metadata={
            "name": avatar.get("name", source.stem),
            "original_path": Path(str(input_relative)).as_posix(), "original_sha256": expected_hash,
            "repair": report,
        })
    return {**attached, "repair": report}


ANIMATION_PRESETS: dict[str, dict[str, Any]] = {
    # Rotations are normalized armature XYZ degrees (X side, Y front-back, Z up),
    # applied as Rz·Ry·Rx. In the T-pose a rotation about Y lowers the left arm
    # (+) / right arm (-); about Z (right arm, +) swings it forward; with the
    # arm forward, -X on the forearm bends it up. Arms rest 60° below the
    # T-pose. Loops end on their first pose.
    "idle": {
        "duration": 90, "fps": 30,
        "keyframes": [
            *({"frame": f, "bone": "leftUpperArm", "rotation": [0, 60, 0]} for f in (0, 90)),
            *({"frame": f, "bone": "rightUpperArm", "rotation": [0, -60, 0]} for f in (0, 90)),
            {"frame": 0, "bone": "chest", "rotation": [0, 0, 0]},
            {"frame": 45, "bone": "chest", "rotation": [1.5, 0, 0]},
            {"frame": 90, "bone": "chest", "rotation": [0, 0, 0]},
            {"frame": 0, "bone": "head", "rotation": [0, 0, 0]},
            {"frame": 30, "bone": "head", "rotation": [0, 0, 2]},
            {"frame": 60, "bone": "head", "rotation": [0, 0, -2]},
            {"frame": 90, "bone": "head", "rotation": [0, 0, 0]},
        ],
    },
    "wave": {
        "duration": 60, "fps": 30,
        "keyframes": [
            *({"frame": f, "bone": "leftUpperArm", "rotation": [0, 60, 0]} for f in (0, 60)),
            {"frame": 0, "bone": "rightUpperArm", "rotation": [0, -60, 0]},
            {"frame": 12, "bone": "rightUpperArm", "rotation": [0, -20, 70]},
            {"frame": 48, "bone": "rightUpperArm", "rotation": [0, -20, 70]},
            {"frame": 60, "bone": "rightUpperArm", "rotation": [0, -60, 0]},
            {"frame": 0, "bone": "rightLowerArm", "rotation": [0, 0, 0]},
            {"frame": 12, "bone": "rightLowerArm", "rotation": [-80, 0, 0]},
            {"frame": 21, "bone": "rightLowerArm", "rotation": [-80, 25, 0]},
            {"frame": 30, "bone": "rightLowerArm", "rotation": [-80, -25, 0]},
            {"frame": 39, "bone": "rightLowerArm", "rotation": [-80, 25, 0]},
            {"frame": 48, "bone": "rightLowerArm", "rotation": [-80, 0, 0]},
            {"frame": 60, "bone": "rightLowerArm", "rotation": [0, 0, 0]},
        ],
    },
}


def auto_prepare_avatar(
    project_path: str | Path,
    *,
    blender_path: str | None = None,
    forward_axis: str = "-Y",
    repair: bool = True,
    min_island_vertices: int = 0,
    clips: tuple[str, ...] | list[str] = ("idle", "wave"),
    actor: str = "agent",
    expected_mtime_ns: int | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Repair the mesh, add bones and weights, then add base animation clips.

    Progress and the last result are written to the project manifest after each
    step so the library can follow the work. The outcome is always
    ``needs_review``: the automatic checks never replace a visual review. If a
    step fails after the repair replaced a rigged avatar, the previous avatar is
    restored.
    """
    unknown = [name for name in clips if name not in ANIMATION_PRESETS]
    if unknown:
        raise ProjectError("Animaciones desconocidas: " + ", ".join(unknown) + ". Disponibles: " + ", ".join(ANIMATION_PRESETS) + ".")
    project_file = Path(project_path).expanduser().resolve()
    if expected_mtime_ns is not None and project_file.stat().st_mtime_ns != expected_mtime_ns:
        raise ProjectError("El proyecto cambió desde que se abrió; recárgalo antes de preparar el avatar.")
    data = load_project(project_file)
    previous_avatar = json.loads(json.dumps(data.get("avatar3d", {})))
    if previous_avatar.get("format") != "glb":
        raise ProjectError("Primero importa un archivo GLB al proyecto.")
    task_id = f"avatar3d-auto-{uuid.uuid4().hex[:8]}"
    steps: list[dict[str, Any]] = []
    total = (1 if repair else 0) + 1 + len(clips)
    done = 0

    def report(stage: str, detail: str) -> None:
        percent = int(done / total * 100)
        update_workflow(project_file, "in_progress", stage, detail, percent, task_id)
        if progress:
            progress(percent, detail)

    try:
        needs_repair = repair and not previous_avatar.get("repair")
        if needs_repair:
            report("avatar3d_repair", "Reparando la malla con Blender…")
            repaired = repair_avatar_mesh(
                project_file, blender_path=blender_path, min_island_vertices=min_island_vertices,
                actor=actor, rebuild_rigged=bool(previous_avatar.get("rigged")),
            )
            steps.append({"step": "repair", "welded_vertices": sum(c["welded_vertices"] for c in repaired["repair"]["changes"].values()),
                          "warnings": repaired["repair"]["warnings"]})
            done += 1
        elif repair:
            steps.append({"step": "repair", "skipped": "ya reparado"})
            done += 1
        report("avatar3d_autorig", "Creando huesos y pesos con Blender…")
        rigged = autorig_glb(project_file, blender_path=blender_path, forward_axis=forward_axis, actor=actor)
        analysis = rigged["analysis"]
        deformation = analysis.get("deformation_validation", {})
        steps.append({"step": "autorig", "confidence": analysis.get("confidence"),
                      "failed_poses": deformation.get("failed_pose_count"), "poses": deformation.get("pose_count")})
        done += 1
        existing = set(inspect_glb(project_file.parent / rigged["avatar"]["path"])["animations"])
        for name in clips:
            clip_name = f"MeshingUp · {name.capitalize()}"
            if clip_name in existing:
                steps.append({"step": f"clip:{name}", "skipped": "ya existe"})
            else:
                report("avatar3d_animation", f"Generando la animación «{clip_name}»…")
                preset = ANIMATION_PRESETS[name]
                animate_avatar3d(project_file, clip_name, preset["duration"], preset["fps"], preset["keyframes"], blender_path=blender_path, actor=actor)
                steps.append({"step": f"clip:{name}", "name": clip_name})
            done += 1
    except Exception as exc:
        restored = False
        current = load_project(project_file)
        if previous_avatar.get("rigged") and not current.get("avatar3d", {}).get("rigged"):
            current["avatar3d"] = previous_avatar
            save_project(project_file, current, action="avatar3d_auto_rolled_back", actor=actor)
            restored = True
        update_workflow(project_file, "failed", "avatar3d_auto", str(exc)[:500] + (" Se restauró el rig anterior." if restored else ""), int(done / total * 100), task_id)
        raise
    failed = next((item.get("failed_poses") for item in steps if item["step"] == "autorig"), None)
    summary = (
        f"Avatar preparado: {len(steps)} pasos. "
        + (f"La prueba de deformación falló en {failed} poses; " if failed else "")
        + "revisa el rig visualmente antes de usarlo."
    )
    update_workflow(project_file, "needs_review", "avatar3d_review", summary, 100, task_id)
    if progress:
        progress(100, "Listo")
    return {"project": str(project_file), "task_id": task_id, "steps": steps, "summary": summary,
            "avatar": load_project(project_file)["avatar3d"]}


def validate_project_avatar(data: dict[str, Any], project_path: str | Path) -> dict[str, Any] | None:
    if "avatar3d" not in data:
        return None
    avatar = data.get("avatar3d")
    if not isinstance(avatar, dict) or (avatar.get("format"), avatar.get("version")) not in {("vrm", "1.0"), ("glb", "2.0")}:
        return {"errors": ["El recurso avatar3d debe ser VRM 1.0 o GLB 2.0."], "warnings": []}
    relative = Path(str(avatar.get("path", "")))
    root = Path(project_path).expanduser().resolve().parent
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        return {"errors": ["La ruta de avatar3d debe ser relativa y permanecer dentro del proyecto."], "warnings": []}
    model = (root / relative).resolve()
    try:
        model.relative_to(root)
    except ValueError:
        return {"errors": ["La ruta del avatar sale de la carpeta del proyecto."], "warnings": []}
    if not model.is_file():
        return {"errors": [f"No encuentro el avatar 3D: {relative.as_posix()}."], "warnings": []}
    actual_hash = hashlib.sha256(model.read_bytes()).hexdigest()
    if avatar.get("sha256") != actual_hash:
        return {"errors": ["El hash del avatar VRM no coincide con el recurso registrado."], "warnings": []}
    result = validate_vrm(model) if avatar["format"] == "vrm" else validate_glb(model)
    rigging = avatar.get("rigging", {})
    deformation_validation = rigging.get("deformation_validation") if isinstance(rigging, dict) else None
    validation_warnings: list[str] = []
    if isinstance(deformation_validation, dict):
        if not deformation_validation.get("passed", False):
            validation_warnings.append("El rig no pasó la prueba automática de deformación; el proyecto requiere revisión y no debe marcarse como listo.")
        validation_warnings.extend(
            str(item) for item in deformation_validation.get("warnings", []) if isinstance(item, str)
        )
    part_analysis = avatar.get("part_analysis")
    expected_analysis_hash = avatar.get("source_sha256") if avatar.get("source_path") else avatar.get("sha256")
    if isinstance(part_analysis, dict) and part_analysis.get("source_sha256") != expected_analysis_hash:
        validation_warnings.append("El análisis de piezas está obsoleto respecto al GLB fuente; vuelve a analizarlo.")
    source_relative = avatar.get("source_path")
    if source_relative:
        source = (root / str(source_relative)).resolve()
        try:
            source.relative_to(root)
        except ValueError:
            return {"errors": ["La ruta GLB de origen sale de la carpeta del proyecto."], "warnings": []}
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != avatar.get("source_sha256"):
            return {"errors": ["El GLB fuente del auto-rig falta o cambió."], "warnings": []}
    return {
        "errors": result["errors"], "warnings": [*result["warnings"], *validation_warnings],
        "avatar": result, "deformation_validation": deformation_validation, "part_analysis": part_analysis,
    }
