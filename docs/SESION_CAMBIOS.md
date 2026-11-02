# Registro de cambios de sesión

> Registro acumulativo de los cambios hechos en esta sesión de trabajo. Al continuar, añade una entrada con fecha, archivos afectados, comportamiento y validación. No reemplaces entradas anteriores.

## 2026-11-01 — soporte de proyectos 3D y edición del rig

### Proyectos GLB/VRM sin imagen 2D

- Se añadió la creación de proyectos `.rigm` directamente desde un GLB o VRM.
- Abrir un proyecto 3D-only ya no exige una imagen 2D. Si la imagen falta o no se puede leer pero existe un avatar 3D, RigM mantiene disponible la vista 3D y desactiva las herramientas 2D que necesitan esa imagen.
- Se ajustó el manejo de imagen faltante para mostrar un lienzo de respaldo y un error que indica la ruta al fallar la carga de una imagen.

### Rig y animación local sin Blender

- El visor Three.js genera un esqueleto humanoide geométrico y pesos por vértice para GLB estáticos, con clips base para estados y locomoción.
- La propuesta incluye cadenas aproximadas para dedos, pulgares y dedos de los pies, además de mandíbula. El nivel de confianza es bajo y hay que revisar las deformaciones en el modelo.
- Las animaciones de boca usan blend shapes o mandíbula disponible y señales de visema/energía externas. RigM no obtiene fonemas del texto o audio por sí solo.
- Se conserva la fuente del GLB para poder regenerar el rig y no sobrescribir silenciosamente el original.

### Corrección manual del rig

- El visor ofrece selección de huesos, controles de rotación y desplazamiento local del pivote, y guardado del resultado como otra versión GLB del proyecto.
- Se agregó un marcador amarillo sobre el hueso seleccionado para identificar el pivote durante el ajuste.
- Se agregó **Restaurar rig completo** para deshacer los giros y desplazamientos manuales de todos los huesos antes de guardar.

### Instalación y documentación

- Se añadieron scripts de instalación para Windows PowerShell y Linux, incluyendo la instalación del visor 3D local.
- Se acotó PyQt6 y PyQt6-WebEngine a `<6.12` por incompatibilidad binaria observada entre sus versiones Qt en Windows.
- Se actualizaron `README.md` y `AGENTS.md` para describir el flujo 3D y sus límites. Los temporales de trabajo se excluyeron de Git.

### Validación de esta entrada

- `npm run build` en `viewer3d/`: completado. Vite advierte que el bundle JS minificado supera 500 kB; no bloquea la compilación.
- `python -m py_compile src/rigm/avatar3d_viewer.py`: completado.
- No se ejecutó una suite de pruebas ni se verificó el instalador Linux en este entorno.

### Límites conocidos

- El auto-rig deduce anatomía y pesos desde la geometría; no recupera anatomía oculta y puede ubicar mal articulaciones en modelos estilizados.
- Los ajustes actuales editan huesos y pivotes existentes. Aún no hay pintura manual de pesos ni herramientas para remodelar o cortar la malla.
- La conexión de eventos de TTS/Meido en vivo sigue pendiente; las señales de voz han de venir de una integración externa.

## 2026-11-01 — ajuste local de manos y dedos

- Se reemplazaron las posiciones fijas de dedos por una estimación geométrica por mano: RigM busca vértices de la región distal del brazo, aproxima el centro de la mano y deduce dirección, ancho y longitud usando muestras locales.
- Los dedos y el pulgar ahora se distribuyen dentro de esa orientación local y sus articulaciones siguen la dirección estimada de la mano. El rig guarda en el informe si la inferencia tuvo muestras en cada lado.
- Se mantiene una advertencia explícita: la estimación puede fallar con manos ocultas, brazos cruzados, poses extremas o geometría estilizada y se debe revisar visualmente.
- La inspección del GLB compartido encontró muestras distales en ambos lados; el cambio usa esos grupos para orientar el ajuste de cada mano.
- La captura siguiente reveló que el detector anterior ubicaba hombros demasiado abajo y confundía vértices del torso con manos. Se añadió detección de brazos extendidos, una banda de muestreo alrededor de los hombros y articulaciones de brazo interpoladas entre hombro y muñeca.
- `npm run build` en `viewer3d/`: completado. Vite conserva el aviso no bloqueante sobre el tamaño del bundle.

