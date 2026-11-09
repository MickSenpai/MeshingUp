# Verificación del flujo de agentes 3D — 2026-11-09

Resultado: pasan las pruebas del flujo implementado; no es una certificación de
calidad visual ni una evaluación autónoma completa de IA.

## Ejecutado

- `python -m unittest discover -s tests -v`: 6 pruebas, incluyendo solicitudes
  mal formadas, valores NaN/infinito, índices booleanos, hashes obsoletos y
  autorización de generación. El proveedor TRELLIS está simulado.
- `node viewer3d/check_hand_rig.mjs`: preservación de bind, pesos y movilidad.
- `node viewer3d/check_edit_history.mjs`: historial correcto.
- Ventana Qt offscreen: creación y acción de evaluación disponibles.
- Blender real sobre copia temporal de Meido: evaluación, tres renders,
  registro de revisión, rechazo de evidencia alterada, edición de pesos y
  traslación de vértices, conservación de fuente y dos clips, rechazo de
  revisión obsoleta y bloqueo de `ready` tras editar.
- Compilación Python y `git diff --check`: sin errores.

La prueba integral se reproduce con:

```bash
PYTHONPATH=src .venv/bin/python scripts/check_agent3d_integration.py /ruta/modelo.glb
```

Requiere Blender y un GLB con dos clips; crea sus artefactos en un directorio
 temporal independiente y no modifica el archivo de entrada.

## Correcciones durante la revisión

- Validación de solicitudes antes de ejecutar Blender.
- Invalidación de evidencias y revisiones al editar.
- Bloqueo de listo cuando faltan nuevas pruebas de deformación tras la edición.
- Comprobación del hash del avatar antes de guardar resultados.
- Los pesos sólo admiten huesos del armature asociado a la malla.

## Límites pendientes

No se probó una generación remota TRELLIS real. La revisión visual requiere un
agente externo que abra las imágenes. Las evidencias son de reposo y no incluyen
un barrido de poses animadas. No hay corrección autónoma semántica ni garantía de
que el cabello, dedos, ropa o articulaciones estén bien modelados. El caso Meido
conserva los problemas visuales identificados anteriormente; esta prueba verifica
la infraestructura sobre copias, no repara el personaje final.
