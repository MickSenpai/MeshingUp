# RigM

RigM es un editor de escritorio para preparar personajes 2D animables desde ilustraciones y revisar avatares 3D GLB/VRM. Incluye herramientas de rigging, animación reactiva, validación y una CLI para que personas y agentes trabajen sobre el mismo proyecto.

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
- Añade `BehaviorRuntime` para mezclar clips base con estados reactivos, mirada, parpadeo, gestos, voz normalizada y una marcha de inquietud con pasos alternados y recorrido lateral. La simulación del editor es temporal y `rigmctl behavior render` genera PNG de evidencia.
- Añade un flujo de avatar 3D GLB/VRM con pestañas de vista 2D y 3D. El visor encuadra el modelo, muestra el esqueleto, permite ajustar pivotes y giros de huesos, y guarda las ediciones como una nueva versión GLB dentro del proyecto.
- El flujo recomendado crea o rehace el rig con Blender, estima las proporciones de personajes estilizados, calcula pesos automáticos y conserva la fuente. Un postproceso inspirado en Mesh2Motion transfiere pesos de brazos mal asignados a la cabeza sobre el cuello, devuelve al torso influencias de brazo dentro del plano de hombros y refuerza el peso de `Hand` en la región distal de cada muñeca. Cuando la topología permite aislar el componente conectado de cada brazo, RigM ajusta muñeca y cadenas de dedos a los vértices reales de la mano; si no, lo indica y conserva una disposición proporcional de respaldo. El informe registra las correcciones. Blender prueba rotaciones límite en hombros, brazos y piernas, mide cambios anómalos en las aristas de la malla y guarda el informe en el proyecto; si falla, RigM avisa y no permite marcarlo como listo. El visor Three.js mantiene un auto-rig experimental sin Blender. La validación geométrica ayuda a localizar fallos, pero no sustituye la revisión visual. Consulta [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para atribución y licencia.
- **Analizar piezas extraíbles** inventaría islas desconectadas, las agrupa por regiones corporales aproximadas y señala componentes conectados que necesitarían cortes semánticos. Guarda el informe con el hash de la fuente y propone candidatos faciales; si ojos/boca están en una textura, advierte que no son geometría extraíble. Las etiquetas son propuestas de baja confianza, no separación automática ya aprobada.
- El visor 3D permite previsualizar estados de reposo, escucha, pensamiento, habla e inquietud. Estos controles sirven para revisar el movimiento; la conexión automática con eventos en vivo de Meido aún no está integrada. La reproducción de clips y los controles faciales dependen de las animaciones, huesos o blend shapes incluidos en el modelo.
- El auto-rig del visor Three.js crea un esqueleto humanoide y pesos desde la geometría localmente, sin Blender. Cuando faltan clips compatibles, genera clips glTF base para reposo, escucha, pensamiento, habla, espera, recuperación y ciclo de caminar, y los exporta junto con el GLB riggeado. La inferencia de proporciones y pesos es heurística; una revisión visual breve sigue siendo necesaria.
- Las posiciones de hombros, brazos, manos, dedos y pulgares se estiman desde muestras de vértices en cada lado del personaje y una orientación local inferida para la mano. El informe de auto-rig indica cuántas muestras encontró por lado; en poses cruzadas, manos ocultas o geometría estilizada puede fallar y requiere corrección manual.
- La biblioteca también puede crear proyectos `.rigm` 3D-only directamente desde GLB/VRM. La imagen fuente 2D es opcional para esos proyectos; si falta en un proyecto 2D antiguo, RigM aún puede abrir el avatar 3D y señala la fuente ausente.
- Los estados del visor mezclan clips disponibles con movimiento procedural. Las señales de voz normalizadas (`tts.viseme`, `speech.viseme` y `speech.level`) animan expresiones/blend shapes o la mandíbula cuando el modelo las incluye. RigM no sintetiza voz ni deduce fonemas de texto/audio por sí solo; necesita recibir visemas o energía de un proveedor TTS.
- Puede separar ilustraciones anime en capas semánticas con See-through (servicio de Hugging Face), importar el PSD y mantener el JPG original. La app pide consentimiento antes de enviar una copia.
- Guarda y vuelve a abrir proyectos `.rigm` versionados con una copia portable de la imagen fuente.
- Expone las operaciones disponibles con `rigmctl`, para que un agente pueda inspeccionar y modificar el mismo proyecto que la interfaz, incluidas la creación de capas, la importación de máscaras PNG y sprites RGBA de lienzo completo.
- El lienzo admite zoom y desplazamiento.

Las máscaras se pintan manualmente, se importan como PNG monocromos o se proponen con SAM 2 usando puntos positivos/negativos o una caja. SAM 2 devuelve tres candidatos clasificados; se muestran como previsualizaciones no destructivas y sólo se sustituyen las máscaras al aceptar una. La inferencia es local y el primer uso puede descargar el modelo `facebook/sam2.1-hiera-tiny` de Hugging Face. De las máscaras aprobadas se extraen sprites RGBA y OpenCV rellena la zona que dejan en la imagen base. El inpainting clásico prolonga contexto local; no inventa de forma fiable anatomía o detalles semánticos, así que la vista compuesta requiere revisión.

## Investigación y criterios de calidad

La [investigación sobre rigging 2D](docs/INVESTIGACION_RIG_2D.md) recoge el flujo de preparación, capas y oclusión, deformaciones, cara, principios de animación, uso de IA, validación y las etapas que aún faltan.
La [guía de animación reactiva](docs/ANIMACION_REACTIVA.md) describe la API de eventos, la simulación de Meido y sus límites actuales.
El [estudio de avatar reactivo 2D/3D](docs/ESTUDIO_AVATAR_REACTIVO_2D_3D.md) compara rutas, formatos humanoides, mezcla de movimiento y una arquitectura recomendada para Meido.

## Preparar RigM en otra PC

### Requisitos

- Git.
- Python 3.10 o posterior.
- Node.js 20.19 o posterior (LTS) y npm. El visor 3D local se compila desde `viewer3d/`.
- Windows 10/11 o una distribución Linux de escritorio. El visor 3D necesita WebGL; en escritorios Linux mínimos puede requerir bibliotecas de Qt WebEngine y gráficos del sistema.

No copies `.venv/` ni `node_modules/` desde otra PC: contienen dependencias específicas del sistema operativo. Ejecuta el instalador en cada equipo.

### Windows PowerShell

Después de publicar los cambios en GitHub, abre PowerShell y ejecuta:

```powershell
git clone https://github.com/MickSenpai/RigM.git
cd RigM
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
.\.venv\Scripts\rigm.exe
```

Para abrir RigM más adelante:

```powershell
cd ruta\a\RigM
.\.venv\Scripts\rigm.exe
```

### Linux

```bash
git clone https://github.com/MickSenpai/RigM.git
cd RigM
bash scripts/install_linux.sh
.venv/bin/rigm
```

Para abrirlo después, ejecuta `.venv/bin/rigm` desde la carpeta del repositorio. En Debian/Ubuntu el instalador intenta preparar las bibliotecas gráficas de Qt WebEngine con `apt-get`; en otras distribuciones, instala las bibliotecas Qt/XCB/WebEngine que indique el sistema.

### Transferir esta copia antes de publicarla

Los commits locales no aparecen al clonar GitHub hasta que se publiquen. Para llevar el estado actual del repositorio a otra PC sin esperar al push, crea un bundle en esta PC:

```bash
git bundle create work/RigM-transfer.bundle main
```

Copia `work/RigM-transfer.bundle` a la otra PC y clónalo desde la carpeta donde lo guardaste:

```bash
git clone RigM-transfer.bundle RigM
cd RigM
```

Después instala las dependencias con las instrucciones de Windows o Linux de arriba. Si quieres conectar esa copia con GitHub cuando tengas autenticación, ejecuta `git remote set-url origin https://github.com/MickSenpai/RigM.git` y luego sincroniza o publica la rama `main`.

### Llevar proyectos entre equipos

Cada proyecto guarda sus recursos con rutas relativas. Para trasladarlo, copia el archivo `.rigm` **junto con la carpeta `assets/` que está a su lado**; ahí se guardan la imagen de referencia y los GLB/VRM importados o generados. Mantén esa estructura al copiarla a la otra PC. El código del programa y los proyectos son transferencias separadas: instala RigM en la nueva PC y luego abre el `.rigm` desde su biblioteca.

## Ejecutar

Los instaladores crean el entorno `.venv`, instalan RigM con PyQt6/WebEngine y las dependencias de rigging, instalan las dependencias exactas del visor con `npm ci` y compilan Three.js. Requieren Python 3.10+ y Node.js 20.19+ con npm. Blender es externo y opcional: se usa para el flujo recomendado de auto-rig; el auto-rig experimental del visor puede ejecutarse sin él.

Para activar la reconstrucción local de zonas ocultas, instala el extra opcional: `python -m pip install -e '.[inpaint]'`.
Para activar la segmentación SAM 2, instala: `python -m pip install -e '.[segmentation]'`.
Para la separación completa anime con See-through, instala: `python -m pip install -e '.[decomposition]'` y autentícate con `.venv/bin/hf auth login`. El servicio recibe la imagen sólo después de aceptar el aviso de privacidad dentro de la app.
Para activar mallas, deformación, IK y render de poses, instala: `python -m pip install -e '.[rigging]'`.
En Arch Linux puedes instalar Blender con `flatpak install --user flathub org.blender.Blender`; RigM también detecta un ejecutable `blender` instalado en `PATH`.
Para habilitar el visor 3D, instala el extra `avatar3d` con uno de los scripts de plataforma. La vista central ofrece pestañas **Vista 2D** y **Vista 3D**. Al importar un GLB/VRM, RigM lo abre en el visor y ajusta su escala y encuadre. Para GLB sin esqueleto, usa **Crear rig con Blender**: genera el esqueleto y los pesos, conserva la fuente y somete el resultado a una prueba geométrica de deformación en poses límite. El resumen aparece en la interfaz y `rigmctl avatar3d validate PROYECTO.rigm` muestra el informe completo. Si falla, el proyecto permanece pendiente de revisión. Si Blender no está instalado, la CLI informa cómo instalarlo; también detecta instalaciones en `PATH` y Flatpak. **Auto-rig geométrico en visor · experimental** conserva la antigua alternativa Three.js. Para rehacer un rig guardado, el botón de Blender vuelve a usar el GLB fuente preservado. En el visor puedes ajustar pivotes y rotaciones; **Guardar rig/pose editados** exporta otra versión GLB con los clips generados y conserva los archivos previos. La prueba no repara automáticamente pesos ni recupera anatomía oculta: usa sus fallos para orientar la corrección y revisa visualmente las poses antes de animar.

El comando `rigmctl avatar3d analyze-parts PROYECTO.rigm` genera el inventario de piezas y propuestas corporales en Blender; el informe queda en el manifiesto y el JSON incluye componentes grandes, regiones y avisos. `rigmctl --help` muestra además la interfaz estructurada para listar, crear, inspeccionar y validar proyectos; modificar pivotes, huesos, capas y progreso. Es la base para que un agente opere RigM sin simular clics de ratón.

Ejemplo para un agente:

```bash
rigmctl list --json
rigmctl inspect ~/RigM/Projects/Meido.rigm
rigmctl validate ~/RigM/Projects/Meido.rigm
rigmctl status ~/RigM/Projects/Meido.rigm in_progress skeleton --progress 35 --task-id rig-001
rigmctl layer add ~/RigM/Projects/Meido.rigm brazo_derecho
rigmctl layer import-mask ~/RigM/Projects/Meido.rigm cabello cabello.png
rigmctl layer extract ~/RigM/Projects/Meido.rigm layer-abc123
rigmctl layer inpaint ~/RigM/Projects/Meido.rigm --method telea --radius 3
rigmctl layer render ~/RigM/Projects/Meido.rigm /tmp/meido-preview.png
rigmctl layer propose ~/RigM/Projects/Meido.rigm layer-abc123 --positive 400 350 --negative 450 400 --box 280 200 550 600
rigmctl layer accept ~/RigM/Projects/Meido.rigm layer-abc123 proposal-0123456789ab
rigmctl mesh generate ~/RigM/Projects/Meido.rigm layer-abc123 --spacing 20
rigmctl clip create ~/RigM/Projects/Meido.rigm caminar --duration 60 --fps 30
rigmctl clip key ~/RigM/Projects/Meido.rigm clip-abc123 joint-brazo 0 0
rigmctl clip key ~/RigM/Projects/Meido.rigm clip-abc123 joint-brazo 30 35
rigmctl pose render ~/RigM/Projects/Meido.rigm /tmp/pose.png --clip clip-abc123 --frame 15
rigmctl export ~/RigM/Projects/Meido.rigm ~/RigM/Exports/Meido.zip
rigmctl decompose ~/RigM/Projects/Meido.rigm --allow-upload
```

Extrae cada pieza después de finalizar su máscara. Cuando todas las máscaras activas tienen una pieza actual, ejecuta el inpainting y abre **Ver composición reconstruida** para revisar el resultado. Esto rellena con píxeles vecinos; no sustituye una reconstrucción semántica. La validación detecta piezas ausentes o desactualizadas y no permite marcar `ready` mientras haya errores o advertencias.

Para proponer máscaras desde `rigmctl`, instala el extra `segmentation`. `--positive x y` y `--negative x y` se pueden repetir; `--box` recibe `x1 y1 x2 y2`. Las propuestas se guardan en el proyecto y deben aprobarse con `rigmctl layer accept`.

La separación completa anime requiere el extra `decomposition` y una sesión de Hugging Face. El comando `rigmctl decompose` exige `--allow-upload`; la app solicita confirmación antes de transmitir la imagen. See-through devuelve un PSD con partes semánticas y reconstrucciones; RigM conserva ese PSD como evidencia e importa las piezas como capas editables. En la app, **Agrupar capas generadas en una carpeta** viene activado: las piezas aparecen bajo un grupo desplegable en el panel de capas. La opción se puede desactivar antes de iniciar la separación. La calidad se debe revisar antes de construir el esqueleto.

## Uso

1. En **Biblioteca**, crea un rig desde una imagen, crea un proyecto 3D desde un GLB/VRM o registra un proyecto `.rigm` existente.
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
3. La exportación actual es un bundle portable propio de RigM; no produce directamente archivos Rive, Live2D o Spine.
4. SAM 2 propone máscaras, pero no decide por sí solo la segmentación final. La opción See-through sí propone capas semánticas y reconstrucciones para ilustración anime, aunque sus resultados requieren revisión y el servicio se ejecuta en la nube.
5. El flujo asistido por IA puede operar mediante `rigmctl`, pero aún falta el coordinador autónomo que planifica, ejecuta, compara poses y solicita revisión dentro de la biblioteca.
6. Faltan deshacer/rehacer, relleno semántico de zonas ocultas, cambios animados de orden de dibujo y validación visual automática con evidencia.

Las poses de manos y su procedencia se describen en [Poses de capa y manos](docs/POSES_DE_CAPA_Y_MANOS.md).

La CLI equivalente al botón de Blender es `rigmctl avatar3d autorig PROYECTO`. Para que un agente genere un clip 3D, `rigmctl avatar3d animate PROYECTO NOMBRE --duration 48 --fps 24` acepta claves repetidas `--keyframe FOTOGRAMA HUESO RX RY RZ`; los ángulos son grados XYZ locales al hueso. El GLB conserva los clips previos y RigM puede reproducir el nuevo. Por ejemplo, el agente puede dar claves para `leftUpperArm` en los fotogramas 0, 12, 24 y 48 para un saludo, previsualizarlo y ajustar sus ángulos. Las animaciones de habla usan señales de visemas/energía que debe enviar la integración TTS; RigM aún no genera fonemas desde texto ni se conecta en vivo con Meido.

RigM ya permite construir y revisar manualmente un rig deformable y animarlo; la automatización integral desde una sola imagen sigue siendo una etapa futura. La [investigación de rigging 2D](docs/INVESTIGACION_RIG_2D.md) recoge los criterios de calidad.