## 2026-11-02 — preparación para trabajar desde otra PC

- Se documentaron requisitos, clonación e instalación en Windows y Linux, y cómo volver a iniciar RigM en cada sistema.
- Se explicó qué carpetas acompañan a un proyecto `.rigm` al trasladarlo (`assets/`) y por qué no se deben reutilizar `.venv/` ni `node_modules/` entre equipos.
- Como los commits actuales aún no están publicados en GitHub, se generó un Git bundle local para transferir el historial completo de `main`; se verificó con `git bundle verify`.

## 2026-11-02 — diagnóstico y profundidad del auto-rig 3D

- La revisión del proyecto Meido encontró pivotes del torso, cabeza y brazos con profundidad local `z = 0`, mientras que la malla fuente ocupa aproximadamente `0.81` unidades en profundidad. Esto explica que el esqueleto se superponga al mirar el modelo desde arriba o de lado.
- El mapeo ahora estima la mediana de profundidad de la geometría por franja vertical y usa ese perfil al colocar pivotes corporales. Los desplazamientos explícitos de mandíbula y pies se mantienen relativos a ese centro local.
- Los pesos por vértice ya no mezclan los cuatro segmentos globalmente más cercanos: primero asignan el segmento dominante y solo permiten influencias de segmentos conectados a ese segmento en la jerarquía. Esto limita el arrastre entre cadenas anatómicas sin cortar la malla ni crear costuras físicas.
- El `.rigm` revisado declara las proporciones nuevas de cuello/hombros, pero referencia un GLB de 20 huesos anterior al cambio documentado de dedos y pies; hay que regenerar el rig para comprobar el mapeo nuevo en ese proyecto.
- `npm run build` en `viewer3d/`: completado. Vite conserva el aviso no bloqueante sobre el tamaño del bundle. La revisión visual del GLB regenerado queda pendiente; la separación geométrica de piezas y edición manual de pesos siguen pendientes.

## 2026-11-02 — integración de Blender para rigging 3D

- Se conectó la interfaz 3D de RigM con el auto-rig Blender ya disponible en la CLI; el botón admite rigs nuevos y rehacerlos desde la fuente preservada. El resultado abre el visor para revisar poses y clips base, que se pueden guardar junto con el GLB.
- Se añadió `rigmctl avatar3d animate` con claves estructuradas por fotograma/hueso/rotación XYZ local; Blender exporta el clip como acción glTF, RigM comprueba que figure en el GLB y registra la operación en el historial del proyecto.
- El lanzador detecta Blender instalado directamente o como Flatpak de usuario/sistema y limita el acceso Flatpak al directorio del proyecto y al script de RigM.
- El script de Blender ahora estima proporciones de cabeza grande y desplaza torso, caderas y hombros para personajes chibi; los humanoides adultos conservan sus proporciones anteriores.
- Blender 5.2 LTS quedó instalado como Flatpak de usuario. Una prueba CLI temporal con Meido generó un rig de 49 huesos, detectó la proporción chibi, creó el clip `Prueba CLI` y pasó la validación GLB; se confirmó el historial `avatar3d_autorig_generated` y `avatar3d_animation_generated`.
- Otra prueba temporal rehízo el rig dos veces desde la fuente preservada y añadió dos clips secuencialmente; ambos quedaron en el GLB y la validación siguió sin errores.
- Blender no pudo resolver los pesos por calor para este modelo, por lo que RigM activó el fallback de segmentos óseos conectados y marcó la confianza como baja. El botón Blender aparece habilitado en una ventana Qt offscreen; falta revisar visualmente el personaje en movimiento.
- Excluir temporalmente los huesos de dedos y pies tampoco hizo que Blender resolviera los pesos por calor; el fallback limita las influencias y el informe deja explícita la confianza baja. `py_compile`, `git diff --check` y la construcción de la ventana Qt offscreen pasaron.

## 2026-11-02 — validación automática de deformaciones 3D

