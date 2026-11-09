"""Optional cloud image reconstruction; never treats generated surfaces as observed."""
from pathlib import Path
import hashlib
import tempfile
import uuid
from PIL import Image
from .project import ProjectError, load_project, save_project, update_workflow
from .avatar3d import attach_avatar, auto_prepare_avatar, blender_available, validate_glb

SPACE = 'trellis-community/TRELLIS'


def _output_glb(value):
    if isinstance(value, str):
        p = Path(value)
        return p if p.suffix.lower() == '.glb' and p.is_file() else None
    if isinstance(value, dict):
        value = [value.get('path'), value.get('name')]
    if isinstance(value, (tuple, list)):
        for item in value:
            found = _output_glb(item)
            if found:
                return found
    return None


def generate_avatar(project_path, *, allow_upload=False, prepare=False, seed=0,
                    expected_mtime_ns=None, actor='agent', progress=None):
    path = Path(project_path).resolve()
    if not allow_upload:
        raise ProjectError('TRELLIS envía la imagen a Hugging Face. Autoriza con --allow-upload.')
    if not 0 <= seed <= 2147483647:
        raise ProjectError('La semilla debe estar entre 0 y 2147483647.')
    if expected_mtime_ns is not None and path.stat().st_mtime_ns != expected_mtime_ns:
        raise ProjectError('El proyecto cambió; recárgalo antes de generar.')
    data = load_project(path)
    if data.get('avatar3d'):
        raise ProjectError('Este proyecto ya tiene un avatar. Crea otro proyecto desde la imagen para conservarlo.')
    if not data.get('image'):
        raise ProjectError('Importa una imagen de referencia antes de generar el modelo.')
    source = path.parent / data['image']
    try:
        with Image.open(source) as im:
            im.verify()
    except (OSError, ValueError) as exc:
        raise ProjectError('La imagen no se puede leer.') from exc
    if prepare and not blender_available():
        raise ProjectError('Instala Blender para preparar el rig, o genera sólo la malla.')
    try:
        from gradio_client import Client, handle_file
    except ImportError as exc:
        raise ProjectError('Instala la dependencia opcional: pip install -e ".[generation3d]"') from exc
    task = 'generate3d-' + uuid.uuid4().hex[:10]
    update_workflow(path, 'in_progress', 'avatar3d_generation', 'Generando con TRELLIS en Hugging Face…', 5, task)
    stamp = path.stat().st_mtime_ns
    try:
        with tempfile.TemporaryDirectory(prefix='meshingup-generation-') as tmp:
            snapshot = Path(tmp) / ('reference' + source.suffix)
            snapshot.write_bytes(source.read_bytes())
            digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            client = Client(SPACE, verbose=False, download_files=tmp)
            client.predict(api_name='/start_session')
            processed = client.predict(image=handle_file(str(snapshot)), api_name='/preprocess_image')
            result = client.predict(image=handle_file(processed), multiimages=[],
                                    seed=seed, ss_guidance_strength=7.5, ss_sampling_steps=12,
                                    slat_guidance_strength=3.0, slat_sampling_steps=12,
                                    multiimage_algo='stochastic', mesh_simplify=0.95,
                                    texture_size=1024, api_name='/generate_and_extract_glb')
            model = _output_glb(result)
            if model is None:
                raise ProjectError('TRELLIS no devolvió un GLB descargable.')
            report = validate_glb(model)
            if report['errors']:
                raise ProjectError('TRELLIS devolvió un GLB inválido: ' + '; '.join(report['errors']))
            if path.stat().st_mtime_ns != stamp or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise ProjectError('La referencia o el proyecto cambió durante la generación; no se adjuntó el resultado.')
            attach_avatar(path, model, actor=actor)
        data = load_project(path)
        data['avatar3d']['generation'] = dict(provider=SPACE, model='TRELLIS', seed=seed,
            source_path=data['image'], source_sha256=digest, confidence='unverified',
            inferred_surfaces=True, privacy='image_uploaded_to_huggingface')
        save_project(path, data, action='avatar3d_generated', actor=actor)
        stamp = path.stat().st_mtime_ns
        if prepare:
            return auto_prepare_avatar(path, actor=actor, progress=progress)
        update_workflow(path, 'needs_review', 'avatar3d_generation', 'Malla generada; revisa silueta, superficies inferidas y orientación antes del rig.', 100, task)
        return {'summary': 'Malla generada; requiere revisión visual.', 'avatar': load_project(path)['avatar3d']}
    except Exception as exc:
        if path.stat().st_mtime_ns == stamp:
            update_workflow(path, 'failed', 'avatar3d_generation', 'La generación no se completó; revisa conexión, cuota y proveedor.', 0, task)
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError('Falló TRELLIS. Comprueba conexión, cuota y disponibilidad del Space.') from exc
