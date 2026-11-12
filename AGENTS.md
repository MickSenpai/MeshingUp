# Instrucciones para agentes de MeshingUp

MeshingUp es una aplicación de escritorio para convertir ilustraciones en personajes 2D articulados. La visión completa es que un agente de IA pueda gestionar un proyecto de principio a fin y que la interfaz muestre rigs, estados, progreso, evidencias y errores verificables.

## Estado real del producto

- Editor PyQt6: importa una imagen, coloca/mueve pivotes, conecta huesos, crea y pinta máscaras monocromas, abre y guarda `.meshingup` portable.
- Biblioteca: registra proyectos, descubre `.meshingup` en `~/MeshingUp/Projects`, marca archivos e imágenes ausentes y muestra etapa/estado/progreso/conteo de capas.
- `meshingupctl`: CLI estructurada para crear, inspeccionar, validar y modificar proyectos, pivotes, huesos, capas, máscaras, sprites extraídos, reconstrucciones, clips 2D y animaciones GLB creadas con Blender. Usa operaciones compartidas con la interfaz y registra historial.
- La biblioteca detecta cambios externos y el editor impide sobrescribirlos sin recargar.
- Las máscaras se crean vacías y se pintan a mano o se importan desde PNG; SAM 2 local propone tres candidatos mediante puntos/cajas y requiere aceptación explícita. Instala `.[segmentation]` para PyTorch/Transformers y `.[inpaint]` para OpenCV. MeshingUp también ofrece mallas/pesos, deformaciones, expresiones, clips y exportadores.
- El flujo 3D recomendado usa Blender para generar huesos y pesos automáticos; MeshingUp abre su visor para probar clips base, editar pivotes y guardar GLB. Conserva la fuente y solicita revisión visual. El auto-rig geométrico Three.js sigue disponible como alternativa experimental sin Blender. Las señales de visemas/TTS deben venir de una integración externa.
- `meshingupctl avatar3d repair` suelda costuras duplicadas y quita geometría muerta de un GLB sin rig (el original se conserva y queda registrado); `meshingupctl avatar3d auto` encadena reparación, auto-rig Blender y clips base, actualiza etapa/progreso en el manifiesto y siempre termina en `needs_review`. No declares `ready` tras `auto`: exige revisión visual de poses.
- `meshingupctl avatar3d enhance --spec` añade morphs faciales (`blink`, `aa`, `ih`, `ou`, `ee`, `oh` y las expresiones `happy`, `sad`, `angry`, `surprised`), dedos separados con huesos de nombre VRM (`hands`: sustituye un bloque de dedos fusionado por cápsulas generadas) y huesos secundarios (pelo) a un GLB riggeado. Son reconstrucciones: quedan en `avatar3d.enhancements` con el hash de origen y la validación bloquea `ready` hasta que haya una revisión `pass` de esa versión. `avatar3d evidence --request` renderiza poses, morphs y fotogramas de clips; úsalo antes de registrar la revisión. El visor mueve con inercia los huesos `hair*`/`tail*`/… sin tocar el rig guardado.
- `meshingupctl avatar3d animate --keyframes-file` admite claves de hueso `{frame, bone, rotation}` y de morph `{frame, morph, value}`: un clip puede llevar boca y parpadeo propios. Los dedos se cierran con +Y en la mano izquierda y −Y en la derecha (ejes semánticos del rig); comprueba cada falange por separado antes de dar por buena una mano. `blink`, `happy`, `sad` y `angry` mueven los mismos párpados: no los sumes a 1 a la vez en un clip.
- `meshingupctl avatar3d analyze-parts` y la interfaz pueden inventariar islas desconectadas y proponer regiones corporales/candidatos faciales con confianza explícita. No presentes propuestas heurísticas como separación semántica confirmada; conserva el hash fuente y solicita revisión antes de extraer.
- Los proyectos 3D-only pueden omitir `image` e `image_size`, pero deben incluir un avatar GLB/VRM válido. No hagas obligatoria la imagen 2D para abrir o editar ese avatar.
- No declarar un proyecto como `ready` si la validación informa pendientes.

## Reglas de arquitectura

1. `meshingup.project` es la fuente de verdad para lectura, escritura, validación y operaciones del modelo. La interfaz y los agentes no deben mantener formatos diferentes.
2. Toda operación de agente debe tener argumentos estructurados, resultados JSON, errores claros, historial y cambios atómicos. Añade cada capacidad a `meshingupctl` sin automatizar clics de interfaz.
3. Mantén el `.meshingup` versionado, portable y con rutas relativas. Migra formatos previos cuando cambie el esquema; no pierdas archivos de usuario.
4. Registra etapa, estado, progreso, identificador de tarea y último resultado en el manifiesto para que la biblioteca pueda supervisar el trabajo.
5. Las inferencias de IA deben seguir siendo editables, conservar la fuente y distinguir reconstrucciones de píxeles observados.
6. El estado `ready` requiere que todas las validaciones obligatorias pasen; no lo fuerces para que la interfaz se vea completa.
7. Mantén la aplicación operativa sin una GPU o proveedor de IA. Los modelos de visión deben ser adaptadores opcionales y mostrar modelo, confianza y privacidad.
8. Al modificar máscaras desde la GUI, comprueba el manifiesto antes de escribir los píxeles y conserva historial del cambio.
9. Mantén la fuente intacta. Etiqueta sprites y reconstrucciones con hashes de fuente/máscara; si cambian, no los presentes como resultados actuales.

## Desarrollo y operación

Instalar el paquete desde la raíz del proyecto con `python -m pip install -e .`. El editor se inicia con `meshingup`; las operaciones para agentes se consultan con `meshingupctl --help`.

Comandos útiles desde la raíz:

```bash
meshingupctl list --json
meshingupctl validate /ruta/al/personaje.meshingup
meshingupctl inspect /ruta/al/personaje.meshingup
```

Para cambios de lógica del proyecto, valida el flujo CLI con un directorio temporal y abre una ventana Qt en modo offscreen cuando no haya escritorio disponible. No dejes imágenes o proyectos de smoke test en la biblioteca real.

## Diseño y calidad

Antes de implementar un paso de rigging, consulta [docs/INVESTIGACION_RIG_2D.md](docs/INVESTIGACION_RIG_2D.md). La pose neutral debe reconstruir la fuente sin costuras; cada articulación debe probarse en su rango límite; expresiones, parpadeo y voz deben combinarse; el movimiento debe revisarse a la escala de uso. El informe automático no sustituye la vista de evidencia.