- El auto-rig Blender somete hombros, brazos, caderas y piernas a rotaciones de estrés en los tres ejes locales y ambos sentidos, y compara las longitudes de las aristas deformadas contra la pose neutral.
- El informe registra el hueso/eje/ángulo de cada pose, percentiles de estiramiento, proporción de aristas severamente alteradas y fallos. Si falla alguna pose, baja la confianza y la interfaz identifica cuántas poses requieren revisión.
- `rigmctl avatar3d validate` y la validación general del proyecto exponen el informe persistido y avisan que el rig no se puede marcar como listo hasta corregirlo.
- Esta fase es geométrica y determinista; no incorpora un servicio de visión/IA ni corrige pesos automáticamente. La revisión visual sigue siendo necesaria y el informe puede señalar falsos positivos en geometrías con costuras, accesorios o partes rígidas.
- Tras revisar la captura de Meido, se añadió un limitador espacial de influencia para que vértices de cabeza/cabello alejados del brazo no queden pesados a hombros/brazos/manos; si se quedan sin pesos, se reasignan al hueso central más cercano. La primera altura de corte afectó las mangas, así que se movió el límite por encima del cuello y se amplió la zona de tolerancia de brazo para conservar continuidad en el hombro. El cambio requiere rehacer el rig desde la fuente preservada para aplicarse al proyecto.
- Validación en copias temporales de dos GLB distintos (46.617 y 62.745 vértices): ambos generaron 49 huesos y aprobaron 60 poses de estrés; el auto-peso por calor falló en ambos y el fallback dejó la confianza en baja. Cada archivo es un solo objeto GLB, aunque Blender detecta 1.608 y 1.460 componentes desconectados respectivamente. Esto permite explorar asignación por piezas conectadas sin crear miles de objetos; la aprobación geométrica actual no sustituye una revisión visual ni garantiza generalización a cualquier modelo.

## 2026-11-02 — inventario automático de partes 3D

- Se añadió `rigmctl avatar3d analyze-parts` y el botón **Analizar piezas extraíbles**. Blender cuenta componentes conectados, mallas/materiales/texturas, propone regiones corporales por ubicación y registra candidatos geométricos de ojos/boca, límite de 50 componentes detallados y resumen agregado.
- La operación analiza el GLB fuente preservado, registra hash y resultado en el manifiesto/historial, y deja el proyecto `needs_review`. La validación avisa si el análisis deja de coincidir con el hash de la fuente.
- Se probó en copias de Meido y otro GLB: se encontraron 1.609/1.461 componentes en la importación de Blender, 168/141 islas cruzan regiones espaciales y requieren un corte semántico; ambos usan materiales con textura. Los candidatos faciales son zonas geométricas, no detección confirmada de ojos/boca. El inventario no modifica ni divide el GLB.
- Py_compile y `git diff --check` pasaron. Sigue pendiente usar segmentación semántica revisable para cortar componentes conectados y generar extractos independientes; las heurísticas no deben presentarse como separación final.
- Se corrigió `_render_current_pose` para proyectos 3D-only: no debe invocar el render 2D cuando falta una imagen. La ventana Qt offscreen abrió una copia de Meido y confirmó habilitado el botón de análisis.

## 2026-11-02 — corrección de pesos de cabeza y brazos

- Se reemplazó el limitador que quitaba influencias de brazo a distancia por un postproceso de pesos inspirado en los correctores de cabeza y brazo de Mesh2Motion. Sobre el cuello, las influencias de brazos/manos/dedos se trasladan a `head` con normalización; en la banda de torso, las influencias de brazo dentro del plano de hombros vuelven a los pesos existentes de torso o al hueso de torso más cercano.
- La corrección del torso excluye los huesos `Shoulder` para conservar la unión de las mangas. El plano se calcula en el eje lateral detectado y se restringe verticalmente entre cadera y cuello. Se registran el conteo de vértices modificados, límites geométricos y el método en el informe de auto-rig.
- Se incluyó atribución/licencia MIT en `THIRD_PARTY_NOTICES.md`; el código es una adaptación independiente, no se incorpora Mesh2Motion como dependencia.
- Se regeneraron rigs en copias temporales de Meido (46.617 vértices) y otro GLB (62.745). Las correcciones actuaron sobre 29.829/40.900 vértices por encima del límite de cuello y 691/553 dentro del plano torso-brazos; las 60 poses de estrés pasaron en ambos. La confianza sigue baja porque Blender falló en el cálculo automático por calor y se usaron pesos aproximados por distancia.
- `py_compile` y `git diff --check` pasaron. Falta comprobar visualmente estos GLB en el visor y animar rangos naturales: la prueba de aristas no demuestra por sí sola que mangas, cabello o accesorios se vean correctos.

