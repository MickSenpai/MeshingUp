"""Optional Qt WebEngine window for the bundled local VRM viewer."""

from __future__ import annotations

import json
import base64
import io
import tempfile
import uuid
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QFile, QIODevice, QUrl, Qt, QObject, QRunnable, QThreadPool, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSlider, QSplitter, QVBoxLayout, QWidget

# Qt WebEngine must be imported before QApplication is instantiated. Importing
# these lazily from Avatar3DDialog.__init__ is too late in the normal GUI flow.
try:
    from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage, QWebEngineScript
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebChannel import QWebChannel
    _WEBENGINE_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # keep the 2D editor usable without the optional extra
    QWebEngineSettings = QWebEnginePage = QWebEngineScript = QWebEngineView = QWebChannel = None
    _WEBENGINE_IMPORT_ERROR = exc

from .avatar_behavior import AvatarBehaviorPlanner
from .behavior import BEHAVIOR_STATES
from .project import ProjectError, load_project
from .avatar3d import attach_avatar


STATE_LABELS = {
    "idle": "Reposo", "listening": "Escuchando", "thinking": "Procesando",
    "speaking": "Hablando", "waiting": "Esperando", "restless": "Nerviosa · paseando", "error": "Error",
}


def webengine_available() -> bool:
    return _WEBENGINE_IMPORT_ERROR is None


