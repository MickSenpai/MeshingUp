"""Provider-neutral evaluation and bounded modeling operations for CLI agents."""
import hashlib
import json
import math
import subprocess
import uuid
from pathlib import Path
from .project import ProjectError, load_project, save_project
from .avatar3d import _project_relative_file, _blender_command, resolve_blender_launcher, validate_project_avatar, validate_glb


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract():
    return {'version': 1, 'commands': ['evaluate', 'evidence', 'edit', 'review'],
        'workflow': 'evaluate → evidence → inspect PNG and geometry.json → edit → evidence → review',
        'edit_schema': {'source_sha256': 'current avatar hash', 'changes': [
            {'type': 'weights', 'mesh': 'exact mesh name', 'vertices': [0], 'weights': {'head': 1.0}},
            {'type': 'translate_vertices', 'mesh': 'exact mesh name', 'vertices': [0], 'delta': [0, 0, 0.01]}]},
        'coordinates': 'Blender mesh-local; vertex indices apply only to the exact source hash',
        'review_schema': {'source_sha256': 'current avatar hash', 'evidence_id': 'evidence directory id',
            'reviewer': 'agent/model name', 'verdict': 'pass|needs_changes', 'findings': ['concrete observations']},
        'limits': 'No automatic AI provider calls. External vision agent must inspect evidence; reviews never set ready.'}


def validate_changes(changes):
    if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
        raise ProjectError('changes requiere entre 1 y 100 operaciones.')
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get('mesh'), str) or not change['mesh']:
            raise ProjectError('Cada cambio requiere mesh y una operación estructurada.')
        indices = change.get('vertices')
        if not isinstance(indices, list) or not indices or any(type(i) is not int or i < 0 for i in indices):
            raise ProjectError('vertices requiere índices enteros no negativos.')
        if change.get('type') == 'weights':
            weights = change.get('weights')
            if (not isinstance(weights, dict) or not 1 <= len(weights) <= 4
                or any(not isinstance(k, str) or not k or not number(v) or v < 0 for k,v in weights.items())
                or not number(sum(weights.values())) or sum(weights.values()) <= 0):
                raise ProjectError('weights requiere de 1 a 4 pesos finitos no negativos, con suma positiva.')
        elif change.get('type') == 'translate_vertices':
            delta = change.get('delta')
            if not isinstance(delta, list) or len(delta) != 3 or not all(number(v) for v in delta):
                raise ProjectError('delta requiere tres números finitos.')
        else:
            raise ProjectError('Tipo de edición no soportado.')


def operate(project, operation, payload=None):
    path = Path(project).resolve(); data = load_project(path)
    avatar = data.get('avatar3d', {})
    source = _project_relative_file(path, avatar.get('path', ''), 'avatar')
    if not source.is_file() or digest(source) != avatar.get('sha256'):
        raise ProjectError('El avatar falta o cambió; vuelve a importarlo.')
    sha = digest(source); stamp = path.stat().st_mtime_ns
    payload = {} if payload is None else payload
    if not isinstance(payload, dict): raise ProjectError('La solicitud debe ser un objeto JSON.')
    if operation in {'edit', 'review'} and payload.get('source_sha256') != sha:
        raise ProjectError('source_sha256 no coincide; inspecciona la versión actual.')
    task = uuid.uuid4().hex
    if operation == 'evaluate':
        validation = validate_project_avatar(data, path)
        report = {'source_sha256': sha, 'structural': validation,
                  'recorded_rig_diagnostics': avatar.get('rigging', {}),
                  'visual_review': 'pending', 'verdict': 'needs_review',
                  'next_action': 'avatar3d evidence; inspeccionar PNG y registrar review', 'contract': contract()}
    elif operation == 'review':
        if payload.get('verdict') not in {'pass','needs_changes'} or not isinstance(payload.get('reviewer'),str) or not payload['reviewer'].strip():
            raise ProjectError('Indica reviewer y verdict: pass o needs_changes.')
        if not isinstance(payload.get('findings'),list) or not all(isinstance(x,str) for x in payload['findings']):
            raise ProjectError('findings debe ser una lista de observaciones.')
        previous = data.get('agent3d', {}).get('evidence', {})
        if previous.get('id') != payload.get('evidence_id') or previous.get('source_sha256') != sha:
            raise ProjectError('La evidencia no corresponde a este avatar.')
        if not previous.get('files'):
            raise ProjectError('La evidencia está incompleta.')
        for item in previous['files']:
            file = _project_relative_file(path, item['path'], 'evidencia')
            if not file.is_file() or digest(file) != item['sha256']: raise ProjectError('La evidencia cambió.')
        report = {**payload, 'review_type': 'external_agent_attestation', 'structural': validate_project_avatar(data,path)}
    elif operation in {'evidence','edit'}:
        if avatar.get('format') != 'glb': raise ProjectError('Este adaptador admite GLB; conserva VRM sin reexportarlo.')
        if operation == 'edit':
            changes = payload.get('changes')
            validate_changes(changes)
        launcher = resolve_blender_launcher()
        if not launcher: raise ProjectError('Instala Blender para esta operación.')
        root = path.parent / 'assets' / 'agent3d' / task; root.mkdir(parents=True)
        request = root/'request.json'; request.write_text(json.dumps({**payload,'operation':operation}))
        command = _blender_command(launcher, Path(__file__).with_name('blender_agent.py'), path.parent,
                                   [str(source),str(request),str(root)])
        try:
            process = subprocess.run(command,capture_output=True,text=True,timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProjectError('No se pudo completar Blender; se conservó el avatar.') from exc
        (root/'blender.log').write_text(process.stdout+process.stderr)
        if process.returncode: raise ProjectError('Falló Blender; consulta '+str(root/'blender.log'))
        if path.stat().st_mtime_ns != stamp or digest(source)!=sha:
            raise ProjectError('El proyecto cambió; no se aplicó el resultado.')
        report = json.loads((root/'report.json').read_text())
        report.update(id=task, source_sha256=sha)
        if operation == 'edit':
            output=root/'edited.glb'; info=validate_glb(output)
            if info['errors']: raise ProjectError('GLB editado inválido.')
            # Keep all versions. Never prune user assets or carry stale rig diagnostics forward.
            data.setdefault('agent3d',{}).setdefault('versions',[]).append(avatar)
            data['avatar3d'] = {k:info[k] for k in ('sha256','rigged','human_bones','animations','name')}
            data['avatar3d'].update(format='glb',version='2.0',path=output.relative_to(path.parent).as_posix())
            report['output_sha256']=info['sha256']
            data['avatar3d']['agent_edited'] = True
            for stale in ('evidence', 'review', 'evaluate'):
                data['agent3d'].pop(stale, None)
        else:
            report['files']=[{'path':f.relative_to(path.parent).as_posix(),'sha256':digest(f)} for f in root.iterdir() if f.suffix in {'.png','.json'}]
            report['geometry']= (root/'geometry.json').relative_to(path.parent).as_posix()
    else: raise ProjectError('Operación desconocida.')
    if path.stat().st_mtime_ns != stamp or digest(source) != sha: raise ProjectError('El proyecto o avatar cambió; recárgalo.')
    data.setdefault('agent3d',{})[operation]=report
    data.update(status='needs_review',stage='avatar3d_'+operation,progress=100,task_id=task,
                last_result='Operación '+operation+' completada; requiere revisión visual y de poses.')
    save_project(path,data,action='agent3d_'+operation,actor=payload.get('reviewer','agent'))
    return report