## 2026-11-02 — seguimiento de manos al esqueleto

- La captura del visor mostró que las manos no seguían el movimiento de los huesos. El análisis de pesos del GLB confirmó la causa: el grupo `Hand` cubría sólo 11 vértices a la izquierda y 55 a la derecha; la mayor parte de la palma se asignaba a `LowerArm`.
- El postproceso ahora reconoce la zona distal usando la posición y distancia al segmento `Hand`, y eleva su influencia al 65% en palma/muñeca. Omite la región si ya tiene peso suficiente en dedos para no aplastarlos sobre la palma. La lógica previa de cabeza/torso y la preservación del hombro se mantienen.
- En Meido, el grupo `Hand` pasó a cubrir 591/396 vértices al importar el GLB corregido; la región reforzada reportó 579/340 vértices. Un segundo modelo reportó 594/535 vértices reforzados. Las 60 poses de estrés pasaron en ambos; `py_compile` y `git diff --check` pasaron.
- La nueva versión aumenta la deformación del hueso `Hand`, pero no prueba visualmente cada dedo ni la muñeca en poses extremas. Se requiere reabrir/regenerar el avatar desde su GLB fuente y revisar giros de muñeca y dedos en el visor.
- Una captura superior posterior mostró que, aunque las muñecas seguían los huesos, la orientación de los dedos estaba girada: el generador los proyectaba hacia el frente del personaje. Se reorientaron las cadenas de dedos para continuar hacia fuera desde la palma, con la separación entre dedos a través de la profundidad de la mano; también se giró el abanico del pulgar para acompañar esa convención. El informe ahora registra `hand_bone_layout: lateral_fingers_v2`.
- El rig Meido se regeneró en una copia temporal con esta geometría. Las 60 poses estructurales pasaron y se mantuvo el refuerzo de 579/340 vértices en la región distal. `py_compile` y `git diff --check` pasaron. Aún hace falta comparar visualmente la orientación con la pose original del modelo: la geometría de dedos es aproximada y esta actualización no modifica el GLB de usuario hasta regenerar desde la fuente.
- La inspección ampliada reveló que el respaldo aún generaba cadenas casi coincidentes porque una ventana de altura excluía los vértices reales de la mano. Ahora Blender localiza el componente conectado que sobresale a cada lado y toma muestras de la profundidad y la silueta distal para colocar muñeca y cuatro cadenas de dedos; si no encuentra ese componente lo declara en `hand_finger_layout_by_side` y agrega una advertencia.
- En Meido se identificaron componentes de 1.327/1.192 vértices para los dos brazos; el detector encontró cuatro trazas por mano y registró `mesh_point_cloud` en ambos lados. Las 60 poses de estrés pasaron; `py_compile` y `git diff --check` pasaron. La verificación cuantitativa aún no reemplaza revisar la superposición en el visor.
- Esta medición reemplaza como ruta principal la colocación `lateral_fingers_v2` basada en proporciones; esa versión queda sólo como respaldo cuando la topología no permite aislar un componente por brazo. El informe `connected_arm_surface_v3` guarda tamaños de componentes, cantidad de trazas detectadas y si cada lado usó geometría o respaldo.
- Estado para investigación: los rigs de validación están en `/tmp/rigm-autorig-validation-8q9ok053/` y `/tmp/rigm-autorig-validation-x769yrxn/`; son copias temporales. El GLB del proyecto del usuario no se ha sobrescrito. Hay que confirmar visualmente la superposición en el visor después de regenerar desde el GLB fuente.