class Avatar3DDialog(QDialog):
    """Interactive local GLB/VRM viewer and host for the browser-side auto-rigger."""

    autorig_saved = pyqtSignal(object)
    model_loading_finished = pyqtSignal()
    autorig_failed = pyqtSignal(str)

    def __init__(
        self, model_path: str | Path, model_name: str = "Avatar 3D", parent=None,
        *, project_path: str | Path | None = None, expected_mtime_ns: int | None = None,
        allow_autorig: bool = False, forward_axis: str = "+Z", rigging_report: dict[str, Any] | None = None,
    ) -> None:
        if _WEBENGINE_IMPORT_ERROR is not None:
            exc = _WEBENGINE_IMPORT_ERROR
            raise ProjectError(
                "No se pudo importar Qt WebEngine en este intérprete:\n"
                f"{sys.executable}\nDetalle: {exc}\n"
                "Instálalo en este mismo entorno con:\n"
                f"{sys.executable} -m pip install -e '.[avatar3d]'"
            ) from exc
        super().__init__(parent)
        self.model_path = Path(model_path).expanduser().resolve()
        self.project_path = Path(project_path).expanduser().resolve() if project_path else None
        self.expected_mtime_ns = expected_mtime_ns
        self.allow_autorig = allow_autorig
        self.rigging_report = rigging_report if isinstance(rigging_report, dict) else {}
        self.forward_axis = forward_axis if forward_axis in {"+Z", "-Z", "+X", "-X"} else "+Z"
        self.page_ready = False
        self.model_ready = False
        self.autorig_requested = False
        self._sam2_confirmed = False
        self.planner = AvatarBehaviorPlanner()
        self.setWindowTitle(f"MeshingUp · Avatar 3D · {model_name}")
        self.resize(980, 760)
        self.setMinimumSize(540, 430)
        self.web = QWebEngineView(self)
        self.web.setMinimumHeight(260)

        owner = self
        class DiagnosticPage(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, message, line, source):
                # PyQt6 passes JavaScriptConsoleMessageLevel (an enum), not an
                # int. Never let an exception escape a Qt virtual callback:
                # PyQt may abort the whole process for an uncaught callback error.
                try:
                    severity = getattr(level, "value", level)
                    if severity in ("error", "Error"):
                        is_error = True
                    else:
                        is_error = int(severity) >= 2
                    if is_error:
                        owner.message.setText(f"Visor 3D · {message} (línea {line})")
                except Exception as exc:
                    try:
                        owner.message.setText(f"Error interno del visor · {message} · {exc}")
                    except Exception:
                        pass

        self.page = DiagnosticPage(self.web)
        self.web.setPage(self.page)
        channel_script_file = QFile(":/qtwebchannel/qwebchannel.js")
        if not channel_script_file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise ProjectError("Qt WebEngine no pudo abrir el adaptador local de comunicación (qwebchannel.js).")
        channel_script = QWebEngineScript()
        channel_script.setName("MeshingUp Qt WebChannel bootstrap")
        channel_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        channel_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        channel_script.setRunsOnSubFrames(False)
        channel_script.setSourceCode(bytes(channel_script_file.readAll()).decode("utf-8"))
        self.page.scripts().insert(channel_script)
        channel_script_file.close()
        self.bridge = Avatar3DBridge(self.project_path, self.expected_mtime_ns, self) if self.project_path else None
        self.channel = QWebChannel(self.page)
        if self.bridge:
            self.channel.registerObject("meshingupHost", self.bridge)
            self.bridge.saved.connect(self._autorig_saved)
            self.bridge.failed.connect(self._autorig_failed)
            self.bridge.progress.connect(self._on_progress)
            self.bridge.history_changed.connect(self._set_history_buttons)
            self.bridge.hand_mask_ready.connect(self._on_hand_mask_ready)
        self.page.setWebChannel(self.channel)
        settings = self.web.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.web.loadFinished.connect(self._on_page_loaded)
        for keys, handler in (("Ctrl+Z", self._undo_edit), ("Ctrl+Shift+Z", self._redo_edit), ("Ctrl+Y", self._redo_edit)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)

        controls = QHBoxLayout()
        self.state_combo = QComboBox()
        for state in BEHAVIOR_STATES:
            self.state_combo.addItem(STATE_LABELS[state], state)
        self.state_combo.currentIndexChanged.connect(self._send_state)
        controls.addWidget(QLabel("Conducta"))
        controls.addWidget(self.state_combo)
        clip_controls = QHBoxLayout()
        clip_controls.addWidget(QLabel("Clip GLB"))
        self.clip_combo = QComboBox()
        self.clip_combo.setEnabled(False)
        self.clip_combo.setMinimumWidth(100)
        clip_controls.addWidget(self.clip_combo, 1)
        self.play_clip_button = QPushButton("Reproducir clip")
        self.play_clip_button.setEnabled(False)
        self.play_clip_button.clicked.connect(self._play_selected_clip)
        clip_controls.addWidget(self.play_clip_button)
        self.intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self.intensity_slider.setRange(0, 100)
        self.intensity_slider.setValue(35)
        self.intensity_slider.valueChanged.connect(self._send_intensity)
        intensity_controls = QHBoxLayout()
        intensity_controls.addWidget(QLabel("Intensidad"))
        intensity_controls.addWidget(self.intensity_slider, 1)
        self.autorig_button = None
        action_controls = QHBoxLayout()
        if self.allow_autorig:
            self.autorig_button = QPushButton("Crear esqueleto sin Blender")
            self.autorig_button.setToolTip("Analiza la geometría por lotes y propone huesos y pesos.")
            self.autorig_button.clicked.connect(self.request_autorig)
            action_controls.addWidget(self.autorig_button)
        self.skeleton_button = QPushButton("Ocultar esqueleto")
        self.skeleton_button.setCheckable(True)
        self.skeleton_button.setChecked(True)
        self.skeleton_button.setEnabled(False)
        self.skeleton_button.toggled.connect(self._toggle_skeleton)
        action_controls.addWidget(self.skeleton_button)

        bone_controls = QHBoxLayout()
        bone_controls.addWidget(QLabel("Hueso"))
        self.bone_combo = QComboBox()
        self.bone_combo.setMinimumWidth(110)
        self.bone_combo.setMaximumWidth(250)
        self.bone_combo.setToolTip("Huesos agrupados por región corporal. Los encabezados no son seleccionables.")
        self.bone_combo.setEnabled(False)
        self.bone_combo.currentTextChanged.connect(self._select_bone)
        bone_controls.addWidget(self.bone_combo)
        self.focus_bone_button = QPushButton("Enfocar")
        self.focus_bone_button.setToolTip("Centra y acerca la cámara al hueso seleccionado.")
        self.focus_bone_button.setEnabled(False)
        self.focus_bone_button.clicked.connect(lambda: self._run("window.meshingupAvatar?.focusBone(" + json.dumps(self.bone_combo.currentText()) + ");"))
        bone_controls.addWidget(self.focus_bone_button)
        bone_view_controls = QHBoxLayout()
        bone_view_controls.setContentsMargins(0, 0, 0, 0)
        self.undo_button = QPushButton("↶ Deshacer")
        self.undo_button.setToolTip("Deshace la última edición de pesos o de pivotes (Ctrl+Z).")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self._undo_edit)
        self.redo_button = QPushButton("↷ Rehacer")
        self.redo_button.setToolTip("Rehace la edición deshecha (Ctrl+Shift+Z o Ctrl+Y).")
        self.redo_button.setEnabled(False)
        self.redo_button.clicked.connect(self._redo_edit)
        bone_view_controls.addWidget(self.undo_button)
        bone_view_controls.addWidget(self.redo_button)
        bone_view_controls.addSpacing(14)
        bone_view_controls.addWidget(QLabel("Alinear vista"))
        self.bone_view_buttons: list[QPushButton] = []
        for axis, sign, label, tooltip in (
            ("x", 1, "+X", "Vista desde +X"), ("x", -1, "−X", "Vista desde −X"),
            ("y", 1, "+Y", "Vista desde +Y (arriba)"), ("y", -1, "−Y", "Vista desde −Y (abajo)"),
            ("z", 1, "+Z", "Vista desde +Z (frente GLB habitual)"), ("z", -1, "−Z", "Vista desde −Z (espalda)"),
        ):
            button = QPushButton(label)
            button.setFixedWidth(38)
            button.setToolTip(tooltip + ". En este visor Y es vertical.")
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, a=axis, s=sign: self._set_avatar_view(a, s))
            bone_view_controls.addWidget(button)
            self.bone_view_buttons.append(button)
        self.bone_spins: list[QDoubleSpinBox] = []
        bone_rotation_controls = QHBoxLayout()
        bone_rotation_controls.addWidget(QLabel("Rotación local"))
        for axis in ("X", "Y", "Z"):
            bone_rotation_controls.addWidget(QLabel(axis))
            spin = QDoubleSpinBox()
            spin.setRange(-90.0, 90.0)
            spin.setDecimals(1)
            spin.setSingleStep(2.0)
            spin.setSuffix("°")
            spin.setMaximumWidth(78)
            spin.setEnabled(False)
            spin.valueChanged.connect(self._apply_bone_rotation)
            bone_rotation_controls.addWidget(spin)
            self.bone_spins.append(spin)
        self.reset_bone_button = QPushButton("Restaurar")
        self.reset_bone_button.setEnabled(False)
        self.reset_bone_button.clicked.connect(self._reset_selected_bone)
        reset_controls = QHBoxLayout()
        reset_controls.addWidget(self.reset_bone_button)
        self.reset_all_bones_button = QPushButton("Restaurar rig completo")
        self.reset_all_bones_button.setToolTip("Deshace todos los giros y desplazamientos manuales de huesos.")
        self.reset_all_bones_button.setEnabled(False)
        self.reset_all_bones_button.clicked.connect(self._reset_all_bones)
        reset_controls.addWidget(self.reset_all_bones_button)
        hand_controls = QHBoxLayout()
        self.hand_diagnostics = QCheckBox("Marcar articulaciones del cuerpo")
        self.hand_diagnostics.setToolTip("Muestra los hitos del rig: rojo indica confianza baja, amarillo media; sin informe usa colores por región corporal.")
        self.hand_diagnostics.setEnabled(False)
        self.hand_diagnostics.toggled.connect(self._toggle_hand_diagnostics)
        hand_controls.addWidget(self.hand_diagnostics)
        self.hand_weights = QCheckBox("Ver pesos del hueso seleccionado")
        self.hand_weights.setToolTip("Muestra la superficie visible del hueso seleccionado: azul/rosa indica influencia baja/alta y cian señala pesos lejos de su segmento. El punto amarillo es el pivote.")
        self.hand_weights.setEnabled(False)
        self.hand_weights.toggled.connect(self._toggle_hand_weights)
        hand_controls.addWidget(self.hand_weights)
        self.drag_bone_pivot = QCheckBox("Mover pivote con ratón")
        self.drag_bone_pivot.setToolTip("Activa el modo, selecciona un hueso y arrastra su punto amarillo. Sólo cambia ese pivote: las articulaciones hijas quedan ancladas. El arrastre ocurre en el plano de la vista; gira la cámara para ajustar otra profundidad. Se puede deshacer.")
        self.drag_bone_pivot.setEnabled(False)
        self.drag_bone_pivot.toggled.connect(self._configure_bone_pivot_drag)
        self.move_bone_section = QCheckBox("Mover sección completa (incluir descendientes)")
        self.move_bone_section.setToolTip("Selecciona la mano para mover muñeca y dedos juntos. Conserva la distancia entre sus articulaciones y la apariencia de la malla. Funciona con el ratón y los controles de posición; se puede deshacer.")
        self.move_bone_section.toggled.connect(lambda enabled: self._run("window.meshingupAvatar?.setMoveBoneSection(" + json.dumps(enabled) + ");"))
        pivot_controls = QHBoxLayout()
        pivot_controls.addWidget(self.drag_bone_pivot)
        pivot_controls.addWidget(QLabel("Limitar a"))
        self.pivot_axis_combo = QComboBox()
        for label, axis in (("Vista", "view"), ("Eje X", "x"), ("Eje Y", "y"), ("Eje Z", "z")):
            self.pivot_axis_combo.addItem(label, axis)
        self.pivot_axis_combo.setToolTip("Restringe el arrastre al plano de vista o a un eje del mundo. Para mover en profundidad, gira a una vista lateral.")
        self.pivot_axis_combo.currentIndexChanged.connect(self._set_pivot_axis_constraint)
        pivot_controls.addWidget(self.pivot_axis_combo)
        self.center_pivot_x = QPushButton("Centrar X")
        self.center_pivot_x.setToolTip("Alinea X del pivote con el centro geométrico del modelo. Se puede deshacer.")
        self.center_pivot_x.setEnabled(False)
        self.center_pivot_x.clicked.connect(lambda: self._run("window.meshingupAvatar?.centerSelectedBoneOnAxis('x')", self._pivot_centered))
        pivot_controls.addWidget(self.center_pivot_x)
        self.center_pivot_y = QPushButton("Centrar Y")
        self.center_pivot_y.setToolTip("Alinea Y del pivote con el centro geométrico del modelo. Se puede deshacer.")
        self.center_pivot_y.setEnabled(False)
        self.center_pivot_y.clicked.connect(lambda: self._run("window.meshingupAvatar?.centerSelectedBoneOnAxis('y')", self._pivot_centered))
        pivot_controls.addWidget(self.center_pivot_y)
        weight_controls = QHBoxLayout()
        ai_controls = QHBoxLayout()
        fit_controls = QHBoxLayout()
        pivot_proposal_controls = QHBoxLayout()
        self.weight_paint = QCheckBox("Corregir pesos con pincel")
        self.weight_paint.setToolTip("Pinta una influencia parcial del hueso elegido. Los vértices tocados se resaltan en verde; el borde usa caída suave. Usa Deshacer o Guardar para conservar el resultado.")
        self.weight_paint.setEnabled(False)
        self.weight_paint.toggled.connect(self._configure_weight_paint)
        weight_controls.addWidget(self.weight_paint)
        self.ai_hand_select = QPushButton("IA local: marcar región")
        self.ai_hand_select.setToolTip("Selecciona un hueso, enfoca esa parte y marca sobre la malla. SAM 2 local propone una máscara revisable.")
        self.ai_hand_select.setCheckable(True)
        self.ai_hand_select.setEnabled(False)
        self.ai_hand_select.toggled.connect(self._configure_ai_hand_selection)
        ai_controls.addWidget(self.ai_hand_select)
        self.ai_hand_apply = QPushButton("Aplicar propuesta")
        self.ai_hand_apply.setToolTip("Aplica los puntos amarillos al hueso seleccionado y retira su influencia de los puntos cian visibles que pertenecen a otra región. Se puede deshacer.")
        self.ai_hand_apply.setEnabled(False)
        self.ai_hand_apply.clicked.connect(lambda: self._run("window.meshingupAvatar?.applyHandSegmentationProposal()", self._ai_hand_applied))
        ai_controls.addWidget(self.ai_hand_apply)
        self.ai_hand_discard = QPushButton("Descartar propuesta")
        self.ai_hand_discard.setEnabled(False)
        self.ai_hand_discard.clicked.connect(self._discard_ai_hand_proposal)
        ai_controls.addWidget(self.ai_hand_discard)
        brush_options_controls = QHBoxLayout()
        brush_options_controls.addWidget(QLabel("Radio del pincel"))
        self.brush_radius = QDoubleSpinBox()
        self.brush_radius.setRange(0.2, 8.0)
        self.brush_radius.setDecimals(1)
        self.brush_radius.setSingleStep(0.2)
        self.brush_radius.setValue(1.2)
        self.brush_radius.setSuffix("% h")
        self.brush_radius.setToolTip("Radio expresado como porcentaje de la altura del modelo; así se adapta al tamaño de cada avatar.")
        self.brush_radius.setMaximumWidth(100)
        self.brush_radius.setEnabled(False)
        self.brush_radius.valueChanged.connect(self._brush_options_changed)
        brush_options_controls.addWidget(self.brush_radius)
        brush_options_controls.addWidget(QLabel("Fuerza"))
        self.brush_strength = QDoubleSpinBox()
        self.brush_strength.setRange(5.0, 100.0)
        self.brush_strength.setDecimals(0)
        self.brush_strength.setSingleStep(5.0)
        self.brush_strength.setValue(25.0)
        self.brush_strength.setSuffix("%")
        self.brush_strength.setMaximumWidth(82)
        self.brush_strength.setEnabled(False)
        self.brush_strength.valueChanged.connect(self._brush_options_changed)
        brush_options_controls.addWidget(self.brush_strength)
        self.clear_brush_selection = QPushButton("Limpiar marca verde")
        self.clear_brush_selection.setEnabled(False)
        self.clear_brush_selection.clicked.connect(self._clear_brush_selection)
        brush_options_controls.addWidget(self.clear_brush_selection)
        self.assign_brush_selection = QPushButton("Asignar selección al hueso")
        self.assign_brush_selection.setToolTip(
            "Asigna al 100% del hueso seleccionado los vértices verdes y quita ese hueso de los demás vértices de la malla. "
            "Fuera de la selección conserva y normaliza otros pesos; si no queda otro, usa el hueso vecino más próximo. "
            "La operación completa se puede deshacer. Pulsa «Guardar rig/pose editados» para escribirla en el GLB."
        )
        self.assign_brush_selection.setEnabled(False)
        self.assign_brush_selection.clicked.connect(self._assign_brush_selection_to_bone)
        brush_options_controls.addWidget(self.assign_brush_selection)
        self.auto_correct_hand = QPushButton("Corregir pesos cerca del hueso")
        self.auto_correct_hand.setToolTip("En pose neutral refuerza la influencia cerca del segmento del hueso seleccionado y retira pesos que estén lejos y pertenezcan claramente a otro segmento. Revisa el mapa; puedes deshacerlo.")
        self.auto_correct_hand.setEnabled(False)
        self.auto_correct_hand.clicked.connect(self._auto_correct_hand)
        fit_controls.addWidget(self.auto_correct_hand)
        self.fit_hand_skeleton = QPushButton("Proponer pivote anatómico")
        self.fit_hand_skeleton.setToolTip("Para hips propone el punto medio entre las raíces de ambos muslos. En otros huesos sólo acepta pesos dominantes compactos; rechaza centroides de regiones amplias. Muestra un marcador cian sin mover el rig hasta que aceptes.")
        self.fit_hand_skeleton.setEnabled(False)
        self.fit_hand_skeleton.clicked.connect(self._fit_hand_skeleton)
        fit_controls.addWidget(self.fit_hand_skeleton)
        self.apply_bone_pivot = QPushButton("Aplicar pivote")
        self.apply_bone_pivot.setEnabled(False)
        self.apply_bone_pivot.clicked.connect(self._apply_bone_pivot_proposal)
        pivot_proposal_controls.addWidget(self.apply_bone_pivot)
        self.discard_bone_pivot = QPushButton("Descartar pivote")
        self.discard_bone_pivot.setEnabled(False)
        self.discard_bone_pivot.clicked.connect(self._discard_bone_pivot_proposal)
        pivot_proposal_controls.addWidget(self.discard_bone_pivot)
        self.hand_test_button = QPushButton("Probar movimiento del hueso")
        self.hand_test_button.setToolTip(
            "Detiene el clip actual, devuelve los demás huesos a reposo y prueba sólo el hueso seleccionado en tres ejes. "
            "Así puedes distinguir una deformación por pesos del movimiento normal de una animación."
        )
        self.hand_test_button.setEnabled(False)
        self.hand_test_button.clicked.connect(self._play_hand_test)
        hand_controls.addWidget(self.hand_test_button)
        save_controls = QHBoxLayout()
        self.save_edit_button = QPushButton("Guardar rig/pose editados")
        self.save_edit_button.setEnabled(False)
        self.save_edit_button.clicked.connect(self.save_edited_model)
        save_controls.addWidget(self.save_edit_button)
        position_controls = QHBoxLayout()
        position_controls.addWidget(QLabel("Mover pivote local"))
        self.position_spins: list[QDoubleSpinBox] = []
        for axis in ("X", "Y", "Z"):
            position_controls.addWidget(QLabel(axis))
            spin = QDoubleSpinBox()
            spin.setRange(-0.5, 0.5)
            spin.setDecimals(3)
            spin.setSingleStep(0.01)
            spin.setMaximumWidth(86)
            spin.setEnabled(False)
            spin.valueChanged.connect(self._apply_bone_position)
            position_controls.addWidget(spin)
            self.position_spins.append(spin)
        self.bone_quality_label = QLabel("Diagnóstico de pivote: selecciona un hueso para ver su método y confianza.")
        self.bone_quality_label.setWordWrap(True)
        region_report = self.rigging_report.get("weight_region_validation", {})
        region_ratio = region_report.get("corrected_vertex_ratio") if isinstance(region_report, dict) else None
        self.weight_region_label = QLabel(
            f"Regiones de pesos: {region_report.get('corrected_vertex_count', 0)} vértices ajustados por influencia de huesos no vecinos ({float(region_ratio) * 100:.1f}% del total)."
            if region_ratio is not None else "Regiones de pesos: sin informe guardado."
        )
        self.weight_region_label.setWordWrap(True)
        if region_ratio is not None:
            self.weight_region_label.setStyleSheet("color: #9a4b00;" if float(region_ratio) > 0.12 else "color: #315d38;")
        self.message = QLabel("Cargando visor local… · el selector de conducta sirve para probar estados; aún no recibe eventos en vivo de Meido.")
        self.message.setWordWrap(True)

        def row(*items) -> QHBoxLayout:
            result = QHBoxLayout()
            result.setContentsMargins(0, 0, 0, 0)
            result.setSpacing(4)
            for item in items:
                result.addWidget(item)
            return result

        def labelled(text: str, widget: QWidget, *extra: QWidget) -> QHBoxLayout:
            result = row(QLabel(text), widget, *extra)
            result.setStretch(1, 1)
            return result

        def axis_row(spins: list[QDoubleSpinBox]) -> QHBoxLayout:
            result = QHBoxLayout()
            result.setContentsMargins(0, 0, 0, 0)
            result.setSpacing(4)
            for axis, spin in zip("XYZ", spins):
                result.addWidget(QLabel(axis))
                spin.setMaximumWidth(16777215)
                result.addWidget(spin, 1)
            return result

        def group(title: str, *parts) -> QGroupBox:
            box = QGroupBox(title)
            layout_ = QVBoxLayout(box)
            layout_.setContentsMargins(6, 8, 6, 6)
            layout_.setSpacing(5)
            for part in parts:
                if isinstance(part, QWidget):
                    layout_.addWidget(part)
                else:
                    layout_.addLayout(part)
            return box

        # Ordered by how the panel is used: preview, then pick a bone, then fix
        # its pivot, then its weights. Saving stays visible below the scroll area.
        animation_group = group(
            "Animación",
            labelled("Conducta", self.state_combo),
            labelled("Clip GLB", self.clip_combo, self.play_clip_button),
            labelled("Intensidad", self.intensity_slider),
        )
        view_widgets = ([self.autorig_button] if self.autorig_button else []) + [self.skeleton_button, self.hand_diagnostics]
        view_group = group("Vista", *view_widgets)
        bone_group = group(
            "Hueso seleccionado",
            labelled("Hueso", self.bone_combo, self.focus_bone_button),
            QLabel("Rotación local"), axis_row(self.bone_spins),
            QLabel("Mover pivote local"), axis_row(self.position_spins),
            row(self.reset_bone_button, self.reset_all_bones_button),
            self.hand_test_button,
            self.bone_quality_label,
        )
        pivot_group = group(
            "Pivotes",
            self.drag_bone_pivot,
            self.move_bone_section,
            labelled("Limitar a", self.pivot_axis_combo),
            row(self.center_pivot_x, self.center_pivot_y),
            self.fit_hand_skeleton,
            row(self.apply_bone_pivot, self.discard_bone_pivot),
        )
        weights_group = group(
            "Pesos",
            self.hand_weights,
            self.weight_paint,
            row(QLabel("Radio"), self.brush_radius, QLabel("Fuerza"), self.brush_strength),
            self.clear_brush_selection,
            self.assign_brush_selection,
            self.auto_correct_hand,
            self.ai_hand_select,
            row(self.ai_hand_apply, self.ai_hand_discard),
            self.weight_region_label,
        )
        control_panel = QWidget(self)
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(4, 4, 4, 4)
        for box in (animation_group, view_group, bone_group, pivot_group, weights_group):
            control_layout.addWidget(box)
        control_layout.addStretch(1)
        control_scroll = QScrollArea(self)
        control_scroll.setWidgetResizable(True)
        control_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        control_scroll.setWidget(control_panel)
        self.save_edit_button.setStyleSheet("font-weight: 600;")
        footer = QWidget(self)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(6, 4, 6, 6)
        footer_layout.setSpacing(4)
        footer_layout.addWidget(self.save_edit_button)
        footer_layout.addWidget(self.message)
        sidebar = QWidget(self)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)
        sidebar_layout.addWidget(control_scroll, 1)
        sidebar_layout.addWidget(footer)
        sidebar.setMinimumWidth(320)
        sidebar.setMaximumWidth(440)

        viewport = QWidget(self)
        viewport_layout = QVBoxLayout(viewport)
        viewport_layout.setContentsMargins(6, 4, 4, 4)
        viewport_header = QHBoxLayout()
        self.viewport_model_label = QLabel(model_name)
        self.viewport_model_label.setStyleSheet("font-weight: 600; padding: 4px 8px;")
        viewport_header.addWidget(self.viewport_model_label)
        viewport_header.addStretch(1)
        viewport_header.addLayout(bone_view_controls)
        viewport_layout.addLayout(viewport_header)
        viewport_layout.addWidget(self.web, 1)

        workspace = QSplitter(Qt.Orientation.Horizontal, self)
        workspace.setChildrenCollapsible(False)
        workspace.addWidget(sidebar)
        workspace.addWidget(viewport)
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        workspace.setSizes([370, 760])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(workspace, 1)

        bundle = Path(__file__).resolve().parent / "viewer3d_static" / "index.html"
        if not bundle.is_file():
            raise ProjectError("No encuentro el visor 3D compilado. Ejecuta `npm install && npm run build` dentro de viewer3d/.")
        self.web.setUrl(QUrl.fromLocalFile(str(bundle)))

    def _run(self, script: str, callback=None) -> None:
        self.web.page().runJavaScript(script, callback or (lambda _result: None))

    def _on_page_loaded(self, ok: bool) -> None:
        if not ok:
            self.message.setText("No se pudo abrir el visor 3D local. Revisa Qt WebEngine y WebGL en el sistema.")
            self.model_loading_finished.emit()
            return
        self.page_ready = True
        model_url = self.model_path.as_uri()
        name = self.windowTitle().split("·")[-1].strip()
        script = """(function start(tries) {
          if (window.meshingupAvatar) {
            window.meshingupAvatar.loadModel(%s, %s).then(info => {
              window.meshingupAvatar.status(`Modelo ${info.format.toUpperCase()} cargado · ${info.animations.length} clips`);
              window.dispatchEvent(new Event('meshingup-model-ready'));
            }).catch(error => {
              window.meshingupAvatar.status(`Error cargando modelo: ${error.message}`);
            });
            return;
          }
          if (tries <= 0) {
            const detail = document.querySelector('#model-detail');
            if (detail) detail.textContent = 'El visor 3D no inició. Comprueba WebGL y vuelve a abrir la vista.';
            return;
          }
          setTimeout(() => start(tries - 1), 50);
        })(100);""" % (json.dumps(model_url), json.dumps(name))
        self._run(script)
        self._poll_model_load(100)

    def request_autorig(self) -> None:
        """Request geometry-based rig creation in the local Three.js viewer."""
        self.autorig_requested = True
        if self.autorig_button:
            self.autorig_button.setEnabled(False)
            self.autorig_button.setText("Calculando pesos…")
        self.message.setText("Esperando a que termine de cargar el modelo…" if not self.model_ready else "Preparando el análisis de pesos…")
        if self.page_ready and self.model_ready:
            self._run("window.meshingupAvatar?.autoRig(" + json.dumps(self.forward_axis) + ").catch(() => {});")

    def _poll_model_load(self, attempts: int) -> None:
        if not self.page_ready or self.model_ready:
            return
        self._run("window.meshingupAvatar?.getInfo() || null", lambda info: self._model_load_status(info, attempts))

    def _model_load_status(self, info: Any, attempts: int) -> None:
        if isinstance(info, dict) and info.get("loaded"):
            self._model_load_finished({**info, "ok": True})
            return
        if isinstance(info, dict) and info.get("loadState") == "error":
            self._model_load_finished({**info, "ok": False, "error": info.get("loadError")})
            return
        if attempts <= 0:
            self._model_load_finished({"ok": False, "error": "El visor no confirmó la carga del modelo en 20 segundos."})
            return
        QTimer.singleShot(200, lambda: self._poll_model_load(attempts - 1))

    def _model_load_finished(self, result: Any) -> None:
        self.model_loading_finished.emit()
        if not isinstance(result, dict) or not result.get("ok"):
            detail = result.get("error", "El visor no devolvió información del modelo.") if isinstance(result, dict) else "El visor no devolvió información del modelo."
            self.message.setText(f"No se pudo mostrar el modelo: {detail}")
            self.autorig_requested = False
            if self.autorig_button:
                self.autorig_button.setEnabled(True)
                self.autorig_button.setText("Reintentar auto-rig")
            return
        self.model_ready = True
        landmark_diagnostics = self.rigging_report.get("bone_landmark_diagnostics", {})
        if landmark_diagnostics:
            self._run("window.meshingupAvatar?.setBoneLandmarkDiagnostics(" + json.dumps(landmark_diagnostics, ensure_ascii=False) + ");")
        self.clip_combo.blockSignals(True)
        self.clip_combo.clear()
        self.clip_combo.addItems([str(name) for name in result.get("animations", []) if name])
        self.clip_combo.blockSignals(False)
        has_clips = self.clip_combo.count() > 0
        self.clip_combo.setEnabled(has_clips)
        self.play_clip_button.setEnabled(has_clips)
        self.message.setText(
            f"Modelo cargado · {result.get('meshes', 0)} mallas · "
            f"{len(result.get('bones', []))} huesos · {len(result.get('animations', []))} clips. "
            "El selector de conducta prueba estados; Meido aún no está conectado en vivo."
        )
        self._send_state()
        self._receive_bones(result, 0)
        if self.autorig_requested:
            self._run("window.meshingupAvatar?.autoRig(" + json.dumps(self.forward_axis) + ").catch(() => {});")

    def _play_selected_clip(self) -> None:
        name = self.clip_combo.currentText()
        if name:
            self._run("window.meshingupAvatar?.playClip(" + json.dumps(name) + ", {loop: true});")

    def _on_progress(self, message: str) -> None:
        self.message.setText(str(message))

    def _autorig_saved(self, payload: dict[str, Any]) -> None:
        self.autorig_requested = False
        self.model_ready = True
        if self.autorig_button:
            self.autorig_button.setEnabled(False)
            self.autorig_button.setText("Esqueleto guardado")
        self.message.setText("GLB actualizado y guardado en el proyecto. Revisa la deformación antes de usarlo.")
        self.autorig_saved.emit(payload)

    def _autorig_failed(self, message: str) -> None:
        self.autorig_requested = False
        if self.autorig_button:
            self.autorig_button.setEnabled(True)
            self.autorig_button.setText("Reintentar auto-rig")
        self.message.setText(message)
        self.autorig_failed.emit(message)

    def _send_state(self, _index: int = -1) -> None:
        try:
            plan = self.planner.dispatch({"type": "state", "state": self.state_combo.currentData(), "intensity": self.intensity_slider.value() / 100})
        except ProjectError as exc:
            self.message.setText(str(exc))
            return
        self._run("window.meshingupAvatar?.setPlan(" + json.dumps(plan, ensure_ascii=False) + ");")

    def _send_intensity(self, value: int) -> None:
        plan = self.planner.dispatch({"type": "parameter", "name": "intensity", "value": value / 100})
        self._run("window.meshingupAvatar?.setPlan(" + json.dumps(plan, ensure_ascii=False) + ");")

    def _refresh_bones(self, attempts: int = 0) -> None:
        if not self.page_ready:
            return
        self._run("window.meshingupAvatar?.getInfo()", lambda info: self._receive_bones(info, attempts))

    def _receive_bones(self, info: Any, attempts: int) -> None:
        bones = info.get("bones", []) if isinstance(info, dict) else []
        if not bones and attempts > 0:
            QTimer.singleShot(200, lambda: self._refresh_bones(attempts - 1))
            return
        current = self.bone_combo.currentText()
        self.bone_combo.blockSignals(True)
        model = QStandardItemModel(self.bone_combo)
        sections = (
            ("Torso", lambda n: n.lower() in {"hips", "spine", "chest", "upperchest"}),
            ("Cabeza y rostro", lambda n: any(part in n.lower() for part in ("neck", "head", "jaw", "eye", "brow", "mouth"))),
            ("Brazos", lambda n: any(part in n.lower() for part in ("shoulder", "upperarm", "lowerarm", "armtwist"))),
            ("Manos y dedos", lambda n: any(part in n.lower() for part in ("hand", "thumb", "index", "middle", "ring", "little", "pinky", "metacarpal"))),
            ("Piernas", lambda n: any(part in n.lower() for part in ("upperleg", "lowerleg", "legtwist", "knee"))),
            ("Pies", lambda n: any(part in n.lower() for part in ("foot", "toe", "ankle"))),
        )
        named_bones = [str(name) for name in bones if name]
        assigned: set[str] = set()
        grouped: list[tuple[str, list[str]]] = []
        for title, predicate in sections:
            members = [name for name in named_bones if name not in assigned and predicate(name)]
            if members:
                grouped.append((title, members))
                assigned.update(members)
        remaining = [name for name in named_bones if name not in assigned]
        if remaining:
            grouped.append(("Otros", remaining))
        for title, members in grouped:
            header = QStandardItem(title)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setEnabled(False)
            model.appendRow(header)
            for name in members:
                item = QStandardItem(name)
                item.setData(name, Qt.ItemDataRole.UserRole)
                model.appendRow(item)
        self.bone_combo.setModel(model)
        if current:
            index = self.bone_combo.findText(current)
            if index >= 0:
                self.bone_combo.setCurrentIndex(index)
        self.bone_combo.blockSignals(False)
        enabled = bool(self.bone_combo.count())
        self.skeleton_button.setEnabled(enabled)
        self.bone_combo.setEnabled(enabled)
        for spin in self.bone_spins:
            spin.setEnabled(enabled)
        for spin in self.position_spins:
            spin.setEnabled(enabled and info.get("format") == "glb")
        self.reset_bone_button.setEnabled(enabled)
        self.reset_all_bones_button.setEnabled(enabled)
        self.hand_diagnostics.setEnabled(enabled)
        self.hand_weights.setEnabled(enabled)
        self.drag_bone_pivot.setEnabled(enabled and info.get("format") == "glb")
        self.weight_paint.setEnabled(enabled)
        self.brush_radius.setEnabled(enabled and info.get("format") == "glb")
        self.brush_strength.setEnabled(enabled and info.get("format") == "glb")
        self.clear_brush_selection.setEnabled(enabled and info.get("format") == "glb")
        self.assign_brush_selection.setEnabled(enabled and info.get("format") == "glb")
        self.ai_hand_select.setEnabled(enabled and info.get("format") == "glb" and self.bridge is not None)
        for button in self.bone_view_buttons:
            button.setEnabled(bool(info.get("loaded")))
        self.center_pivot_x.setEnabled(enabled and info.get("format") == "glb")
        self.center_pivot_y.setEnabled(enabled and info.get("format") == "glb")
        self.pivot_axis_combo.setEnabled(enabled and info.get("format") == "glb")
        self.auto_correct_hand.setEnabled(enabled)
        self.fit_hand_skeleton.setEnabled(enabled)
        self.hand_test_button.setEnabled(enabled)
        self.focus_bone_button.setEnabled(enabled)
        self.save_edit_button.setEnabled(enabled and self.bridge is not None and info.get("format") == "glb")
        if enabled:
            self._select_bone(self.bone_combo.currentText())
        elif isinstance(info, dict) and info.get("loaded"):
            self.message.setText("El modelo cargó, pero no contiene huesos. Crea el auto-rig para inspeccionarlos y editarlos.")
        if enabled:
            self._run("window.meshingupAvatar?.selectBone(" + json.dumps(self.bone_combo.currentText()) + ");")

    def _undo_edit(self) -> None:
        self._run("window.meshingupAvatar?.undoEdit()", lambda _result: self._refresh_history_buttons())

    def _redo_edit(self) -> None:
        self._run("window.meshingupAvatar?.redoEdit()", lambda _result: self._refresh_history_buttons())

    def _set_history_buttons(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_button.setEnabled(bool(can_undo))
        self.redo_button.setEnabled(bool(can_redo))

    def _refresh_history_buttons(self) -> None:
        """Ask the viewer for the history state (used when no host bridge pushes it)."""
        def apply(state: Any) -> None:
            if isinstance(state, dict):
                self._set_history_buttons(bool(state.get("canUndo")), bool(state.get("canRedo")))
        self._run("window.meshingupAvatar?.getHistoryState()", apply)

    def _toggle_skeleton(self, visible: bool) -> None:
        self.skeleton_button.setText("Ocultar esqueleto" if visible else "Mostrar esqueleto")
        self._run("window.meshingupAvatar?.setSkeletonVisible(" + ("true" if visible else "false") + ");")

    def _toggle_hand_diagnostics(self, visible: bool) -> None:
        self._run("window.meshingupAvatar?.setHandDiagnostics(" + ("true" if visible else "false") + ");")

    def _toggle_hand_weights(self, visible: bool) -> None:
        bone = self.bone_combo.currentText()
        self._run("window.meshingupAvatar?.setBoneWeightDiagnostics(" + ("true" if visible else "false") + "," + json.dumps(bone) + ");")

    def _configure_bone_pivot_drag(self, enabled: bool) -> None:
        if enabled:
            if self.weight_paint.isChecked():
                self.weight_paint.setChecked(False)
            if self.ai_hand_select.isChecked():
                self.ai_hand_select.setChecked(False)
        self._run(
            "window.meshingupAvatar?.configureBonePivotDrag(" + json.dumps(bool(enabled)) + ")",
            self._bone_pivot_drag_configured,
        )

    def _bone_pivot_drag_configured(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        accepted = bool(result.get("enabled"))
        if accepted != self.drag_bone_pivot.isChecked():
            self.drag_bone_pivot.blockSignals(True)
            self.drag_bone_pivot.setChecked(accepted)
            self.drag_bone_pivot.blockSignals(False)
        self.message.setText(str(result.get("message", "Modo de arrastre actualizado.")))

    def _set_pivot_axis_constraint(self, _index: int = -1) -> None:
        axis = self.pivot_axis_combo.currentData()
        self._run("window.meshingupAvatar?.configureBonePivotAxis(" + json.dumps(axis or "view") + ");")

    def _set_avatar_view(self, axis: str, sign: int) -> None:
        self._run(
            "window.meshingupAvatar?.setViewAxis(" + json.dumps(axis) + "," + str(int(sign)) + ")",
            self._avatar_view_changed,
        )

    def _avatar_view_changed(self, result: Any) -> None:
        if isinstance(result, dict):
            self.message.setText(str(result.get("message", "Vista alineada.")))

    def _pivot_centered(self, result: Any) -> None:
        if isinstance(result, dict):
            self.message.setText(str(result.get("message", "Alineación de pivote actualizada.")))

    def _configure_weight_paint(self, enabled: bool) -> None:
        if enabled:
            if self.drag_bone_pivot.isChecked():
                self.drag_bone_pivot.setChecked(False)
            if self.ai_hand_select.isChecked():
                self.ai_hand_select.setChecked(False)
        bone = self.bone_combo.currentText()
        options = {"radiusRatio": self.brush_radius.value() / 100, "strength": self.brush_strength.value() / 100}
        self._run("window.meshingupAvatar?.configureHandWeightPaint(" + json.dumps(bool(enabled)) + "," + json.dumps(bone) + "," + json.dumps(options) + ");")
        can_edit_weights = self.model_ready and self.model_path.suffix.casefold() == ".glb"
        self.assign_brush_selection.setEnabled(can_edit_weights)

    def _brush_options_changed(self, _value: float = 0.0) -> None:
        if self.weight_paint.isChecked():
            self._configure_weight_paint(True)

    def _clear_brush_selection(self) -> None:
        self._run("window.meshingupAvatar?.clearHandPaintSelection()")
        self.message.setText("Se limpió la marca verde del pincel; los pesos aplicados no cambiaron.")

    def _assign_brush_selection_to_bone(self) -> None:
        self._run(
            "window.meshingupAvatar?.assignHandPaintSelectionToBone()",
            self._hand_paint_selection_assigned,
        )

    def _hand_paint_selection_assigned(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        self.message.setText(str(result.get("message", "No se pudo asignar la selección.")))
        if result.get("changed"):
            self._refresh_history_buttons()

    def _configure_ai_hand_selection(self, enabled: bool) -> None:
        if enabled and not self._sam2_confirmed:
            answer = QMessageBox.question(
                self, "Análisis local de región con SAM 2",
                "SAM 2 procesará localmente una captura de la vista 3D y el punto que marques sobre la región del hueso seleccionado. "
                "Si el modelo aún no está en caché, MeshingUp descargará sus pesos oficiales una vez. "
                "La captura no se enviará a un servicio remoto. ¿Continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.ai_hand_select.blockSignals(True)
                self.ai_hand_select.setChecked(False)
                self.ai_hand_select.blockSignals(False)
                return
            self._sam2_confirmed = True
        if enabled and self.weight_paint.isChecked():
            self.weight_paint.setChecked(False)
        bone = self.bone_combo.currentText()
        self._run("window.meshingupAvatar?.configureHandSegmentation(" + json.dumps(bool(enabled)) + "," + json.dumps(bone) + ");")
        if enabled:
            self.ai_hand_apply.setEnabled(False)
            self.message.setText(f"Enfoca {bone}, marca la región visible que quieras mapear y revisa la propuesta local de SAM 2.")

    def _discard_ai_hand_proposal(self) -> None:
        self._run("window.meshingupAvatar?.discardHandSegmentationProposal()")
        self.ai_hand_apply.setEnabled(False)
        self.ai_hand_discard.setEnabled(False)
        self.message.setText("Propuesta de segmentación descartada.")

    def _ai_hand_applied(self, result: Any) -> None:
        if isinstance(result, dict):
            self.message.setText(str(result.get("message", "Propuesta aplicada.")))
            self.ai_hand_apply.setEnabled(False)
            self.ai_hand_discard.setEnabled(False)

    def _on_hand_mask_ready(self, job_id: str, result_json: str) -> None:
        try:
            result = json.loads(result_json)
        except (TypeError, json.JSONDecodeError):
            result = {"error": "Respuesta de SAM 2 inválida."}
        # runJavaScript does not await a Promise returned by an async function.
        self._run("window.meshingupHandPreviewResult = null; window.meshingupAvatar.receiveHandSegmentation(" + json.dumps(job_id) + "," + json.dumps(result, ensure_ascii=False) + ").then(r => window.meshingupHandPreviewResult = r).catch(e => window.meshingupHandPreviewResult = {ready:false,message:String(e)});")
        self._poll_hand_preview(2400)

    def _poll_hand_preview(self, attempts: int) -> None:
        def received(result):
            if isinstance(result, dict):
                self._hand_segmentation_previewed(result)
            elif attempts > 0:
                QTimer.singleShot(100, lambda: self._poll_hand_preview(attempts - 1))
            else:
                self.message.setText("El visor no confirmó la propuesta; vuelve a marcar la región.")
        self._run("window.meshingupHandPreviewResult", received)

    def _hand_segmentation_previewed(self, result: Any) -> None:
        if isinstance(result, dict):
            self.ai_hand_apply.setEnabled(bool(result.get("ready")))
            self.ai_hand_discard.setEnabled(bool(result.get("ready")))
            self.message.setText(str(result.get("message", "SAM 2 terminó el análisis.")))

    def _auto_correct_hand(self) -> None:
        bone = self.bone_combo.currentText()
        if not self.hand_weights.isChecked():
            self.hand_weights.setChecked(True)
        self._run("window.meshingupAvatar?.autoCorrectSelectedBoneWeights(" + json.dumps(bone) + ")", self._auto_correct_hand_finished)

    def _auto_correct_hand_finished(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        self.message.setText(str(result.get("message", "La corrección automática no devolvió diagnóstico.")))

    def _fit_hand_skeleton(self) -> None:
        bone = self.bone_combo.currentText()
        self._run("window.meshingupAvatar?.centerSelectedBonePivotOnWeights(" + json.dumps(bone) + ")", self._fit_hand_skeleton_finished)

    def _fit_hand_skeleton_finished(self, result: Any) -> None:
        if isinstance(result, dict):
            self.message.setText(str(result.get("message", "El ajuste no devolvió diagnóstico.")))
            proposed = bool(result.get("proposed"))
            self.apply_bone_pivot.setEnabled(proposed)
            self.discard_bone_pivot.setEnabled(proposed)

    def _apply_bone_pivot_proposal(self) -> None:
        self._run("window.meshingupAvatar?.applySelectedBonePivotProposal()", self._bone_pivot_proposal_applied)

    def _bone_pivot_proposal_applied(self, result: Any) -> None:
        self.apply_bone_pivot.setEnabled(False)
        self.discard_bone_pivot.setEnabled(False)
        if isinstance(result, dict):
            self.message.setText(str(result.get("message", "Propuesta de pivote aplicada.")))

    def _discard_bone_pivot_proposal(self) -> None:
        self._run("window.meshingupAvatar?.discardSelectedBonePivotProposal()")
        self.apply_bone_pivot.setEnabled(False)
        self.discard_bone_pivot.setEnabled(False)
        self.message.setText("Propuesta de pivote descartada; el rig no cambió.")

    def _play_hand_test(self) -> None:
        bone = self.bone_combo.currentText()
        self._run("window.meshingupAvatar?.playSelectedBoneTest(" + json.dumps(bone) + ")", lambda result: self.message.setText(str(result.get("message", "Prueba de movimiento iniciada.")) if isinstance(result, dict) else "Prueba de movimiento iniciada."))

    def _select_bone(self, name: str) -> None:
        if not name:
            return
        if getattr(self, "apply_bone_pivot", None) and self.apply_bone_pivot.isEnabled():
            self._discard_bone_pivot_proposal()
        diagnostics = self.rigging_report.get("bone_landmark_diagnostics", {})
        diagnostic = diagnostics.get(name) if isinstance(diagnostics, dict) else None
        if isinstance(diagnostic, dict):
            confidence = str(diagnostic.get("confidence", "low"))
            labels = {"high": "alta", "medium": "media", "low": "baja"}
            position = diagnostic.get("position", [])
            position_text = ", ".join(f"{float(value):.3f}" for value in position[:3]) if len(position) >= 3 else "sin coordenada"
            hips_note = " Hips es el control central de pelvis; los puntos muestran influencias de peso y no dos pivotes laterales." if name.lower() == "hips" else ""
            self.bone_quality_label.setText(
                f"{name} · confianza {labels.get(confidence, confidence)} · {diagnostic.get('method', 'método no registrado')} · "
                f"{diagnostic.get('sample_count', 0)} muestras · hito estimado [{position_text}]. "
                f"{diagnostic.get('note', 'Puedes moverlo con los controles locales; revisa en la malla.')}{hips_note}"
            )
            self.bone_quality_label.setStyleSheet("color: #9a4b00;" if confidence == "low" else "color: #315d38;")
        else:
            extra = " Hips es el control central de pelvis; el mapa de puntos muestra sus pesos, no dos pivotes laterales." if name.lower() == "hips" else ""
            self.bone_quality_label.setText(f"{name} · sin diagnóstico geométrico individual; confirma su pivote visualmente.{extra}")
            self.bone_quality_label.setStyleSheet("color: #9a4b00;")
        self._run("window.meshingupAvatar?.selectBone(" + json.dumps(name) + ");")
        if self.weight_paint.isChecked():
            self._configure_weight_paint(True)
        if self.ai_hand_select.isChecked():
            self._configure_ai_hand_selection(True)
        self._run("window.meshingupAvatar?.getBoneRotation(" + json.dumps(name) + ")", self._receive_bone_rotation)
        self._run("window.meshingupAvatar?.getBonePosition(" + json.dumps(name) + ")", self._receive_bone_position)

    def _receive_bone_rotation(self, values: Any) -> None:
        if not isinstance(values, list) or len(values) != 3:
            return
        for spin, value in zip(self.bone_spins, values):
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)

    def _apply_bone_rotation(self, _value: float = 0.0) -> None:
        name = self.bone_combo.currentText()
        if not name:
            return
        rotation = [spin.value() for spin in self.bone_spins]
        self._run("window.meshingupAvatar?.setBoneRotation(" + json.dumps(name) + "," + json.dumps(rotation) + ");")

    def _receive_bone_position(self, values: Any) -> None:
        if not isinstance(values, list) or len(values) != 3:
            return
        for spin, value in zip(self.position_spins, values):
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)

    def _apply_bone_position(self, _value: float = 0.0) -> None:
        name = self.bone_combo.currentText()
        if name:
            position = [spin.value() for spin in self.position_spins]
            self._run("window.meshingupAvatar?.setBonePosition(" + json.dumps(name) + "," + json.dumps(position) + ");")

    def _reset_selected_bone(self) -> None:
        name = self.bone_combo.currentText()
        if name:
            self._run("window.meshingupAvatar?.resetBone(" + json.dumps(name) + ");")
            for spin in self.bone_spins:
                spin.blockSignals(True)
                spin.setValue(0)
                spin.blockSignals(False)
            for spin in self.position_spins:
                spin.blockSignals(True)
                spin.setValue(0)
                spin.blockSignals(False)

    def _reset_all_bones(self) -> None:
        if not self.bone_combo.count():
            return
        self._run("window.meshingupAvatar?.resetAllBones();")
        for spin in (*self.bone_spins, *self.position_spins):
            spin.blockSignals(True)
            spin.setValue(0)
            spin.blockSignals(False)
        self.message.setText("Ajustes manuales deshechos. Guarda el GLB si quieres conservar el rig en su pose base.")

    def save_edited_model(self) -> None:
        if self.page_ready:
            self._run("window.meshingupAvatar?.saveEditedModel().catch(() => {});")

    def dispatch_agent_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Update the avatar from a public Meido status/TTS event."""
        plan = self.planner.dispatch(event)
        self._run("window.meshingupAvatar?.setPlan(" + json.dumps(plan, ensure_ascii=False) + ");")
        return plan


class _HandMaskSignals(QObject):
    finished = pyqtSignal(str, str)


class _HandMaskTask(QRunnable):
    def __init__(self, job_id: str, image_data_url: str, x: int, y: int):
        super().__init__()
        self.job_id = job_id
        self.image_data_url = image_data_url
        self.x = x
        self.y = y
        self.signals = _HandMaskSignals()

    def run(self) -> None:
        try:
            from PIL import Image
            from .segmentation import propose_masks
            header, encoded = self.image_data_url.split(",", 1)
            if "image/png" not in header or len(encoded) > 16 * 1024 * 1024:
                raise ProjectError("La captura del visor no es PNG o supera 12 MB.")
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                width, height = image.size
            if width < 32 or height < 32 or not (0 <= self.x < width and 0 <= self.y < height):
                raise ProjectError("El punto guía está fuera de la captura o la imagen es demasiado pequeña.")
            with tempfile.NamedTemporaryFile(prefix="meshingup-hand-sam-", suffix=".png", delete=False) as handle:
                handle.write(raw)
                image_path = Path(handle.name)
            try:
                proposals = propose_masks(image_path, [(float(self.x), float(self.y))])
            finally:
                image_path.unlink(missing_ok=True)
            if not proposals:
                raise ProjectError("SAM 2 no generó una máscara para ese punto.")
            mask, score = proposals[0]
            buffer = io.BytesIO()
            mask.save(buffer, format="PNG")
            result = {"mask": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
                      "score": score, "width": width, "height": height,
                      "model": "facebook/sam2.1-hiera-tiny"}
            self.signals.finished.emit(self.job_id, json.dumps(result))
        except Exception as exc:
            self.signals.finished.emit(self.job_id, json.dumps({"error": str(exc)}, ensure_ascii=False))


class Avatar3DBridge(QObject):
    """Receive a chunked GLB export from the bundled browser runtime."""

    saved = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)
    hand_mask_ready = pyqtSignal(str, str)
    history_changed = pyqtSignal(bool, bool)

    def __init__(self, project_path: Path, expected_mtime_ns: int | None, parent=None):
        super().__init__(parent)
        self.project_path = project_path
        self.expected_mtime_ns = expected_mtime_ns
        self.transfers: dict[str, dict[str, Any]] = {}
        self.hand_tasks: list[_HandMaskTask] = []

    @pyqtSlot(str, str, int, int, result=bool)
    def requestHandMask(self, job_id: str, image_data_url: str, x: int, y: int) -> bool:
        if not job_id or len(job_id) > 80:
            return False
        task = _HandMaskTask(job_id, image_data_url, x, y)
        task.signals.finished.connect(self._hand_mask_finished)
        self.hand_tasks.append(task)
        QThreadPool.globalInstance().start(task)
        self.progress.emit("SAM 2 local está segmentando la región marcada…")
        return True

    @pyqtSlot(str, str)
    def _hand_mask_finished(self, job_id: str, result_json: str) -> None:
        self.hand_tasks = [task for task in self.hand_tasks if task.job_id != job_id]
        self.hand_mask_ready.emit(job_id, result_json)

    @pyqtSlot(bool, bool, result=bool)
    def historyChanged(self, can_undo: bool, can_redo: bool) -> bool:
        self.history_changed.emit(bool(can_undo), bool(can_redo))
        return True

    @pyqtSlot(str, result=bool)
    def reportProgress(self, message: str) -> bool:
        self.progress.emit(message)
        return True

    @pyqtSlot(str, result=bool)
    def reportFailure(self, message: str) -> bool:
        self.failed.emit(message)
        return True

    @pyqtSlot(str, int, str, result=str)
    def beginAutoRig(self, model_name: str, total_bytes: int, report_json: str) -> str:
        try:
            if total_bytes < 20 or total_bytes > 512 * 1024 * 1024:
                raise ProjectError("El GLB generado tiene un tamaño inválido o supera 512 MB.")
            if self.project_path is None or not self.project_path.is_file():
                raise ProjectError("El proyecto MeshingUp ya no está disponible.")
            if self.expected_mtime_ns is not None and self.project_path.stat().st_mtime_ns != self.expected_mtime_ns:
                raise ProjectError("El proyecto cambió externamente; recárgalo antes de guardar el esqueleto.")
            data = load_project(self.project_path)
            avatar = data.get("avatar3d", {})
            if not isinstance(avatar, dict) or avatar.get("format") != "glb":
                raise ProjectError("El proyecto ya no contiene el GLB que se está modificando.")
            report = json.loads(report_json)
            if not isinstance(report, dict):
                raise ProjectError("El informe del auto-rig no tiene formato válido.")
            transfer_id = uuid.uuid4().hex
            handle = tempfile.NamedTemporaryFile(prefix="meshingup-autorig-", suffix=".glb", delete=False)
            self.transfers[transfer_id] = {
                "handle": handle, "path": Path(handle.name), "size": total_bytes,
                "written": 0, "name": model_name, "report": report,
                "source_path": avatar.get("source_path", avatar["path"]),
                "source_sha256": avatar.get("source_sha256", avatar["sha256"]),
            }
            return transfer_id
        except (OSError, ValueError, KeyError, ProjectError) as exc:
            self.failed.emit(str(exc))
            return ""

    @pyqtSlot(str, str, result=bool)
    def writeAutoRigChunk(self, transfer_id: str, encoded_chunk: str) -> bool:
        try:
            transfer = self.transfers[transfer_id]
            chunk = base64.b64decode(encoded_chunk, validate=True)
            transfer["written"] += len(chunk)
            if transfer["written"] > transfer["size"]:
                raise ProjectError("El visor envió más bytes de los declarados para el GLB.")
            transfer["handle"].write(chunk)
            return True
        except (KeyError, ValueError, OSError, ProjectError) as exc:
            self._discard_transfer(transfer_id)
            self.failed.emit(str(exc))
            return False

    @pyqtSlot(str, result=bool)
    def finishAutoRig(self, transfer_id: str) -> bool:
        transfer = self.transfers.get(transfer_id)
        if not transfer:
            self.failed.emit("No encuentro la transferencia del GLB generado.")
            return False
        try:
            transfer["handle"].flush()
            transfer["handle"].close()
            if transfer["written"] != transfer["size"]:
                raise ProjectError("La transferencia del GLB quedó incompleta.")
            if self.expected_mtime_ns is not None and self.project_path.stat().st_mtime_ns != self.expected_mtime_ns:
                raise ProjectError("El proyecto cambió externamente durante el auto-rig; no guardé el resultado.")
            report = transfer["report"]
            result = attach_avatar(self.project_path, transfer["path"], actor="user", extra_metadata={
                "name": transfer["name"],
                "source_path": transfer["source_path"], "source_sha256": transfer["source_sha256"],
                "rigging": {"method": report.get("method", "meshingup_threejs_geometry_autorig"), **report},
            })
            self.saved.emit(result)
            return True
        except (OSError, ValueError, KeyError, ProjectError) as exc:
            self.failed.emit(str(exc))
            return False
        finally:
            self._discard_transfer(transfer_id)

    @pyqtSlot(str)
    def cancelAutoRig(self, transfer_id: str) -> None:
        self._discard_transfer(transfer_id)

    def _discard_transfer(self, transfer_id: str) -> None:
        transfer = self.transfers.pop(transfer_id, None)
        if not transfer:
            return
        try:
            if not transfer["handle"].closed:
                transfer["handle"].close()
        finally:
            Path(transfer["path"]).unlink(missing_ok=True)

