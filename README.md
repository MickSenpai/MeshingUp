# MeshingUp

MeshingUp es un editor de escritorio para preparar personajes 2D animables desde ilustraciones y revisar avatares 3D GLB/VRM. Incluye herramientas de rigging, animación reactiva, validación y una CLI para que personas y agentes trabajen sobre el mismo proyecto.

La meta del producto incluye una biblioteca gráfica que verifique qué rigs existen y muestre los trabajos en curso. La IA deberá poder llevar un proyecto de la imagen al rig, animaciones, validación y exportación, mientras la interfaz expone progreso, evidencia, errores y versiones.

## Prototipo actual

- Importa JPG, PNG, BMP y WebP.
- Presenta una biblioteca de rigs registrados con estado, etapa, progreso y última actualización; señala proyectos o imágenes faltantes.
- Permite registrar, abrir, validar y quitar de la biblioteca proyectos existentes sin borrar sus archivos.
- Permite colocar y mover pivotes encima de la imagen.
- Permite seleccionar dos pivotes y conectarlos con un hueso.
- Crea máscaras por pieza y permite pintarlas en el lienzo; clic izquierdo añade y clic derecho borra. La biblioteca muestra el conteo de capas.
- Extrae cada máscara como una pieza PNG transparente, rellena debajo de las piezas con inpainting clásico y presenta una vista compuesta revisable.
- Genera una malla triangular por pieza, asigna pesos normalizados a las articulaciones y deforma los sprites con skinning lineal en la vista previa. Al renderizar, limpia en memoria los píxeles originales de las piezas para evitar que quede una copia estática debajo.
- Incluye previsualización de rotación, ajuste de pesos por vértice, IK planar de dos huesos, clips con interpolación suave, expresiones faciales basadas en pivotes, opacidad y visibilidad animada por capa, validación de mallas y exportación de un bundle ZIP portable.
- Añade `BehaviorRuntime` para mezclar clips base con estados reactivos, mirada, parpadeo, gestos, voz normalizada y una marcha de inquietud con pasos alternados y recorrido lateral. La simulación del editor es temporal y `meshingupctl behavior render` genera PNG de evidencia.
- Añade un flujo de avatar 3D GLB/VRM con pestañas de vista 2D y 3D. El visor encuadra el modelo, muestra el esqueleto, permite ajustar pivotes y giros de huesos, y guarda las ediciones como una nueva versión GLB dentro del proyecto.
- El flujo recomendado crea o rehace el rig con Blender, estima las proporciones de personajes estilizados, calcula pesos automáticos y conserva la fuente. Comprueba que cada vértice tenga pesos normalizados, repara huecos, y coloca huesos centrales con la profundidad medida en la malla. Un postproceso inspirado en Mesh2Motion transfiere pesos de brazos mal asignados a la cabeza sobre el cuello, devuelve al torso influencias de brazo dentro del plano de hombros y refuerza el peso de `Hand` en la región distal de cada muñeca. Cuando la topología permite aislar el componente conectado de cada brazo, MeshingUp ajusta muñeca y cadenas de dedos a los vértices reales de la mano; si no, lo indica y conserva una disposición proporcional de respaldo. El informe registra las correcciones. Blender prueba rotaciones límite en hombros, brazos y piernas, mide cambios anómalos en las aristas de la malla y guarda el informe en el proyecto; si falla, MeshingUp avisa y no permite marcarlo como listo. El visor Three.js mantiene un auto-rig experimental sin Blender. La validación geométrica ayuda a localizar fallos, pero no sustituye la revisión visual. Consulta [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para atribución y licencia.
- **Analizar piezas extraíbles** inventaría islas desconectadas, las agrupa por regiones corporales aproximadas y señala componentes conectados que necesitarían cortes semánticos. Guarda el informe con el hash de la fuente y propone candidatos faciales; si ojos/boca están en una textura, advierte que no son geometría extraíble. Las etiquetas son propuestas de baja confianza, no separación automática ya aprobada.
- El visor 3D permite previsualizar estados de reposo, escucha, pensamiento, habla e inquietud. Estos controles sirven para revisar el movimiento; la conexión automática con eventos en vivo de Meido aún no está integrada. La reproducción de clips y los controles faciales dependen de las animaciones, huesos o blend shapes incluidos en el modelo.
- El auto-rig del visor Three.js crea un esqueleto humanoide y pesos desde la geometría localmente, sin Blender. Cuando faltan clips compatibles, genera clips glTF base para reposo, escucha, pensamiento, habla, espera, recuperación y ciclo de caminar, y los exporta junto con el GLB riggeado. La inferencia de proporciones y pesos es heurística; una revisión visual breve sigue siendo necesaria.
- Las posiciones de hombros, brazos, manos, dedos y pulgares se estiman desde muestras de vértices en cada lado del personaje y una orientación local inferida para la mano. El informe de auto-rig indica cuántas muestras encontró por lado; en poses cruzadas, manos ocultas o geometría estilizada puede fallar y requiere corrección manual.
- La biblioteca también puede crear proyectos `.meshingup` 3D-only directamente desde GLB/VRM. La imagen fuente 2D es opcional para esos proyectos; si falta en un proyecto 2D antiguo, MeshingUp aún puede abrir el avatar 3D y señala la fuente ausente.
- Los estados del visor mezclan clips disponibles con movimiento procedural. Las señales de voz normalizadas (`tts.viseme`, `speech.viseme` y `speech.level`) animan expresiones/blend shapes o la mandíbula cuando el modelo las incluye. MeshingUp no sintetiza voz ni deduce fonemas de texto/audio por sí solo; necesita recibir visemas o energía de un proveedor TTS.
- Puede separar ilustraciones anime en capas semánticas con See-through (servicio de Hugging Face), importar el PSD y mantener el JPG original. La app pide consentimiento antes de enviar una copia.
- Guarda y vuelve a abrir proyectos `.meshingup` versionados con una copia portable de la imagen fuente.
- Expone las operaciones disponibles con `meshingupctl`, para que un agente pueda inspeccionar y modificar el mismo proyecto que la interfaz, incluidas la creación de capas, la importación de máscaras PNG y sprites RGBA de lienzo completo.
- El lienzo admite zoom y desplazamiento.

Las máscaras se pintan manualmente, se importan como PNG monocromos o se proponen con SAM 2 usando puntos positivos/negativos o una caja. SAM 2 devuelve tres candidatos clasificados; se muestran como previsualizaciones no destructivas y sólo se sustituyen las máscaras al aceptar una. La inferencia es local y el primer uso puede descargar el modelo `facebook/sam2.1-hiera-tiny` de Hugging Face. De las máscaras aprobadas se extraen sprites RGBA y OpenCV rellena la zona que dejan en la imagen base. El inpainting clásico prolonga contexto local; no inventa de forma fiable anatomía o detalles semánticos, así que la vista compuesta requiere revisión.

## Investigación y criterios de calidad

La [investigación sobre rigging 2D](docs/INVESTIGACION_RIG_2D.md) recoge el flujo de preparación, capas y oclusión, deformaciones, cara, principios de animación, uso de IA, validación y las etapas que aún faltan.
La [guía de animación reactiva](docs/ANIMACION_REACTIVA.md) describe la API de eventos, la simulación de Meido y sus límites actuales.
El [estudio de avatar reactivo 2D/3D](docs/ESTUDIO_AVATAR_REACTIVO_2D_3D.md) compara rutas, formatos humanoides, mezcla de movimiento y una arquitectura recomendada para Meido.

## Preparar MeshingUp en otra PC

### Requisitos

- Git.
- Python 3.10 o posterior.
- Node.js 20.19 o posterior (LTS) y npm. El visor 3D local se compila desde `viewer3d/`.
- Windows 10/11 o una distribución Linux de escritorio. El visor 3D necesita WebGL; en escritorios Linux mínimos puede requerir bibliotecas de Qt WebEngine y gráficos del sistema.

No copies `.venv/` ni `node_modules/` desde otra PC: contienen dependencias específicas del sistema operativo. Ejecuta el instalador en cada equipo.

### Windows PowerShell

Después de publicar los cambios en GitHub, abre PowerShell y ejecuta:

```powershell
git clone https://github.com/MickSenpai/MeshingUp.git
cd MeshingUp
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
.\.venv\Scripts\meshingup.exe
```

Para abrir MeshingUp más adelante:

```powershell
cd ruta\a\MeshingUp
.\.venv\Scripts\meshingup.exe
```

### Linux

```bash
git clone https://github.com/MickSenpai/MeshingUp.git
cd MeshingUp
bash scripts/install_linux.sh
.venv/bin/meshingup
```

Para abrirlo después, ejecuta `.venv/bin/meshingup` desde la carpeta del repositorio. En Debian/Ubuntu el instalador intenta preparar las bibliotecas gráficas de Qt WebEngine con `apt-get`; en otras distribuciones, instala las bibliotecas Qt/XCB/WebEngine que indique el sistema.

### Transferir esta copia antes de publicarla

Los commits locales no aparecen al clonar GitHub hasta que se publiquen. Para llevar el estado actual del repositorio a otra PC sin esperar al push, crea un bundle en esta PC:

```bash
git bundle create work/MeshingUp-transfer.bundle main
```

Copia `work/MeshingUp-transfer.bundle` a la otra PC y clónalo desde la carpeta donde lo guardaste:

```bash
git clone MeshingUp-transfer.bundle MeshingUp
cd MeshingUp
```

Después instala las dependencias con las instrucciones de Windows o Linux de arriba. Si quieres conectar esa copia con GitHub cuando tengas autenticación, ejecuta `git remote set-url origin https://github.com/MickSenpai/MeshingUp.git` y luego sincroniza o publica la rama `main`.

### Llevar proyectos entre equipos

Cada proyecto guarda sus recursos con rutas relativas. Para trasladarlo, copia el archivo `.meshingup` **junto con la carpeta `assets/` que está a su lado**; ahí se guardan la imagen de referencia y los GLB/VRM importados o generados. Mantén esa estructura al copiarla a la otra PC. El código del programa y los proyectos son transferencias separadas: instala MeshingUp en la nueva PC y luego abre el `.meshingup` desde su biblioteca.

## Ejecutar

Los instaladores crean el entorno `.venv`, instalan MeshingUp con PyQt6/WebEngine y las dependencias de rigging, instalan las dependencias exactas del visor con `npm ci` y compilan Three.js. Requieren Python 3.10+ y Node.js 20.19+ con npm. Blender es externo y opcional: se usa para el flujo recomendado de auto-rig; el auto-rig experimental del visor puede ejecutarse sin él.

Para activar la reconstrucción local de zonas ocultas, instala el extra opcional: `python -m pip install -e '.[inpaint]'`.
Para activar la segmentación SAM 2, instala: `python -m pip install -e '.[segmentation]'`.
Para la separación completa anime con See-through, instala: `python -m pip install -e '.[decomposition]'` y autentícate con `.venv/bin/hf auth login`. El servicio recibe la imagen sólo después de aceptar el aviso de privacidad dentro de la app.
Para activar mallas, deformación, IK y render de poses, instala: `python -m pip install -e '.[rigging]'`.
En Arch Linux puedes instalar Blender con `flatpak install --user flathub org.blender.Blender`; MeshingUp también detecta un ejecutable `blender` instalado en `PATH`.
Para habilitar el visor 3D, instala el extra `avatar3d` con uno de los scripts de plataforma. La vista central ofrece pestañas **Vista 2D** y **Vista 3D**. Al importar un GLB/VRM, MeshingUp lo abre en el visor y ajusta su escala y encuadre. Para GLB sin esqueleto, usa **Crear rig con Blender**: genera el esqueleto y los pesos, conserva la fuente y somete el resultado a una prueba geométrica de deformación en poses límite. El resumen aparece en la interfaz y `meshingupctl avatar3d validate PROYECTO.meshingup` muestra el informe completo. Si falla, el proyecto permanece pendiente de revisión. Si Blender no está instalado, la CLI informa cómo instalarlo; también detecta instalaciones en `PATH` y Flatpak. **Auto-rig geométrico en visor · experimental** conserva la antigua alternativa Three.js. Para rehacer un rig guardado, el botón de Blender vuelve a usar el GLB fuente preservado. En el visor puedes ajustar pivotes y rotaciones; **Guardar rig/pose editados** exporta otra versión GLB con los clips generados y conserva los archivos previos. La prueba no repara automáticamente pesos ni recupera anatomía oculta: usa sus fallos para orientar la corrección y revisa visualmente las poses antes de animar.

El comando `meshingupctl avatar3d analyze-parts PROYECTO.meshingup` genera el inventario de piezas y propuestas corporales en Blender; el informe queda en el manifiesto y el JSON incluye componentes grandes, regiones y avisos. `meshingupctl --help` muestra además la interfaz estructurada para listar, crear, inspeccionar y validar proyectos; modificar pivotes, huesos, capas y progreso. Es la base para que un agente opere MeshingUp sin simular clics de ratón.

Ejemplo para un agente:

```bash
meshingupctl list --json
meshingupctl inspect ~/MeshingUp/Projects/Meido.meshingup
meshingupctl validate ~/MeshingUp/Projects/Meido.meshingup
meshingupctl status ~/MeshingUp/Projects/Meido.meshingup in_progress skeleton --progress 35 --task-id rig-001
meshingupctl layer add ~/MeshingUp/Projects/Meido.meshingup brazo_derecho
meshingupctl layer import-mask ~/MeshingUp/Projects/Meido.meshingup cabello cabello.png
meshingupctl layer extract ~/MeshingUp/Projects/Meido.meshingup layer-abc123
meshingupctl layer inpaint ~/MeshingUp/Projects/Meido.meshingup --method telea --radius 3
meshingupctl layer render ~/MeshingUp/Projects/Meido.meshingup /tmp/meido-preview.png
meshingupctl layer propose ~/MeshingUp/Projects/Meido.meshingup layer-abc123 --positive 400 350 --negative 450 400 --box 280 200 550 600
meshingupctl layer accept ~/MeshingUp/Projects/Meido.meshingup layer-abc123 proposal-0123456789ab
meshingupctl mesh generate ~/MeshingUp/Projects/Meido.meshingup layer-abc123 --spacing 20
meshingupctl clip create ~/MeshingUp/Projects/Meido.meshingup caminar --duration 60 --fps 30
meshingupctl clip key ~/MeshingUp/Projects/Meido.meshingup clip-abc123 joint-brazo 0 0
meshingupctl clip key ~/MeshingUp/Projects/Meido.meshingup clip-abc123 joint-brazo 30 35
meshingupctl pose render ~/MeshingUp/Projects/Meido.meshingup /tmp/pose.png --clip clip-abc123 --frame 15
meshingupctl export ~/MeshingUp/Projects/Meido.meshingup ~/MeshingUp/Exports/Meido.zip
meshingupctl decompose ~/MeshingUp/Projects/Meido.meshingup --allow-upload
```

`meshingupctl validate` devuelve código 0 para `ready`, 1 para `needs_review` y 2 cuando encuentra errores (`failed`). El informe JSON incluye los motivos.

Extrae cada pieza después de finalizar su máscara. Cuando todas las máscaras activas tienen una pieza actual, ejecuta el inpainting y abre **Ver composición reconstruida** para revisar el resultado. Esto rellena con píxeles vecinos; no sustituye una reconstrucción semántica. La validación detecta piezas ausentes o desactualizadas y no permite marcar `ready` mientras haya errores o advertencias.

Para proponer máscaras desde `meshingupctl`, instala el extra `segmentation`. `--positive x y` y `--negative x y` se pueden repetir; `--box` recibe `x1 y1 x2 y2`. Las propuestas se guardan en el proyecto y deben aprobarse con `meshingupctl layer accept`.

La separación completa anime requiere el extra `decomposition` y una sesión de Hugging Face. El comando `meshingupctl decompose` exige `--allow-upload`; la app solicita confirmación antes de transmitir la imagen. See-through devuelve un PSD con partes semánticas y reconstrucciones; MeshingUp conserva ese PSD como evidencia e importa las piezas como capas editables. En la app, **Agrupar capas generadas en una carpeta** viene activado: las piezas aparecen bajo un grupo desplegable en el panel de capas. La opción se puede desactivar antes de iniciar la separación. La calidad se debe revisar antes de construir el esqueleto.

## Uso

1. En **Biblioteca**, crea un rig desde una imagen, crea un proyecto 3D desde un GLB/VRM o registra un proyecto `.meshingup` existente.
2. Abre un proyecto para pasar al **Editor**.
3. Pulsa **Añadir pivote** y haz clic en cada articulación.
4. Selecciona dos pivotes con Ctrl y pulsa **Conectar selección**.
5. En **Capas / máscaras**, crea una capa, selecciónala y pulsa **Pintar máscara**. Pinta con clic izquierdo y borra con clic derecho; se guarda al soltar.
6. Elige **+ Punto** y marca la pieza que quieres conservar; añade **− Puntos** para excluir zonas o dibuja una **Caja**. Pulsa **Proponer máscara con SAM 2**, revisa los candidatos y acepta el adecuado.
7. Pulsa **Extraer pieza activa** para cortar la zona en un sprite transparente. Repite con las piezas que quieras animar.
8. Pulsa **Reconstruir zonas ocultas** y revisa la composición. El relleno es local y clásico, no una inferencia semántica.
9. Pulsa **Generar malla** por cada pieza; prueba la rotación y corrige pesos si hay pliegues o deriva.
10. Crea un clip, selecciona un pivote, define una rotación y guarda claves en distintos fotogramas. La línea de tiempo permite buscar fotogramas, avanzar o retroceder uno a uno, saltar al inicio/fin, reproducir hacia delante o atrás y elegir una velocidad de 0.5× a 8× (inicialmente 1.5×). Al guardar una clave se puede elegir interpolación cúbica monótona, suave por tramo o lineal.
11. Para alternar dibujos (por ejemplo, puño cerrado/mano abierta), importa cada variante como sprite de lienzo completo, selecciona la capa, cambia **Capa seleccionada visible en este fotograma** y guarda la **clave de visibilidad** en los fotogramas de cambio. La visibilidad usa pasos discretos, no mezcla dibujos.
12. Para IK selecciona raíz, codo/rodilla y extremo en ese orden, marca un objetivo en el lienzo y resuelve. El control **Flexionar al lado opuesto** elige la rama del codo. Guarda las rotaciones resultantes como claves para usarlas en el clip.
13. Para expresiones coloca pivotes faciales y crea capas alternativas (por ejemplo, ojos abiertos/cerrados o boca abierta/cerrada); ajusta la pose/opacidad y guarda una expresión.
14. Valida el proyecto y exporta el bundle ZIP portable cuando la composición y los movimientos se hayan revisado.

La validación comprueba referencias, máscaras, sprites y reconstrucciones. Marca el rig para revisión mientras falten deformaciones y animaciones; no declara terminado un rig incompleto.

## Limitaciones actuales

1. El generador propone una malla uniforme y pesos por distancia; no identifica anatomía ni crea automáticamente máscaras correctas. El usuario debe revisar y corregir la topología/pesos.
2. La IK resuelve cadenas planas de dos huesos. Las expresiones combinan rotaciones de pivotes y opacidad de capas; todavía no generan automáticamente formas de ojos o boca.
3. La exportación actual es un bundle portable propio de MeshingUp; no produce directamente archivos Rive, Live2D o Spine.
4. SAM 2 propone máscaras, pero no decide por sí solo la segmentación final. La opción See-through sí propone capas semánticas y reconstrucciones para ilustración anime, aunque sus resultados requieren revisión y el servicio se ejecuta en la nube.
5. El flujo asistido por IA puede operar mediante `meshingupctl`, pero aún falta el coordinador autónomo que planifica, ejecuta, compara poses y solicita revisión dentro de la biblioteca.
6. Faltan deshacer/rehacer, relleno semántico de zonas ocultas, cambios animados de orden de dibujo y validación visual automática con evidencia.

Las poses de manos y su procedencia se describen en [Poses de capa y manos](docs/POSES_DE_CAPA_Y_MANOS.md).

En el menú **Avatar 3D** hay dos botones más: **Reparar malla (Blender)** y **Preparar avatar completo (reparar → rig → animar)**. Equivalen a `meshingupctl avatar3d repair PROYECTO` (suelda vértices duplicados por costuras UV/normales y quita geometría muerta; no elimina islas salvo con `--min-island-vertices`, no rellena agujeros y no mueve la geometría) y a `meshingupctl avatar3d auto PROYECTO` (reparar, crear rig y añadir los clips `MeshingUp · Idle` y `MeshingUp · Wave`; `--clips none`, `--skip-repair`). La reparación genera un GLB nuevo y conserva el original (`original_path`/`original_sha256` en el manifiesto); si falla un paso después de reparar un avatar ya riggeado, se restaura el rig anterior. `auto` siempre termina en `needs_review`: las pruebas automáticas no sustituyen la revisión visual.

En el visor 3D, **Deshacer** y **Rehacer** (cabecera, junto a «Alinear vista»; Ctrl+Z / Ctrl+Shift+Z) recorren en orden las ediciones de pesos y de pivotes. Para probar su lógica: `node viewer3d/check_edit_history.mjs`.

### Parpadeo, boca y pelo en un GLB riggeado

`meshingupctl avatar3d enhance PROYECTO --spec mejora.json` (menú **Avatar 3D → Paso a paso → Añadir parpadeo, boca y pelo (JSON)…**) añade con Blender, sobre el GLB riggeado:

- **Morphs faciales** `blink`, `aa`, `ih`, `ou`, `ee`, `oh` para caras pintadas en la textura: párpados y boca son parches pequeños ajustados a la superficie de la cara (por debajo del flequillo), sin área en reposo. El visor ya parpadea y aplica visemas con esos nombres.
- **Huesos secundarios** (pelo, colas, lazos): hijos de un hueso existente que se reparten los pesos que ese hueso ya tenía, con una rampa a lo largo de cada hueso. El visor mueve con inercia los huesos cuyo nombre empieza por `hair`, `tail`, `ribbon`, `skirt`, `cape`, `scarf`, `ear`, `ahoge`, `ponytail`, `pigtail` o `braid` (casilla **Pelo con inercia**); no modifica el rig ni los clips.

```json
{
  "secondary_bones": {
    "parent": "head", "radius": 0.2, "smooth": 6,
    "color_like": {"point": [0.04, 0.36, 0.3], "tolerance": 0.2},
    "bones": [{"name": "hairBack", "head": [0.04, 0.3, 0.42], "tail": [0.04, 0.3, 0.14]}]
  },
  "face": {
    "bone": "head",
    "eyes": [{"name": "left", "center": [0.15, 0.44], "radius": [0.078, 0.064]}],
    "mouth": {"center": [0.0245, 0.3145], "width": 0.075, "angle": 6}
  }
}
```

Las coordenadas son las de Blender tras importar el GLB (X lateral, Y profundidad con la cara mirando a −Y, Z arriba), las mismas de `geometry.json`; `center` de ojos y boca es `[x, z]` en la vista frontal. Todo lo añadido es **reconstrucción**, no geometría observada: queda registrado en `avatar3d.enhancements` con la solicitud, el informe y el hash del GLB de origen, el GLB anterior se conserva, el proyecto vuelve a `needs_review` y la validación impide `ready` hasta registrar una revisión `pass` de esa versión. No crea dedos ni separa el pelo de los hombros.

`meshingupctl avatar3d create MODELO.glb PROYECTO.meshingup` crea un proyecto 3D-only sin pasar por la interfaz. `avatar3d animate` acepta además `--keyframes-file claves.json` (lista de `{frame, bone, rotation}`), y `avatar3d evidence --request tomas.json` renderiza, además de las tres vistas de reposo, hasta 24 tomas con poses (`pose`), morphs (`morphs`) o fotogramas de clips (`clip`); el esquema está en `avatar3d agent-contract`.

La CLI equivalente al botón de Blender es `meshingupctl avatar3d autorig PROYECTO`. Para que un agente genere un clip 3D, `meshingupctl avatar3d animate PROYECTO NOMBRE --duration 48 --fps 24` acepta claves repetidas `--keyframe FOTOGRAMA HUESO RX RY RZ`; esos ángulos XYZ se interpretan en los ejes semánticos normalizados del rig y se convierten a los ejes locales de cada hueso. Los clips anteriores conservan su duración en segundos al agregar clips con otra frecuencia; el visor permite seleccionar y reproducir clips por nombre. Por ejemplo, el agente puede dar claves para `leftUpperArm` en los fotogramas 0, 12, 24 y 48 para un saludo, previsualizarlo y ajustar sus ángulos. Las animaciones de habla usan señales de visemas/energía que debe enviar la integración TTS; MeshingUp aún no genera fonemas desde texto ni se conecta en vivo con Meido.

MeshingUp ya permite construir y revisar manualmente un rig deformable y animarlo; la automatización integral desde una sola imagen sigue siendo una etapa futura. La [investigación de rigging 2D](docs/INVESTIGACION_RIG_2D.md) recoge los criterios de calidad.

## Compatibilidad con proyectos anteriores al cambio de nombre

Este proyecto se llamó antes de otra forma. Los proyectos, la biblioteca y las carpetas creados entonces **siguen funcionando y no se mueven ni se renombran**:

- Los archivos con la extensión antigua (`.rigm`) y su marcador de formato interno se siguen abriendo; los proyectos nuevos usan `.meshingup`. Al guardar un proyecto antiguo sólo se actualiza el marcador interno, el archivo conserva su nombre.
- La biblioteca anterior (`~/.local/share/RigM/library.json`) se **copia** a `~/.local/share/MeshingUp/` la primera vez; la original queda intacta.
- La carpeta anterior de proyectos (`~/RigM/Projects`) se sigue explorando además de `~/MeshingUp/Projects`.
- Los clips ya incluidos en GLB antiguos conservan el nombre con el que se generaron.

Esa compatibilidad está en un solo sitio del código (`LEGACY_*` en `src/meshingup/project.py`) y se puede retirar cuando ya no haga falta.

En el visor 3D, **Pivotes → Mover sección completa (incluir descendientes)** mueve el hueso seleccionado y toda su jerarquía. Para recolocar una mano, selecciona `leftHand` o `rightHand`, activa esta opción y arrastra el punto amarillo con **Mover pivote con ratón**, o utiliza los controles de posición. Los dedos conservan sus posiciones relativas. La edición conserva la apariencia neutral de la malla y admite deshacer/rehacer; usa **Guardar rig/pose editados** para conservar el resultado. Desactiva la opción para editar un pivote aislado.

### Generación 3D desde imagen (experimental)

Instala `python -m pip install -e '.[generation3d]'`. Crea un proyecto desde
una imagen de cuerpo completo, preferiblemente con fondo transparente, y usa
el menú 3D → **Crear 3D desde imagen · TRELLIS en nube…**. El diálogo solicita
consentimiento para enviar la imagen a Hugging Face y permite encadenar Blender.
No reemplaza avatares existentes: crea un proyecto nuevo para otra versión.

CLI equivalente:

```bash
meshingupctl avatar3d generate personaje.meshingup --allow-upload --prepare
```

Sin `--prepare`, genera únicamente la malla. El adaptador utiliza
[trellis-community/TRELLIS](https://huggingface.co/spaces/trellis-community/TRELLIS),
requiere conexión y depende de su disponibilidad/cuota. Conserva la referencia,
registra hash, proveedor y semilla, y deja el resultado en `needs_review`.
Las superficies ocultas son inferidas. No incluye generación desde texto,
retopología semántica, rig facial ni corrección autónoma de pesos defectuosos.
Las pruebas locales simulan el proveedor; no certifican la generación remota.

### Evaluación y modelado por agentes (Codex, Claude u otros)

El menú 3D → **Evaluar avatar para agentes…** registra validación estructural y
pendientes. No invoca un proveedor de IA ni declara una inspección visual ficticia.
Un agente con acceso a la CLI y capacidad de ver imágenes puede seguir este ciclo:

```bash
meshingupctl avatar3d agent-contract
meshingupctl avatar3d evaluate personaje.meshingup
meshingupctl avatar3d evidence personaje.meshingup
meshingupctl avatar3d edit personaje.meshingup --request cambios.json
meshingupctl avatar3d evidence personaje.meshingup
meshingupctl avatar3d review personaje.meshingup --request revision.json
```

`agent-contract` devuelve el formato JSON de solicitudes. `evidence` usa Blender
local y guarda vistas frontal/posterior/lateral, nombres de mallas, huesos y
`geometry.json` con índices, coordenadas locales y pesos. El agente debe abrir las
imágenes antes de emitir un juicio visual. Las vistas actuales son de reposo;
no sustituyen las pruebas de poses límite ni una revisión de animaciones.

`edit` admite asignación normalizada de hasta cuatro pesos por vértice y traslación
de vértices seleccionados en coordenadas locales Blender. No ejecuta código del
agente. Requiere el hash exacto del GLB inspeccionado; los índices deben obtenerse
nuevamente después de exportar. Conserva versiones previas en `agent3d.versions` y
archivos originales, invalida diagnósticos del rig anterior y nunca marca `ready`.
No admite edición de VRM ni desplazamiento de vértices con shape keys.

`review` registra una declaración externa identificada por agente/modelo, hallazgos,
veredicto y evidencia con hashes. No prueba por sí sola que una IA haya visto las
imágenes ni convierte un `pass` visual en aprobación estructural. La integración
no es todavía un bucle autónomo de corrección ni un servidor MCP: agentes con acceso
a comandos pueden utilizarla sin automatizar la interfaz.
