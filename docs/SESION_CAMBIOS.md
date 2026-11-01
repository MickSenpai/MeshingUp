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
