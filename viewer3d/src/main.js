import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { VRMLoaderPlugin, VRMUtils, VRMHumanBoneName } from '@pixiv/three-vrm';
import './style.css';
import { preserveBindTransforms } from './hand_rig.js';
import { createEditHistory } from './edit_history.js';

const canvas = document.querySelector('#avatar');
const nameLabel = document.querySelector('#model-name');
const detailLabel = document.querySelector('#model-detail');
const loaderOverlay = document.querySelector('#loader');
const scene = new THREE.Scene();
scene.background = new THREE.Color('#17191d');
scene.fog = new THREE.Fog('#17191d', 7, 18);
const camera = new THREE.PerspectiveCamera(32, 1, 0.01, 100);
camera.position.set(0, 1.45, 4.6);
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
scene.add(new THREE.HemisphereLight(0xe5eaff, 0x302d39, 2.0));
const key = new THREE.DirectionalLight(0xffead8, 2.8);
key.position.set(-3, 5, 4);
scene.add(key);
const fill = new THREE.DirectionalLight(0x9cafff, 1.1);
fill.position.set(4, 2, -2);
scene.add(fill);
const floor = new THREE.GridHelper(8, 32, 0x515565, 0x343844);
floor.position.y = -0.015;
scene.add(floor);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1, 0);
controls.enableDamping = true;
controls.minDistance = 1.3;
controls.maxDistance = 8;
controls.maxPolarAngle = Math.PI * 0.88;

const handPaintRaycaster = new THREE.Raycaster();
const handPaintPointer = new THREE.Vector2();
let handSegmentation = { enabled: false, targetBone: '' };
let handSegmentationProposal = null;
let handSegmentationPoints = null;
let handSegmentationPending = false;
let handSegmentationCapture = null;
let handMappingReports = [];
const handSegmentationRaycaster = new THREE.Raycaster();
const handSegmentationNdc = new THREE.Vector2();
const pivotDragRaycaster = new THREE.Raycaster();
const pivotDragNdc = new THREE.Vector2();
let bonePivotDragEnabled = false;
let bonePivotAxisConstraint = 'view';
let activeBonePivotDrag = null;
const bonePivotEdits = [];
function paintHandWeights(event) {
  if (!handWeightPaint.enabled || !avatarRoot) return;
  const rect = renderer.domElement.getBoundingClientRect();
  handPaintPointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  handPaintRaycaster.setFromCamera(handPaintPointer, camera);
  const hit = handPaintRaycaster.intersectObject(avatarRoot, true).find((item) => item.object.isSkinnedMesh && item.faceIndex !== undefined);
  if (!hit) return;
  const mesh = hit.object, geo = mesh.geometry, indices = geo.attributes.skinIndex, weights = geo.attributes.skinWeight;
  if (!indices || !weights) return;
  const boneIndex = mesh.skeleton.bones.findIndex((bone) => bone.name === handWeightPaint.boneName);
  if (boneIndex < 0) { showStatus(avatarDisplayName, 'La malla no contiene el hueso seleccionado.'); return; }
  const modelHeight = new THREE.Box3().setFromObject(avatarRoot).getSize(new THREE.Vector3()).y;
  const radius = Math.max(0.001, modelHeight * handWeightPaint.radiusRatio);
  const radiusSq = radius * radius, worldHit = hit.point.clone();
  const undo = handPaintStroke.get(mesh) || new Map(), vertex = new THREE.Vector3();
  handPaintStroke.set(mesh, undo);
  let selected = handPaintSelection.get(mesh);
  if (!selected) { selected = new Set(); handPaintSelection.set(mesh, selected); }
  let selectedCount = 0;
  for (let i = 0; i < geo.attributes.position.count; i++) {
    mesh.getVertexPosition(i, vertex); mesh.localToWorld(vertex);
    const distanceSq = vertex.distanceToSquared(worldHit);
    if (distanceSq > radiusSq) continue;
    const oldIndices = [0,1,2,3].map((slot) => indices.getComponent(i, slot));
    const oldWeights = [0,1,2,3].map((slot) => weights.getComponent(i, slot));
    if (!oldWeights.some((weight) => weight > 0)) continue;
    selected.add(i); selectedCount++;
    const influence = new Map();
    oldIndices.forEach((index, slot) => influence.set(index, (influence.get(index) || 0) + oldWeights[slot]));
    const previous = influence.get(boneIndex) || 0;
    const distance = Math.sqrt(distanceSq) / radius;
    const falloff = 1 - THREE.MathUtils.smoothstep(distance, 0.45, 1);
    const target = previous + (1 - previous) * handWeightPaint.strength * falloff;
    if (target <= previous + 1e-5) continue;
    if (!undo.has(i)) undo.set(i, { indices: oldIndices, weights: oldWeights });
    for (const [index, weight] of influence) influence.set(index, index === boneIndex ? target : weight * (1 - target) / Math.max(1e-8, 1 - previous));
    influence.set(boneIndex, target);
    const chosen = [...influence.entries()].filter(([, weight]) => weight > 1e-5).sort((a,b) => b[1]-a[1]).slice(0,4);
    const total = chosen.reduce((sum, item) => sum + item[1], 0) || 1;
    for (let slot = 0; slot < 4; slot++) { indices.setComponent(i, slot, chosen[slot]?.[0] ?? boneIndex); weights.setComponent(i, slot, chosen[slot] ? chosen[slot][1] / total : 0); }
  }
  if (undo.size) {
    indices.needsUpdate = true; weights.needsUpdate = true;
    buildHandWeightDiagnostic();
  }
  if (selectedCount) rebuildHandPaintSelectionOverlay();
  if (!undo.size) {
    if (selectedCount) showStatus(avatarDisplayName, `${selectedCount} vértices dentro del pincel · marca verde · sin cambio de peso apreciable.`);
    return;
  }
  showStatus(avatarDisplayName, `Pincel ${handWeightPaint.boneName} · ${selectedCount} vértices tocados, resaltados en verde · radio ${(handWeightPaint.radiusRatio * 100).toFixed(1)}% de la altura · deshaz si hace falta.`);
  event.preventDefault();
}
function requestHandSegmentation(event) {
  if (handSegmentationPending) { showStatus(avatarDisplayName, 'SAM 2 aún está procesando el punto anterior.'); return; }
  if (!hostBridge || typeof hostBridge.requestHandMask !== 'function') { showStatus(avatarDisplayName, 'El canal local de IA no está disponible.'); return; }
  const rect = renderer.domElement.getBoundingClientRect();
  const x = Math.round((event.clientX - rect.left) / rect.width * renderer.domElement.width);
  const y = Math.round((event.clientY - rect.top) / rect.height * renderer.domElement.height);
  const targetBone = findBone(handSegmentation.targetBone || selectedBoneName);
  if (!targetBone) { showStatus(avatarDisplayName, 'Selecciona un hueso antes de marcar una región.'); return; }
  handSegmentationNdc.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  handSegmentationRaycaster.setFromCamera(handSegmentationNdc, camera);
  const clickedSurface = handSegmentationRaycaster.intersectObject(avatarRoot, true).find((hit) => hit.object.isSkinnedMesh && hit.faceIndex !== undefined);
  if (!clickedSurface) { showStatus(avatarDisplayName, 'Marca un punto visible sobre la malla skinned que quieras asignar.'); return; }
  const jobId = `hand-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  discardHandSegmentationProposal();
  activeAction?.stop(); activeAction = null; manualPoseMode = true; clearProceduralOffsets();
  avatarRoot.updateMatrixWorld(true);
  camera.updateMatrixWorld(true);
  handSegmentationCapture = { jobId, camera: camera.clone(), targetBone: targetBone.name, targetPoint: clickedSurface.point.clone(), root: avatarRoot, bones: [...boneRestQuaternions.keys()].map(bone => [bone, bone.matrixWorld.clone()]) };
  try {
    const overlays = [skeletonHelper, handDiagnosticGroup, handWeightDiagnostic, selectedBoneMarker, handSegmentationPoints]
      .filter(Boolean).map((node) => [node, node.visible]);
    let image;
    try {
      overlays.forEach(([node]) => { node.visible = false; });
      renderer.render(scene, camera);
      image = renderer.domElement.toDataURL('image/png');
    } finally {
      overlays.forEach(([node, visible]) => { node.visible = visible; });
      renderer.render(scene, camera);
    }
    handSegmentationPending = true;
    hostBridge.requestHandMask(jobId, image, x, y, (accepted) => {
      if (!accepted) handSegmentationPending = false;
      showStatus(avatarDisplayName, accepted
        ? `SAM 2 analiza la región del hueso ${handSegmentationCapture.targetBone} localmente…`
        : 'MeshingUp no pudo iniciar SAM 2. Instala la opción segmentation y vuelve a intentarlo.');
    });
  } catch (error) { handSegmentationPending = false; showStatus(avatarDisplayName, `No pude capturar la vista: ${error.message}`); }
  event.preventDefault();
}

function configureHandSegmentation(enabled, targetBone) {
  handSegmentationCapture = null;
  handSegmentation = { enabled: Boolean(enabled), targetBone: targetBone || selectedBoneName };
  if (handSegmentation.enabled) {
    handWeightPaint.enabled = false;
    discardHandSegmentationProposal();
    showStatus(avatarDisplayName, `SAM 2 local · enfoca ${handSegmentation.targetBone} y marca la parte que quieras mapear`);
  } else showStatus(avatarDisplayName, 'Selección local de región desactivada');
  return { ...handSegmentation };
}

function discardHandSegmentationProposal() {
  if (handSegmentationPoints) { scene.remove(handSegmentationPoints); handSegmentationPoints.geometry.dispose(); handSegmentationPoints.material.dispose(); handSegmentationPoints = null; }
  handSegmentationProposal = null;
  if (handWeightDiagnostic) handWeightDiagnostic.visible = handWeightsVisible;
  return true;
}

async function receiveHandSegmentation(jobId, result) {
  handSegmentationPending = false;
  const capture = handSegmentationCapture;
  if (!capture || capture.jobId !== jobId || capture.root !== avatarRoot) return { ready: false, message: 'El análisis quedó obsoleto; marca otro punto.' };
  if (result?.error) { showStatus(avatarDisplayName, `SAM 2: ${result.error}`); return { ready: false, message: result.error }; }
  if (!handSegmentation.enabled) return { ready: false, message: 'La selección de región se canceló.' };
  avatarRoot.updateMatrixWorld(true);
  if (capture.bones.some(([bone, matrix]) => !bone.matrixWorld.equals(matrix))) return { ready: false, message: 'La pose cambió durante el análisis. Marca otro punto.' };
  const image = new Image();
  await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; image.src = result.mask; });
  const maskCanvas = document.createElement('canvas'); maskCanvas.width = image.width; maskCanvas.height = image.height;
  const context = maskCanvas.getContext('2d', { willReadFrequently: true }); context.drawImage(image, 0, 0);
  const pixels = context.getImageData(0, 0, image.width, image.height).data;
  avatarRoot.updateMatrixWorld(true);
  const selected = [], excluded = [], markerPositions = [], markerColors = [], world = new THREE.Vector3(), visibility = new Map();
  const bounds = new THREE.Box3().setFromObject(avatarRoot);
  const modelHeight = bounds.getSize(new THREE.Vector3()).y;
  const visibleToleranceSq = (modelHeight * 0.025) ** 2;
  const targetBone = findBone(capture.targetBone);
  const targetBoneSegment = targetBone ? (() => {
    const start = targetBone.getWorldPosition(new THREE.Vector3());
    const child = targetBone.children.find((node) => node.isBone);
    const end = child ? child.getWorldPosition(new THREE.Vector3()) : start.clone();
    return { start, direction: end.sub(start) };
  })() : null;
  const targetRegionRadius = /Head/i.test(capture.targetBone) ? modelHeight * 0.21
    : /Chest|Spine|Hips/i.test(capture.targetBone) ? modelHeight * 0.16
      : /Hand|Foot|Toe|Thumb|Index|Middle|Ring|Little/i.test(capture.targetBone) ? modelHeight * 0.055
        : modelHeight * 0.075;
  const targetBoneIndexBySkeleton = new Map();
  const boneSegmentsBySkeleton = new Map();
  const distanceToSegmentSq = (point, segment) => {
    const delta = point.clone().sub(segment.start), lengthSq = segment.direction.lengthSq();
    const along = lengthSq > 1e-10 ? THREE.MathUtils.clamp(delta.dot(segment.direction) / lengthSq, 0, 1) : 0;
    return point.distanceToSquared(segment.start.clone().addScaledVector(segment.direction, along));
  };
  let count = 0, outsideCount = 0;
  avatarRoot.traverse((mesh) => {
    if (!mesh.isSkinnedMesh || !mesh.geometry.attributes.skinIndex || !mesh.geometry.attributes.skinWeight) return;
    const position = mesh.geometry.attributes.position;
    for (let i = 0; i < position.count; i++) {
      mesh.getVertexPosition(i, world);
      mesh.localToWorld(world);
      const projected = world.clone().project(capture.camera);
      if (projected.z < -1 || projected.z > 1 || Math.abs(projected.x) > 1 || Math.abs(projected.y) > 1) continue;
      const px = Math.floor((projected.x * 0.5 + 0.5) * image.width), py = Math.floor((0.5 - projected.y * 0.5) * image.height);
      if (px < 0 || py < 0 || px >= image.width || py >= image.height) continue;
      const offset = (py * image.width + px) * 4;
      if (pixels[offset + 3] < 128) continue;
      const sampleX = Math.min(image.width - 1, (px >> 2) * 4 + 2), sampleY = Math.min(image.height - 1, (py >> 2) * 4 + 2);
      const pixelKey = `${sampleX}:${sampleY}`;
      if (!visibility.has(pixelKey)) {
        handSegmentationNdc.set(((sampleX + 0.5) / image.width) * 2 - 1, -((sampleY + 0.5) / image.height) * 2 + 1);
        handSegmentationRaycaster.setFromCamera(handSegmentationNdc, capture.camera);
        const front = handSegmentationRaycaster.intersectObject(avatarRoot, true).find((hit) => hit.object.isSkinnedMesh && hit.faceIndex !== undefined);
        visibility.set(pixelKey, front || null);
      }
      const front = visibility.get(pixelKey);
      if (!front || front.object !== mesh || front.point.distanceToSquared(world) > visibleToleranceSq) continue;
      const targetDistanceSq = targetBoneSegment ? distanceToSegmentSq(world, targetBoneSegment) : Infinity;
      let belongsElsewhere = false;
      if (targetBoneSegment) {
        let segments = boneSegmentsBySkeleton.get(mesh.skeleton);
        if (!segments) {
          segments = mesh.skeleton.bones.filter((bone) => bone !== targetBone && bone !== targetBone.parent && bone.parent !== targetBone)
            .map((bone) => {
              const start = bone.getWorldPosition(new THREE.Vector3());
              const child = bone.children.find((node) => node.isBone);
              const end = child ? child.getWorldPosition(new THREE.Vector3()) : start.clone();
              return { start, direction: end.sub(start) };
            });
          boneSegmentsBySkeleton.set(mesh.skeleton, segments);
        }
        // SAM can include nearby limbs in a broad head mask. Keep a vertex only
        // when the selected bone is also its nearest non-adjacent body segment.
        const marginSq = (modelHeight * 0.012) ** 2;
        belongsElsewhere = segments.some((segment) => distanceToSegmentSq(world, segment) + marginSq < targetDistanceSq);
      }
      if (pixels[offset] >= 32 && targetDistanceSq <= targetRegionRadius * targetRegionRadius && !belongsElsewhere) {
        selected.push([mesh, i]); markerPositions.push(world.x, world.y, world.z); markerColors.push(1.0, 0.78, 0.18); count++;
        continue;
      }
      if (!targetBoneSegment) continue;
      let targetBoneIndex = targetBoneIndexBySkeleton.get(mesh.skeleton);
      if (targetBoneIndex === undefined) {
        targetBoneIndex = mesh.skeleton.bones.indexOf(targetBone);
        targetBoneIndexBySkeleton.set(mesh.skeleton, targetBoneIndex);
      }
      if (targetBoneIndex < 0) continue;
      let targetInfluence = 0;
      const indices = mesh.geometry.attributes.skinIndex, weights = mesh.geometry.attributes.skinWeight;
      for (let slot = 0; slot < 4; slot++) if (indices.getComponent(i, slot) === targetBoneIndex) targetInfluence += weights.getComponent(i, slot);
      if (targetInfluence < 0.08) continue;
      if (!belongsElsewhere && targetDistanceSq <= Math.max(modelHeight * 0.035, targetRegionRadius * 0.72) ** 2) continue;
      excluded.push([mesh, i]); markerPositions.push(world.x, world.y, world.z); markerColors.push(0.15, 0.82, 1.0); outsideCount++;
    }
  });
  if (count < 8) {
    showStatus(avatarDisplayName, `La máscara no alcanzó suficientes vértices cerca de ${capture.targetBone} (${count}). Enfoca esa zona y marca sobre la malla.`);
    return { ready: false, mapped_vertices: count, message: 'SAM 2 encontró pocos vértices proyectables. Acerca la cámara a la región seleccionada.' };
  }
  discardHandSegmentationProposal();
  const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(markerPositions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(markerColors, 3));
  handSegmentationPoints = new THREE.Points(geometry, new THREE.PointsMaterial({ vertexColors: true, size: 0.018, sizeAttenuation: true, depthTest: true }));
  handSegmentationPoints.renderOrder = 5; scene.add(handSegmentationPoints);
  if (handWeightDiagnostic) handWeightDiagnostic.visible = false;
  handSegmentationProposal = { selected, excluded, bones: capture.bones, targetBone: capture.targetBone, score: Number(result.score) || 0, model: result.model };
  const message = `Propuesta SAM 2 · ${count} vértices cercanos a ${capture.targetBone}; ${outsideCount} pesos sobrantes visibles fuera de esa región. Amarillo: añadir; cian: retirar. Revisa antes de aplicar.`;
  showStatus(avatarDisplayName, message);
  return { ready: true, mapped_vertices: count, excluded_vertices: outsideCount, score: handSegmentationProposal.score, message };
}

function applyHandSegmentationProposal() {
  if (!handSegmentationProposal) return { changed: 0, message: 'Primero genera y revisa una propuesta SAM 2.' };
  avatarRoot.updateMatrixWorld(true);
  if (handSegmentationProposal.bones.some(([bone, matrix]) => !bone.matrixWorld.equals(matrix))) return {changed: 0, message: 'La pose cambió; genera otra propuesta antes de aplicar pesos.'};
  const acceptedProposal = handSegmentationProposal;
  const boneName = acceptedProposal.targetBone;
  const action = new Map(); let changed = 0, cleaned = 0;
  for (const [mesh, vertex] of handSegmentationProposal.selected) {
    const indices = mesh.geometry.attributes.skinIndex, weights = mesh.geometry.attributes.skinWeight;
    const boneIndex = mesh.skeleton.bones.findIndex(bone => bone.name === boneName);
    if (boneIndex < 0) continue;
    const point = mesh.getVertexPosition(vertex, new THREE.Vector3()); mesh.localToWorld(point);
    const oldIndices = [0,1,2,3].map((slot) => indices.getComponent(vertex, slot));
    const oldWeights = [0,1,2,3].map((slot) => weights.getComponent(vertex, slot));
    if (!oldWeights.some((value) => value > 0)) continue;
    const undo = action.get(mesh) || new Map();
    if (!undo.has(vertex)) undo.set(vertex, { indices: oldIndices, weights: oldWeights });
    const influence = new Map();
    oldIndices.forEach((index, slot) => influence.set(index, (influence.get(index) || 0) + oldWeights[slot] * 0.2));
    influence.set(boneIndex, (influence.get(boneIndex) || 0) + 0.8);
    const chosen = [...influence.entries()].filter(([, value]) => value > 1e-5).sort((a,b) => b[1]-a[1]).slice(0,4), total = chosen.reduce((sum, item) => sum + item[1], 0) || 1;
    for (let slot = 0; slot < 4; slot++) { indices.setComponent(vertex, slot, chosen[slot]?.[0] ?? boneIndex); weights.setComponent(vertex, slot, chosen[slot] ? chosen[slot][1] / total : 0); }
    action.set(mesh, undo); changed++;
  }
  // The accepted visible mask is also negative evidence: if the selected
  // bone still influences visible vertices outside its geometric neighborhood
  // and outside the mask, remove that stray influence while preserving the
  // relative weights of the remaining bones.
  for (const [mesh, vertex] of acceptedProposal.excluded) {
    const indices = mesh.geometry.attributes.skinIndex, weights = mesh.geometry.attributes.skinWeight;
    const boneIndex = mesh.skeleton.bones.findIndex((bone) => bone.name === boneName);
    if (boneIndex < 0) continue;
    const oldIndices = [0,1,2,3].map((slot) => indices.getComponent(vertex, slot));
    const oldWeights = [0,1,2,3].map((slot) => weights.getComponent(vertex, slot));
    const influence = new Map();
    oldIndices.forEach((index, slot) => influence.set(index, (influence.get(index) || 0) + oldWeights[slot]));
    const removed = influence.get(boneIndex) || 0;
    if (removed < 0.08) continue;
    influence.delete(boneIndex);
    let remaining = [...influence.values()].reduce((sum, value) => sum + value, 0);
    if (remaining <= 1e-8) {
      const node = mesh.skeleton.bones[boneIndex];
      const fallback = node.parent && mesh.skeleton.bones.indexOf(node.parent) >= 0
        ? mesh.skeleton.bones.indexOf(node.parent)
        : mesh.skeleton.bones.findIndex((candidate, index) => index !== boneIndex && candidate.isBone);
      if (fallback < 0) continue;
      influence.set(fallback, 1); remaining = 1;
    }
    const undo = action.get(mesh) || new Map();
    if (!undo.has(vertex)) undo.set(vertex, { indices: oldIndices, weights: oldWeights });
    const normalized = [...influence.entries()].map(([index, value]) => [index, value / remaining]);
    const chosen = normalized.filter(([, value]) => value > 1e-5).sort((a,b) => b[1]-a[1]).slice(0,4);
    const total = chosen.reduce((sum, item) => sum + item[1], 0) || 1;
    for (let slot = 0; slot < 4; slot++) {
      indices.setComponent(vertex, slot, chosen[slot]?.[0] ?? boneIndex);
      weights.setComponent(vertex, slot, chosen[slot] ? chosen[slot][1] / total : 0);
    }
    action.set(mesh, undo); cleaned++;
  }
  for (const mesh of action.keys()) { mesh.geometry.attributes.skinIndex.needsUpdate = true; mesh.geometry.attributes.skinWeight.needsUpdate = true; }
  const mappingReport = {
    method: 'sam2_prompt_projection_v1', model: acceptedProposal.model || 'facebook/sam2.1-hiera-tiny',
    target_bone: boneName,
    mapped_vertices: changed, cleaned_outside_vertices: cleaned, mask_score: acceptedProposal.score || 0,
    confidence: 'user_review', note: 'Amarillo asigna la región aceptada; cian retira influencia sobrante visible fuera de la máscara y de la región geométrica más cercana al hueso. Requiere revisión.'
  };
  if (action.size) { action.handMappingReport = mappingReport; editHistory.record('weights', action); }
  buildHandWeightDiagnostic(); handWeightsVisible = true; if (handWeightDiagnostic) handWeightDiagnostic.visible = true;
  discardHandSegmentationProposal();
  const message = `Propuesta aplicada a ${changed} vértices de ${boneName}; se retiró influencia sobrante en ${cleaned}. Revisa y usa Deshacer pesos si hace falta.`;
  if (changed || cleaned) handMappingReports.push(mappingReport);
  showStatus(avatarDisplayName, message);
  return { changed, cleaned, message };
}

function configureBonePivotDrag(enabled) {
  if (enabled && manualBoneRotations.size) {
    return { enabled: false, message: 'Restaura los giros manuales antes de arrastrar pivotes.' };
  }
  bonePivotDragEnabled = Boolean(enabled);
  if (bonePivotDragEnabled) {
    handWeightPaint.enabled = false;
    handSegmentation.enabled = false;
    discardSelectedBonePivotProposal();
    activeAction?.stop(); activeAction = null; manualPoseMode = true; clearProceduralOffsets(); restoreRestPose();
  }
  const message = bonePivotDragEnabled
    ? 'Arrastre de pivote activo · selecciona un hueso y arrastra su punto amarillo. Pulsa de nuevo para salir.'
    : 'Arrastre de pivote desactivado.';
  showStatus(avatarDisplayName, message);
  return { enabled: bonePivotDragEnabled, message };
}

function configureBonePivotAxis(axis = 'view') {
  const allowed = new Set(['view', 'x', 'y', 'z']);
  bonePivotAxisConstraint = allowed.has(String(axis).toLowerCase()) ? String(axis).toLowerCase() : 'view';
  return { axis: bonePivotAxisConstraint };
}

function nearestDescendantBones(bone) {
  const found = [];
  const visit = (node) => {
    for (const child of node.children) {
      if (child.isBone) found.push(child);
      else visit(child);
    }
  };
  visit(bone);
  return found;
}

function vectorMapValue(map, name) {
  const value = map.get(name);
  return value ? new THREE.Vector3(...value) : new THREE.Vector3();
}

function captureBonePivotAction(bone, children = nearestDescendantBones(bone)) {
  const affected = [bone, ...children];
  const skeletons = new Set();
  avatarRoot?.traverse((node) => { if (node.isSkinnedMesh && node.skeleton) skeletons.add(node.skeleton); });
  const names = affected.map((node) => node.name);
  return {
    positions: new Map(affected.map((node) => [node, node.position.clone()])),
    restPositions: new Map(affected.map((node) => [node, boneRestPositions.get(node)?.clone() || node.position.clone()])),
    positionOffsets: new Map(names.map((name) => [name, manualBonePositions.has(name) ? [...manualBonePositions.get(name)] : null])),
    pivotCompensations: new Map(names.map((name) => [name, bonePivotCompensations.has(name) ? [...bonePivotCompensations.get(name)] : null])),
    inverses: new Map([...skeletons].map((skeleton) => [skeleton, skeleton.boneInverses.map((matrix) => matrix.clone())])),
    worlds: new Map([...skeletons].map((skeleton) => [skeleton, skeleton.bones.map((node) => node.matrixWorld.clone())])),
    skeletons,
    childWorldPositions: new Map(children.map((child) => [child, child.getWorldPosition(new THREE.Vector3())])),
    startWorld: bone.getWorldPosition(new THREE.Vector3()),
    children,
  };
}

function restoreNamedMap(map, snapshots) {
  for (const [name, value] of snapshots || []) {
    if (value == null) map.delete(name);
    else map.set(name, [...value]);
  }
}

function boneWorldSegment(bone) {
  if (!bone) return null;
  const start = bone.getWorldPosition(new THREE.Vector3());
  const child = bone.children.find((node) => node.isBone);
  const end = child ? child.getWorldPosition(new THREE.Vector3()) : start.clone();
  const direction = end.sub(start);
  return { start, direction, lengthSq: direction.lengthSq() };
}

function distanceToBoneSegmentSq(point, segment) {
  const delta = point.clone().sub(segment.start);
  const along = segment.lengthSq > 1e-10 ? THREE.MathUtils.clamp(delta.dot(segment.direction) / segment.lengthSq, 0, 1) : 0;
  return point.distanceToSquared(segment.start.clone().addScaledVector(segment.direction, along));
}

function nearestBoneSegment(point, bones, excludedBone = null) {
  let nearest = null;
  for (const item of bones) {
    const bone = item.bone || item;
    if (bone === excludedBone) continue;
    const segment = item.segment || boneWorldSegment(bone);
    if (!segment || segment.lengthSq < 1e-10) continue;
    const distanceSq = distanceToBoneSegmentSq(point, segment);
    if (!nearest || distanceSq < nearest.distanceSq) nearest = { bone, segment, distanceSq };
  }
  return nearest;
}

function updatePivotOffsets(bone, children) {
  const rest = boneRestPositions.get(bone) || bone.position;
  const compensation = vectorMapValue(bonePivotCompensations, bone.name);
  const manual = bone.position.clone().sub(rest).sub(compensation);
  if (manual.lengthSq() < 1e-12) manualBonePositions.delete(bone.name);
  else manualBonePositions.set(bone.name, manual.toArray());
  for (const child of children) {
    const childRest = boneRestPositions.get(child) || child.position;
    const childManual = vectorMapValue(manualBonePositions, child.name);
    const childCompensation = child.position.clone().sub(childRest).sub(childManual);
    if (childCompensation.lengthSq() < 1e-12) bonePivotCompensations.delete(child.name);
    else bonePivotCompensations.set(child.name, childCompensation.toArray());
  }
}

function moveBonePivotToWorld(bone, targetWorld, { method = 'manual_pivot_edit', coalesce = false } = {}) {
  if (!bone?.parent || !avatarRoot) return { moved: false, message: 'El hueso seleccionado no tiene un pivote editable.' };
  avatarRoot.updateMatrixWorld(true);
  if (bone.getWorldPosition(new THREE.Vector3()).distanceTo(targetWorld) < 1e-9) return { moved: false, message: `El pivote ${bone.name} ya está en esa posición.` };
  const action = captureBonePivotAction(bone, moveBoneSection ? [] : nearestDescendantBones(bone));
  bone.position.copy(bone.parent.worldToLocal(targetWorld.clone()));
  avatarRoot.updateMatrixWorld(true);
  for (const [child, worldPosition] of action.childWorldPositions) {
    if (child.parent) child.position.copy(child.parent.worldToLocal(worldPosition.clone()));
  }
  avatarRoot.updateMatrixWorld(true);
  for (const skeleton of action.skeletons) {
    preserveBindTransforms(skeleton, action.worlds.get(skeleton), action.inverses.get(skeleton));
  }
  updatePivotOffsets(bone, action.children);
  const pivotEdit = {
    bone: bone.name, method, include_descendants: moveBoneSection, start_world: action.startWorld.toArray(),
    end_world: targetWorld.toArray(), confidence: 'user_review',
  };
  action.pivotEdit = pivotEdit;
  action.kind = coalesce ? (moveBoneSection ? 'bone_section_control' : 'bone_position_control') : 'bone_pivot';
  action.boneName = bone.name;
  const last = editHistory.peek('bones');
  if (coalesce && last?.kind === action.kind && last.boneName === bone.name) {
    last.pivotEdit.end_world = [...pivotEdit.end_world];
  } else {
    editHistory.record('bones', action);
    bonePivotEdits.push(pivotEdit);
  }
  return { moved: true, action, message: moveBoneSection ? `Se movió la sección ${bone.name} con todas sus articulaciones descendientes.` : `Se movió ${bone.name} y se mantuvieron ancladas sus articulaciones descendientes.` };
}

function beginBonePivotDrag(event) {
  if (!bonePivotDragEnabled || !selectedBoneMarker?.visible || activeBonePivotDrag || !avatarRoot) return false;
  const bone = findBone(selectedBoneName);
  if (!bone || !bone.parent) return false;
  avatarRoot.updateMatrixWorld(true);
  bone.getWorldPosition(selectedBoneMarker.position);
  const rect = renderer.domElement.getBoundingClientRect();
  const projected = selectedBoneMarker.position.clone().project(camera);
  const markerX = rect.left + (projected.x * 0.5 + 0.5) * rect.width;
  const markerY = rect.top + (0.5 - projected.y * 0.5) * rect.height;
  if ((event.clientX - markerX) ** 2 + (event.clientY - markerY) ** 2 > 26 ** 2) return false;
  const normal = camera.getWorldDirection(new THREE.Vector3()).normalize();
  const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, selectedBoneMarker.position);
  pivotDragNdc.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  pivotDragRaycaster.setFromCamera(pivotDragNdc, camera);
  const startWorld = new THREE.Vector3();
  if (!pivotDragRaycaster.ray.intersectPlane(plane, startWorld)) return false;
  const pivotSnapshot = captureBonePivotAction(bone, moveBoneSection ? [] : nearestDescendantBones(bone));
  activeBonePivotDrag = {
    ...pivotSnapshot, section: moveBoneSection, bone, plane, pointerStartWorld: startWorld.clone(),
    pointerOffset: pivotSnapshot.startWorld.clone().sub(startWorld), moved: false,
  };
  controls.enabled = false;
  renderer.domElement.setPointerCapture?.(event.pointerId);
  event.preventDefault();
  return true;
}

function updateBonePivotDrag(event) {
  if (!activeBonePivotDrag) return false;
  const rect = renderer.domElement.getBoundingClientRect();
  pivotDragNdc.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  pivotDragRaycaster.setFromCamera(pivotDragNdc, camera);
  const target = new THREE.Vector3();
  if (!pivotDragRaycaster.ray.intersectPlane(activeBonePivotDrag.plane, target)) return true;
  let worldTarget = target.clone().add(activeBonePivotDrag.pointerOffset);
  if (bonePivotAxisConstraint !== 'view') {
    const axis = new THREE.Vector3(
      bonePivotAxisConstraint === 'x' ? 1 : 0,
      bonePivotAxisConstraint === 'y' ? 1 : 0,
      bonePivotAxisConstraint === 'z' ? 1 : 0,
    );
    const normal = activeBonePivotDrag.plane.normal;
    const projectedAxis = axis.clone().addScaledVector(normal, -axis.dot(normal));
    if (projectedAxis.lengthSq() < 1e-6) {
      showStatus(avatarDisplayName, `El eje ${bonePivotAxisConstraint.toUpperCase()} apunta hacia la cámara; cambia a otra vista para arrastrarlo.`);
      return true;
    }
    const cursorDelta = target.sub(activeBonePivotDrag.pointerStartWorld);
    const amount = cursorDelta.dot(projectedAxis) / projectedAxis.lengthSq();
    worldTarget = activeBonePivotDrag.startWorld.clone().addScaledVector(axis, amount);
  }
  activeBonePivotDrag.bone.position.copy(activeBonePivotDrag.bone.parent.worldToLocal(worldTarget));
  avatarRoot.updateMatrixWorld(true);
  // A bone's local translation normally carries its entire subtree. Keep the
  // nearest child joints anchored in world space so this edits one pivot only.
  for (const [child, childWorld] of activeBonePivotDrag.childWorldPositions) {
    if (child.parent) child.position.copy(child.parent.worldToLocal(childWorld.clone()));
  }
  avatarRoot.updateMatrixWorld(true);
  for (const skeleton of activeBonePivotDrag.skeletons) {
    preserveBindTransforms(skeleton, activeBonePivotDrag.worlds.get(skeleton), activeBonePivotDrag.inverses.get(skeleton));
  }
  activeBonePivotDrag.moved = activeBonePivotDrag.bone.position.distanceTo(activeBonePivotDrag.positions.get(activeBonePivotDrag.bone)) > 1e-6;
  event.preventDefault();
  return true;
}

function finishBonePivotDrag() {
  const drag = activeBonePivotDrag;
  if (!drag) return false;
  activeBonePivotDrag = null;
  controls.enabled = true;
  if (!drag.moved) {
    for (const [bone, position] of drag.positions) bone.position.copy(position);
    restoreNamedMap(manualBonePositions, drag.positionOffsets);
    restoreNamedMap(bonePivotCompensations, drag.pivotCompensations);
    for (const [skeleton, inverses] of drag.inverses) {
      skeleton.boneInverses = inverses.map((matrix) => matrix.clone());
      skeleton.update();
    }
    avatarRoot.updateMatrixWorld(true);
    return false;
  }
  avatarRoot.updateMatrixWorld(true);
  for (const skeleton of drag.skeletons) preserveBindTransforms(skeleton, drag.worlds.get(skeleton), drag.inverses.get(skeleton));
  updatePivotOffsets(drag.bone, drag.children);
  const end = drag.bone.getWorldPosition(new THREE.Vector3()).toArray();
  const pivotEdit = { bone: drag.bone.name, method: drag.section ? 'manual_section_drag' : 'manual_view_drag_camera_plane', start_world: drag.startWorld.toArray(), end_world: end, confidence: 'user_review' };
  drag.pivotEdit = pivotEdit;
  drag.kind = 'bone_pivot';
  drag.boneName = drag.bone.name;
  editHistory.record('bones', drag);
  bonePivotEdits.push(pivotEdit);
  showStatus(avatarDisplayName, drag.section ? `Sección ${drag.bone.name} y todas sus articulaciones descendientes movidas. Puedes deshacer o guardar.` : `Pivote ${drag.bone.name} movido sin desplazar sus articulaciones hijas. La pose neutral se conservó; puedes deshacer o guardar.`);
  return true;
}

renderer.domElement.addEventListener('pointerdown', (event) => {
  if (event.button !== 0) return;
  if (beginBonePivotDrag(event)) return;
  if (handSegmentation.enabled) { requestHandSegmentation(event); return; }
  if (!handWeightPaint.enabled) return;
  handPaintPointerDown = true; handPaintStroke = new Map(); controls.enabled = false; paintHandWeights(event);
});
renderer.domElement.addEventListener('pointermove', (event) => { if (activeBonePivotDrag) updateBonePivotDrag(event); else if (handPaintPointerDown) paintHandWeights(event); });
window.addEventListener('pointerup', () => {
  finishBonePivotDrag();
  if (handPaintPointerDown) { if ([...handPaintStroke.values()].some((undo) => undo.size)) editHistory.record('weights', handPaintStroke); handPaintPointerDown = false; handPaintStroke = null; controls.enabled = true; }
});
window.addEventListener('pointercancel', () => {
  finishBonePivotDrag();
  if (handPaintPointerDown) { handPaintPointerDown = false; handPaintStroke = null; controls.enabled = true; }
});

const gltfLoader = new GLTFLoader();
gltfLoader.register((parser) => new VRMLoaderPlugin(parser));
let vrm = null;
let avatarRoot = null;
let sourceGltf = null;
let autoRigBones = [];
let hostBridge = null;
let skeletonHelper = null;
let selectedBoneMarker = null;
let bonePivotProposalMarker = null;
let pendingBonePivotProposal = null;
let handDiagnosticGroup = null;
let boneLandmarkDiagnostics = {};
let handWeightDiagnostic = null;
let handDiagnosticsVisible = false;
let handWeightsVisible = false;
const handWeightSources = [];
let handWeightPaint = { enabled: false, boneName: '', radiusRatio: 0.012, strength: 0.25 };
const handPaintSelection = new Map();
let handPaintSelectionGroup = null;
let handPaintSelectionSources = [];
// One Undo/Redo across weight edits and bone-pivot edits; see edit_history.js.
const editHistory = createEditHistory({
  onChange: ({ canUndo, canRedo }) => { callHost('historyChanged', canUndo, canRedo).catch(() => {}); },
});
let handPaintPointerDown = false;
let handPaintStroke = null;
let moveBoneSection = false;
let selectedBoneName = '';
let skeletonVisible = true;
let manualPoseMode = false;
let operationBusy = false;
const boneRestQuaternions = new Map();
const boneRestPositions = new Map();
const boneOriginalRestPositions = new Map();
const boneRestInverses = new Map();
const manualBoneRotations = new Map();
const manualBonePositions = new Map();
const bonePivotCompensations = new Map();
let avatarDisplayName = 'Avatar 3D';
let modelLoadState = 'idle';
let modelLoadError = '';
let mixer = null;
let activeAction = null;
let currentPlan = { state: 'idle', action: 'idle', locomotion: 'stand', intensity: 0.3, gaze: [0, 0], expression: 'neutral', viseme: { shape: 'silence' }, speech: { energy: 0, emphasis: 0 } };
let elapsed = 0;
let gaitPhase = 0;
let nodPhase = 0;
let nextNod = 2.5;
let blinkRemaining = 0;
let nextBlink = 3;
let lastTime = performance.now();
let rootRestY = 0;
const gazeTarget = new THREE.Object3D();
scene.add(gazeTarget);
const proceduralOffsets = new Map();
const VISEMES = { silence: {}, closed: {}, open: { aa: 0.8 }, wide: { ih: 0.85 }, round: { ou: 0.85 }, teeth: { ee: 0.75 } };

function connectHost() {
  if (!window.qt?.webChannelTransport || !window.QWebChannel) return;
  new QWebChannel(window.qt.webChannelTransport, (channel) => { hostBridge = channel.objects.meshingupHost || null; });
}
connectHost();

function callHost(method, ...args) {
  return new Promise((resolve, reject) => {
    if (!hostBridge || typeof hostBridge[method] !== 'function') return reject(new Error('Canal de MeshingUp no disponible.'));
    const timeout = setTimeout(() => reject(new Error(`MeshingUp no respondió a ${method}.`)), 60000);
    try {
      hostBridge[method](...args, (result) => { clearTimeout(timeout); resolve(result); });
    } catch (error) { clearTimeout(timeout); reject(error); }
  });
}

async function transferGlb(report) {
  await callHost('reportProgress', 'Exportando modelo y esqueleto a GLB…');
  const blob = await new GLTFExporter().parseAsync(avatarRoot, { binary: true, animations: gltfAnimations });
  const bytes = new Uint8Array(blob);
  const transferId = await callHost('beginAutoRig', avatarDisplayName, bytes.byteLength, JSON.stringify(report));
  if (!transferId) throw new Error('MeshingUp rechazó el archivo generado.');
  const chunkSize = 48 * 1024;
  try {
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
      const chunk = bytes.subarray(offset, Math.min(bytes.length, offset + chunkSize));
      await callHost('writeAutoRigChunk', transferId, btoa(String.fromCharCode(...chunk)));
      if (offset % (chunkSize * 32) === 0 || offset + chunk.length === bytes.length) {
        const message = `Guardando GLB · ${Math.round((offset + chunk.length) / bytes.length * 100)}%`;
        showStatus(avatarDisplayName, message);
        await callHost('reportProgress', message);
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    }
    const saved = await callHost('finishAutoRig', transferId);
    if (!saved) throw new Error('MeshingUp no pudo guardar el GLB generado.');
  } catch (error) {
    hostBridge.cancelAutoRig(transferId);
    throw error;
  }
}

function resize() {
  const width = canvas.clientWidth || window.innerWidth;
  const height = canvas.clientHeight || window.innerHeight;
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
resize();

function showStatus(title, detail) {
  nameLabel.textContent = title;
  detailLabel.textContent = detail;
}

function setExpression(name, value) {
  const amount = Math.max(0, Math.min(1, value));
  if (vrm?.expressionManager) {
    try { vrm.expressionManager.setValue(name, amount); } catch (_) { /* optional expression */ }
    return;
  }
  const aliases = { blink: ['blink', 'eyeclose'], happy: ['happy', 'joy'], sad: ['sad'], aa: ['aa', 'mouthopen', 'jawopen'], ih: ['ih'], ou: ['ou'], ee: ['ee'], oh: ['oh'] }[name] || [name];
  avatarRoot?.traverse((mesh) => {
    if (!mesh.morphTargetDictionary || !mesh.morphTargetInfluences) return;
    for (const [key, index] of Object.entries(mesh.morphTargetDictionary)) {
      const normalized = key.toLowerCase().replace(/[^a-z]/g, '');
      if (aliases.some((alias) => normalized.includes(alias))) mesh.morphTargetInfluences[index] = amount;
    }
  });
}

function clearProceduralOffsets() {
  for (const node of proceduralOffsets.keys()) {
    const rest = boneRestQuaternions.get(node);
    if (rest) {
      node.quaternion.copy(rest);
      const manual = manualBoneRotations.get(node.name);
      if (manual) node.quaternion.multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(...manual, 'XYZ')));
      const position = boneRestPositions.get(node);
      if (position) node.position.copy(position)
        .add(vectorMapValue(manualBonePositions, node.name))
        .add(vectorMapValue(bonePivotCompensations, node.name));
    }
  }
  proceduralOffsets.clear();
}

function refreshBoneData() {
  if (activeBonePivotDrag) {
    activeBonePivotDrag = null;
    controls.enabled = true;
  }
  bonePivotDragEnabled = false;
  discardSelectedBonePivotProposal();
  const detectedBones = [];
  avatarRoot?.traverse((node) => { if (node.isBone) detectedBones.push(node); });
  const sameRig = detectedBones.length === autoRigBones.length && detectedBones.every((node) => boneRestQuaternions.has(node));
  if (!sameRig) {
    clearHandPaintSelection();
    boneRestQuaternions.clear();
    boneRestPositions.clear();
    boneOriginalRestPositions.clear();
    boneRestInverses.clear();
    manualBoneRotations.clear();
    manualBonePositions.clear();
    bonePivotCompensations.clear();
    editHistory.clear();
    bonePivotEdits.length = 0;
    for (const node of detectedBones) {
      boneRestQuaternions.set(node, node.quaternion.clone());
      boneRestPositions.set(node, node.position.clone());
      boneOriginalRestPositions.set(node, node.position.clone());
    }
    const skeletons = new Set();
    avatarRoot?.traverse((node) => { if (node.isSkinnedMesh && node.skeleton) skeletons.add(node.skeleton); });
    for (const skeleton of skeletons) boneRestInverses.set(skeleton, skeleton.boneInverses.map((matrix) => matrix.clone()));
  }
  autoRigBones = detectedBones;
  if (skeletonHelper) scene.remove(skeletonHelper);
  skeletonHelper = avatarRoot ? new THREE.SkeletonHelper(avatarRoot) : null;
  if (skeletonHelper) {
    skeletonHelper.material = new THREE.LineBasicMaterial({ color: 0x70f0d0, depthTest: false, transparent: true, opacity: 0.95 });
    skeletonHelper.visible = skeletonVisible;
    skeletonHelper.renderOrder = 10;
    scene.add(skeletonHelper);
  }
  if (handDiagnosticGroup) scene.remove(handDiagnosticGroup);
  handDiagnosticGroup = new THREE.Group();
  const regionColor = (name) => name.includes('Toe') || name.includes('Foot') ? 0xffc857
    : name.includes('Leg') ? 0x62a7ff : name.includes('Arm') || name.includes('Hand') || /Index|Middle|Ring|Little|Thumb/.test(name) ? 0x5de0a5
      : name === 'head' || name === 'neck' || name === 'jaw' ? 0xc77dff : 0xff4f71;
  const markerSize = Math.max(0.0015, new THREE.Box3().setFromObject(avatarRoot).getSize(new THREE.Vector3()).y * 0.0022);
  for (const bone of autoRigBones) {
    const quality = boneLandmarkDiagnostics[bone.name]?.confidence;
    const markerColor = quality === 'low' ? 0xff4f71 : quality === 'medium' ? 0xffc857 : regionColor(bone.name);
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(markerSize, 6, 4),
      new THREE.MeshBasicMaterial({ color: markerColor, depthTest: false, depthWrite: false }),
    );
    marker.name = `MeshingUp joint diagnostic · ${bone.name}`;
    marker.userData.bone = bone;
    marker.renderOrder = 12;
    handDiagnosticGroup.add(marker);
  }
  handDiagnosticGroup.visible = handDiagnosticsVisible;
  scene.add(handDiagnosticGroup);
  if (selectedBoneMarker) {
    scene.remove(selectedBoneMarker);
    selectedBoneMarker.geometry.dispose();
    selectedBoneMarker.material.dispose();
  }
  selectedBoneMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.018, 12, 8),
    new THREE.MeshBasicMaterial({ color: 0xffc857, depthTest: false, transparent: true, opacity: 0.95 }),
  );
  selectedBoneMarker.renderOrder = 11;
  selectedBoneMarker.visible = false;
  scene.add(selectedBoneMarker);
}

function findBone(name) {
  let found = null;
  const wanted = String(name).toLowerCase();
  avatarRoot?.traverse((node) => { if (!found && node.isBone && node.name.toLowerCase() === wanted) found = node; });
  return found;
}

function setHandDiagnostics(visible) {
  handDiagnosticsVisible = Boolean(visible);
  if (!handDiagnosticGroup) return { visible: handDiagnosticsVisible, markers: 0 };
  avatarRoot?.updateMatrixWorld(true);
  for (const marker of handDiagnosticGroup.children) {
    marker.userData.bone?.getWorldPosition(marker.position);
  }
  handDiagnosticGroup.visible = handDiagnosticsVisible;
  const markers = handDiagnosticGroup.children.length;
  showStatus(avatarDisplayName, handDiagnosticsVisible
    ? `Diagnóstico corporal · ${markers} articulaciones marcadas por región`
    : 'Marcadores de articulaciones ocultos');
  return { visible: handDiagnosticsVisible, markers };
}

function setBoneLandmarkDiagnostics(diagnostics) {
  boneLandmarkDiagnostics = diagnostics && typeof diagnostics === 'object' ? diagnostics : {};
  if (avatarRoot) refreshBoneData();
  const low = Object.values(boneLandmarkDiagnostics).filter((item) => item?.confidence === 'low').length;
  const medium = Object.values(boneLandmarkDiagnostics).filter((item) => item?.confidence === 'medium').length;
  return { low_confidence_bones: low, medium_confidence_bones: medium };
}

function buildHandWeightDiagnostic(targetBone = selectedBoneName) {
  if (handWeightDiagnostic) {
    scene.remove(handWeightDiagnostic);
    handWeightDiagnostic.traverse((node) => {
      node.geometry?.dispose();
      if (Array.isArray(node.material)) node.material.forEach((material) => material.dispose());
      else node.material?.dispose();
    });
  }
  handWeightSources.length = 0;
  handWeightDiagnostic = new THREE.Group();
  const cold = new THREE.Color('#29445c'), hot = new THREE.Color('#ff4f71');
  const outside = new THREE.Color('#35d9e8');
  const modelHeight = Math.max(new THREE.Box3().setFromObject(avatarRoot).getSize(new THREE.Vector3()).y, 1e-4);
  const colors = [];
  avatarRoot?.traverse((mesh) => {
    if (!mesh.isSkinnedMesh || !mesh.geometry.attributes.skinIndex || !mesh.geometry.attributes.skinWeight) return;
    const boneNames = mesh.skeleton.bones.map((bone) => bone.name);
    const indexAttr = mesh.geometry.attributes.skinIndex, weightAttr = mesh.geometry.attributes.skinWeight;
    const boneIndex = boneNames.indexOf(targetBone);
    if (boneIndex < 0) return;
    const bone = mesh.skeleton.bones[boneIndex], selectedSegment = boneWorldSegment(bone);
    const skeletonSegments = mesh.skeleton.bones
      .filter((candidate) => candidate !== bone)
      .map((candidate) => ({ bone: candidate, segment: boneWorldSegment(candidate) }))
      .filter((item) => item.segment && item.segment.lengthSq >= 1e-10);
    const cleanupRadius = selectedSegment?.lengthSq > 1e-10
      ? Math.max(modelHeight * 0.035, Math.sqrt(selectedSegment.lengthSq) * 0.8) : 0;
    const vertexIndices = [], boneColors = [], point = new THREE.Vector3();
    let spillCount = 0;
    for (let vertex = 0; vertex < indexAttr.count; vertex++) {
      let influence = 0;
      for (let slot = 0; slot < 4; slot++) {
        const weight = weightAttr.getComponent(vertex, slot);
        if (indexAttr.getComponent(vertex, slot) === boneIndex) influence += weight;
      }
      if (influence < 0.08) continue;
      vertexIndices.push(vertex);
      mesh.getVertexPosition(vertex, point); mesh.localToWorld(point);
      const targetDistanceSq = selectedSegment ? distanceToBoneSegmentSq(point, selectedSegment) : 0;
      const closest = selectedSegment && Math.sqrt(targetDistanceSq) > cleanupRadius
        ? nearestBoneSegment(point, skeletonSegments, bone) : null;
      const isOutside = Boolean(closest && Math.sqrt(closest.distanceSq) + modelHeight * 0.012 < Math.sqrt(targetDistanceSq));
      if (isOutside) spillCount++;
      const color = isOutside ? outside : cold.clone().lerp(hot, Math.min(1, influence));
      boneColors.push(color.r, color.g, color.b);
    }
    if (!vertexIndices.length) return;
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(vertexIndices.length * 3);
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(boneColors, 3));
    const points = new THREE.Points(geometry, new THREE.PointsMaterial({ size: 0.009, sizeAttenuation: true, vertexColors: true, depthTest: true, depthFunc: THREE.LessEqualDepth, depthWrite: false, transparent: true, opacity: 0.96 }));
    points.renderOrder = 13;
    handWeightDiagnostic.add(points);
    handWeightSources.push({ mesh, vertexIndices, positions, geometry, spillCount });
  });
  handWeightDiagnostic.visible = handWeightsVisible;
  scene.add(handWeightDiagnostic);
  return { vertices: handWeightSources.reduce((total, source) => total + source.vertexIndices.length, 0), outside: handWeightSources.reduce((total, source) => total + source.spillCount, 0) };
}

function updateHandWeightDiagnostic() {
  const point = new THREE.Vector3();
  for (const source of handWeightSources) {
    for (let i = 0; i < source.vertexIndices.length; i++) {
      source.mesh.getVertexPosition(source.vertexIndices[i], point);
      source.mesh.localToWorld(point);
      source.positions[i * 3] = point.x;
      source.positions[i * 3 + 1] = point.y;
      source.positions[i * 3 + 2] = point.z;
    }
    source.geometry.attributes.position.needsUpdate = true;
  }
}

function clearHandPaintSelection() {
  handPaintSelection.clear();
  if (handPaintSelectionGroup) {
    scene.remove(handPaintSelectionGroup);
    handPaintSelectionGroup.traverse((node) => {
      node.geometry?.dispose();
      if (Array.isArray(node.material)) node.material.forEach((material) => material.dispose());
      else node.material?.dispose();
    });
    handPaintSelectionGroup = null;
  }
  handPaintSelectionSources = [];
  return true;
}

function rebuildHandPaintSelectionOverlay() {
  if (handPaintSelectionGroup) {
    scene.remove(handPaintSelectionGroup);
    handPaintSelectionGroup.traverse((node) => {
      node.geometry?.dispose();
      if (Array.isArray(node.material)) node.material.forEach((material) => material.dispose());
      else node.material?.dispose();
    });
  }
  handPaintSelectionSources = [];
  handPaintSelectionGroup = new THREE.Group();
  handPaintSelectionGroup.name = 'MeshingUp · pincel · vértices tocados';
  const height = Math.max(new THREE.Box3().setFromObject(avatarRoot).getSize(new THREE.Vector3()).y, 1e-4);
  for (const [mesh, selected] of handPaintSelection) {
    if (!selected.size) continue;
    const vertices = [...selected], positions = new Float32Array(vertices.length * 3);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage));
    const points = new THREE.Points(geometry, new THREE.PointsMaterial({
      color: 0xa8ff3f, size: Math.max(height * 0.010, 0.006), sizeAttenuation: true,
      depthTest: true, depthFunc: THREE.LessEqualDepth, depthWrite: false, transparent: true, opacity: 1,
    }));
    points.renderOrder = 16;
    handPaintSelectionGroup.add(points);
    handPaintSelectionSources.push({ mesh, vertices, positions, geometry });
  }
  handPaintSelectionGroup.visible = handPaintSelectionSources.length > 0;
  scene.add(handPaintSelectionGroup);
  updateHandPaintSelectionOverlay();
}

function updateHandPaintSelectionOverlay() {
  const point = new THREE.Vector3();
  for (const source of handPaintSelectionSources) {
    for (let i = 0; i < source.vertices.length; i++) {
      source.mesh.getVertexPosition(source.vertices[i], point);
      source.mesh.localToWorld(point);
      source.positions[i * 3] = point.x;
      source.positions[i * 3 + 1] = point.y;
      source.positions[i * 3 + 2] = point.z;
    }
    source.geometry.attributes.position.needsUpdate = true;
  }
}

function setHandWeightDiagnostics(visible, targetBone = selectedBoneName) {
  handWeightsVisible = Boolean(visible);
  const diagnostic = buildHandWeightDiagnostic(targetBone);
  handWeightDiagnostic.visible = handWeightsVisible;
  showStatus(avatarDisplayName, handWeightsVisible
    ? `Pesos de ${targetBone} · ${diagnostic.vertices} vértices · azul/rosa: influencia local baja/alta · cian: influencia lejos del segmento (${diagnostic.outside})`
    : 'Vista de pesos oculta');
  return { visible: handWeightsVisible, mesh_count: handWeightSources.length };
}

function configureHandWeightPaint(enabled, boneName = selectedBoneName, options = {}) {
  const radiusRatio = Number(options.radiusRatio);
  const strength = Number(options.strength);
  handWeightPaint = {
    ...handWeightPaint,
    enabled: Boolean(enabled),
    boneName: boneName || selectedBoneName,
    radiusRatio: Number.isFinite(radiusRatio) ? THREE.MathUtils.clamp(radiusRatio, 0.002, 0.08) : handWeightPaint.radiusRatio,
    strength: Number.isFinite(strength) ? THREE.MathUtils.clamp(strength, 0.05, 1) : handWeightPaint.strength,
  };
  if (handWeightPaint.enabled && avatarRoot) {
    bonePivotDragEnabled = false;
    handSegmentation.enabled = false;
    activeAction?.stop(); activeAction = null;
    clearProceduralOffsets(); manualPoseMode = true; restoreRestPose();
    avatarRoot.updateMatrixWorld(true);
  }
  showStatus(avatarDisplayName, handWeightPaint.enabled
    ? `Pincel activo · ${handWeightPaint.boneName} · radio ${(handWeightPaint.radiusRatio * 100).toFixed(1)}% de altura · fuerza ${(handWeightPaint.strength * 100).toFixed(0)}%`
    : 'Pincel de pesos desactivado');
  return { ...handWeightPaint };
}

// Swap the stored vertex weights with the current ones and return the snapshot
// that reverses the swap, so the same routine serves both undo and redo.
function swapWeightSnapshot(action) {
  const inverse = new Map();
  for (const [mesh, undo] of action) {
    const indices = mesh.geometry.attributes.skinIndex, weights = mesh.geometry.attributes.skinWeight;
    const current = new Map();
    for (const [vertex, old] of undo) {
      current.set(vertex, {
        indices: [0, 1, 2, 3].map((slot) => indices.getComponent(vertex, slot)),
        weights: [0, 1, 2, 3].map((slot) => weights.getComponent(vertex, slot)),
      });
      for (let slot = 0; slot < 4; slot++) {
        indices.setComponent(vertex, slot, old.indices[slot]); weights.setComponent(vertex, slot, old.weights[slot]);
      }
    }
    indices.needsUpdate = true; weights.needsUpdate = true;
    inverse.set(mesh, current);
  }
  inverse.handMappingReport = action.handMappingReport;
  return inverse;
}

const weightHistoryHandler = {
  undo(action) {
    const inverse = swapWeightSnapshot(action);
    clearHandPaintSelection();
    if (action.handMappingReport) {
      const index = handMappingReports.indexOf(action.handMappingReport);
      if (index >= 0) handMappingReports.splice(index, 1);
    }
    buildHandWeightDiagnostic();
    showStatus(avatarDisplayName, 'Se deshizo la última edición de pesos.');
    return inverse;
  },
  redo(action) {
    const inverse = swapWeightSnapshot(action);
    clearHandPaintSelection();
    if (action.handMappingReport && !handMappingReports.includes(action.handMappingReport)) handMappingReports.push(action.handMappingReport);
    buildHandWeightDiagnostic();
    showStatus(avatarDisplayName, 'Se rehizo la edición de pesos.');
    return inverse;
  },
};
editHistory.register('weights', weightHistoryHandler);

function assignHandPaintSelectionToBone() {
  const boneName = handWeightPaint.boneName || selectedBoneName;
  if (!avatarRoot || !boneName) {
    return { changed: 0, message: 'Selecciona un hueso antes de asignar la región.' };
  }
  if (!handPaintSelection.size) {
    return { changed: 0, message: 'Marca primero una región con el pincel; los vértices verdes forman la selección.' };
  }
  const action = new Map();
  let changed = 0, selectedCount = 0, clearedOutside = 0, reassignedWithoutRemainder = 0, blockedOutside = 0;
  const meshesToClean = new Map(handPaintSelection);
  avatarRoot.traverse((mesh) => {
    if (mesh.isSkinnedMesh && !meshesToClean.has(mesh)) meshesToClean.set(mesh, new Set());
  });
  for (const [mesh, selected] of meshesToClean) {
    const indices = mesh.geometry.attributes.skinIndex;
    const weights = mesh.geometry.attributes.skinWeight;
    if (!indices || !weights || !mesh.skeleton) continue;
    const meshBone = mesh.skeleton.bones.find((bone) => bone.name === boneName);
    const boneIndex = meshBone ? mesh.skeleton.bones.indexOf(meshBone) : -1;
    if (boneIndex < 0) continue;
    const fallbackBones = [meshBone.parent, ...meshBone.children]
      .filter((bone) => bone?.isBone && mesh.skeleton.bones.includes(bone) && bone !== meshBone);
    const fallbackSegments = fallbackBones
      .map((bone) => ({ index: mesh.skeleton.bones.indexOf(bone), segment: boneWorldSegment(bone) }))
      .filter((item) => item.segment);
    const undo = new Map();
    const position = mesh.geometry.attributes.position;
    const local = new THREE.Vector3(), world = new THREE.Vector3();
    for (let vertex = 0; vertex < position.count; vertex++) {
      const oldIndices = [0, 1, 2, 3].map((slot) => indices.getComponent(vertex, slot));
      const oldWeights = [0, 1, 2, 3].map((slot) => weights.getComponent(vertex, slot));
      if (!oldWeights.some((weight) => weight > 0)) continue;
      const isSelected = selected.has(vertex);
      const influence = new Map();
      oldIndices.forEach((index, slot) => influence.set(index, (influence.get(index) || 0) + oldWeights[slot]));
      const previous = influence.get(boneIndex) || 0;
      if (isSelected) {
        selectedCount++;
        const alreadyAssigned = previous >= 0.999
          && [...influence.entries()].every(([index, value]) => index === boneIndex || value <= 1e-5);
        if (alreadyAssigned) continue;
        undo.set(vertex, { indices: oldIndices, weights: oldWeights });
        indices.setComponent(vertex, 0, boneIndex);
        weights.setComponent(vertex, 0, 1);
        for (let slot = 1; slot < 4; slot++) {
          indices.setComponent(vertex, slot, boneIndex);
          weights.setComponent(vertex, slot, 0);
        }
        changed++;
        continue;
      }
      if (previous <= 1e-5) continue;
      influence.delete(boneIndex);
      let chosen = [...influence.entries()]
        .filter(([, weight]) => weight > 1e-5)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4);
      if (!chosen.length && fallbackSegments.length) {
        mesh.getVertexPosition(vertex, local);
        world.copy(local);
        mesh.localToWorld(world);
        const nearest = fallbackSegments
          .map((candidate) => ({
            index: candidate.index,
            distance: distanceToBoneSegmentSq(world, candidate.segment),
          }))
          .sort((a, b) => a.distance - b.distance)[0];
        if (nearest) {
          chosen = [[nearest.index, 1]];
          reassignedWithoutRemainder++;
        }
      }
      if (!chosen.length) {
        blockedOutside++;
        continue;
      }
      undo.set(vertex, { indices: oldIndices, weights: oldWeights });
      const total = chosen.reduce((sum, item) => sum + item[1], 0) || 1;
      for (let slot = 0; slot < 4; slot++) {
        indices.setComponent(vertex, slot, chosen[slot]?.[0] ?? chosen[0][0]);
        weights.setComponent(vertex, slot, chosen[slot] ? chosen[slot][1] / total : 0);
      }
      clearedOutside++;
    }
    if (undo.size) {
      indices.needsUpdate = true;
      weights.needsUpdate = true;
      action.set(mesh, undo);
    }
  }
  let residualOutsideVertices = 0, residualOutsideWeight = 0;
  for (const [mesh, selected] of meshesToClean) {
    const indices = mesh.geometry.attributes.skinIndex;
    const weights = mesh.geometry.attributes.skinWeight;
    const boneIndex = mesh.skeleton?.bones.findIndex((bone) => bone.name === boneName) ?? -1;
    if (!indices || !weights || boneIndex < 0) continue;
    for (let vertex = 0; vertex < weights.count; vertex++) {
      if (selected.has(vertex)) continue;
      let influence = 0;
      for (let slot = 0; slot < 4; slot++) {
        if (indices.getComponent(vertex, slot) === boneIndex) influence += weights.getComponent(vertex, slot);
      }
      if (influence > 1e-4) {
        residualOutsideVertices++;
        residualOutsideWeight += influence;
      }
    }
  }
  if (action.size) editHistory.record('weights', action);
  buildHandWeightDiagnostic(boneName);
  handWeightsVisible = true;
  if (handWeightDiagnostic) handWeightDiagnostic.visible = true;
  rebuildHandPaintSelectionOverlay();
  const message = changed || clearedOutside || blockedOutside
    ? `${changed} vértices verdes asignados al 100% a ${boneName}; se retiró ese hueso de ${clearedOutside} vértices fuera de la selección${reassignedWithoutRemainder ? ` y ${reassignedWithoutRemainder} recibieron el hueso vecino más próximo` : ''}. Verificación: ${residualOutsideVertices} vértices fuera conservan influencia de ${boneName}${blockedOutside ? `; ${blockedOutside} no pudieron limpiarse por falta de hueso alternativo` : ''}. Deshaz si hace falta y guarda el GLB.`
    : selectedCount
      ? `Los ${selectedCount} vértices seleccionados ya estaban asignados a ${boneName}.`
      : 'La selección no contiene vértices ponderados válidos para el hueso seleccionado.';
  showStatus(avatarDisplayName, message);
  return { changed, selected: selectedCount, cleared_outside: clearedOutside, reassigned: reassignedWithoutRemainder, blocked_outside: blockedOutside, residual_outside_vertices: residualOutsideVertices, residual_outside_weight: residualOutsideWeight, bone: boneName, message };
}

function autoCorrectFingerWeights(side) {
  if (!avatarRoot || !['left', 'right'].includes(side)) return { changed: 0, message: 'Selecciona una mano válida.' };
  avatarRoot.updateMatrixWorld(true);
  const digitNames = ['Thumb', 'Index', 'Middle', 'Ring', 'Little'];
  const landmarks = [];
  for (const digit of digitNames) for (const segment of ['Proximal', 'Intermediate', 'Distal']) {
    const bone = findBone(`${side}${digit}${segment}`);
    if (bone) landmarks.push({ bone, position: bone.getWorldPosition(new THREE.Vector3()), color: digit });
  }
  if (landmarks.length < 6) return { changed: 0, message: 'No se encontraron suficientes huesos de dedos para proponer la reparación.' };
  const bounds = new THREE.Box3().setFromObject(avatarRoot);
  const radius = Math.max(0.012, (bounds.max.y - bounds.min.y) * 0.018), radiusSq = radius * radius;
  const action = new Map();
  let changed = 0, examined = 0;
  const world = new THREE.Vector3(), local = new THREE.Vector3();
  avatarRoot.traverse((mesh) => {
    if (!mesh.isSkinnedMesh) return;
    const indexAttr = mesh.geometry.attributes.skinIndex, weightAttr = mesh.geometry.attributes.skinWeight;
    if (!indexAttr || !weightAttr) return;
    const boneIndices = landmarks.map((item) => mesh.skeleton.bones.indexOf(item.bone));
    if (boneIndices.some((index) => index < 0)) return;
    const undo = new Map();
    const position = mesh.geometry.attributes.position;
    for (let vertex = 0; vertex < position.count; vertex++) {
      mesh.getVertexPosition(vertex, local);
      world.copy(local); mesh.localToWorld(world);
      let nearest = -1, nearestDistance = radiusSq;
      for (let i = 0; i < landmarks.length; i++) {
        const distance = world.distanceToSquared(landmarks[i].position);
        if (distance < nearestDistance) { nearest = i; nearestDistance = distance; }
      }
      if (nearest < 0) continue;
      examined++;
      const oldIndices = [0,1,2,3].map((slot) => indexAttr.getComponent(vertex, slot));
      const oldWeights = [0,1,2,3].map((slot) => weightAttr.getComponent(vertex, slot));
      if (!oldWeights.some((weight) => weight > 0)) continue;
      const targetIndex = boneIndices[nearest];
      const influence = new Map();
      oldIndices.forEach((index, slot) => influence.set(index, (influence.get(index) || 0) + oldWeights[slot]));
      const previous = influence.get(targetIndex) || 0;
      if (previous >= 0.98) continue;
      undo.set(vertex, { indices: oldIndices, weights: oldWeights });
      const target = Math.max(previous, 0.78);
      for (const [index, value] of influence) influence.set(index, index === targetIndex ? target : value * (1 - target) / Math.max(1e-8, 1 - previous));
      influence.set(targetIndex, target);
      const chosen = [...influence.entries()].filter(([, value]) => value > 1e-5).sort((a,b) => b[1]-a[1]).slice(0,4);
      const total = chosen.reduce((sum, item) => sum + item[1], 0) || 1;
      for (let slot = 0; slot < 4; slot++) {
        indexAttr.setComponent(vertex, slot, chosen[slot]?.[0] ?? targetIndex);
        weightAttr.setComponent(vertex, slot, chosen[slot] ? chosen[slot][1] / total : 0);
      }
      changed++;
    }
    if (undo.size) { indexAttr.needsUpdate = true; weightAttr.needsUpdate = true; action.set(mesh, undo); }
  });
  if (action.size) { editHistory.record('weights', action); buildHandWeightDiagnostic(); }
  if (changed) handWeightsVisible = true;
  if (handWeightDiagnostic) handWeightDiagnostic.visible = handWeightsVisible;
  const message = changed
    ? `Propuesta geométrica · ${changed} vértices próximos a articulaciones; requiere pose neutral y revisión de colores antes de guardar.`
    : `No hubo vértices corregibles cerca de los dedos (${examined} examinados).`;
  showStatus(avatarDisplayName, message);
  return { changed, examined, landmark_count: landmarks.length, radius, message };
}

function fitFingerSkeletonToWeights(side) {
  if (!avatarRoot || !['left', 'right'].includes(side)) return { moved: 0, message: 'Selecciona una mano válida.' };
  if (manualBonePositions.size || manualBoneRotations.size) return {moved: 0, message: 'Restaura el rig completo antes de cambiar sus articulaciones de reposo.'};
  activeAction?.stop(); activeAction = null; manualPoseMode = true; clearProceduralOffsets(); restoreRestPose();
  avatarRoot.updateMatrixWorld(true);
  const oldPositions = new Map(), oldRestPositions = new Map(), vertex = new THREE.Vector3();
  const skeletons = new Set();
  avatarRoot.traverse((mesh) => { if (mesh.isSkinnedMesh) skeletons.add(mesh.skeleton); });
  const oldInverses = new Map([...skeletons].map((skeleton) => [skeleton, skeleton.boneInverses.map((matrix) => matrix.clone())]));
  const oldWorlds = new Map([...skeletons].map((skeleton) => [skeleton, skeleton.bones.map((bone) => bone.matrixWorld.clone())]));
  const plans = [];
  const modelHeight = new THREE.Box3().setFromObject(avatarRoot).getSize(new THREE.Vector3()).y;
  let moved = 0, fitted = 0, sampleCount = 0;
  for (const digit of ['Thumb', 'Index', 'Middle', 'Ring', 'Little']) {
    const proximal = findBone(`${side}${digit}Proximal`);
    const intermediate = findBone(`${side}${digit}Intermediate`) || (digit === 'Thumb' ? findBone(`${side}ThumbMetacarpal`) : null);
    const distal = findBone(`${side}${digit}Distal`);
    if (!proximal || !intermediate || !distal) continue;
    proximal.updateMatrixWorld(true); distal.updateMatrixWorld(true);
    const start = proximal.getWorldPosition(new THREE.Vector3()), end = distal.getWorldPosition(new THREE.Vector3());
    const axis = end.clone().sub(start);
    if (axis.lengthSq() < 1e-8) continue;
    axis.normalize();
    const samples = [];
    avatarRoot.traverse((mesh) => {
      if (!mesh.isSkinnedMesh) return;
      const indices = mesh.geometry.attributes.skinIndex, weights = mesh.geometry.attributes.skinWeight, positions = mesh.geometry.attributes.position;
      if (!indices || !weights || !positions) return;
      const boneNames = mesh.skeleton.bones;
      const digitIndices = new Set(boneNames.map((bone, index) => bone.name.startsWith(`${side}${digit}`) ? index : -1).filter((index) => index >= 0));
      if (!digitIndices.size) return;
      for (let i = 0; i < positions.count; i++) {
        let influence = 0;
        for (let slot = 0; slot < 4; slot++) if (digitIndices.has(indices.getComponent(i, slot))) influence += weights.getComponent(i, slot);
        if (influence < 0.35) continue;
        mesh.getVertexPosition(i, vertex); mesh.localToWorld(vertex);
        const along = vertex.clone().sub(start).dot(axis);
        samples.push({ point: vertex.clone(), along, influence });
      }
    });
    if (samples.length < 12) continue;
    sampleCount += samples.length;
    const alongValues = samples.map((sample) => sample.along).sort((a,b) => a-b);
    const quantile = (q) => alongValues[Math.floor((alongValues.length - 1) * q)];
    const range = quantile(0.95) - quantile(0.05);
    if (range < modelHeight * 0.002 || range > modelHeight * 0.12) continue;
    const targetFor = (low, high, fallback) => {
      const group = samples.filter((sample) => sample.along >= quantile(low) && sample.along <= quantile(high));
      if (group.length < 3) return fallback;
      const center = group.reduce((sum, item) => sum.add(item.point), new THREE.Vector3()).multiplyScalar(1 / group.length);
      return center;
    };
    const interTarget = targetFor(0.48, 0.72, intermediate.getWorldPosition(new THREE.Vector3()));
    const distalTarget = targetFor(0.76, 0.94, distal.getWorldPosition(new THREE.Vector3()));
    const targets = [[intermediate, interTarget], [distal, distalTarget]];
    if (targets.some(([bone, target]) => !Number.isFinite(target.lengthSq()) || target.distanceTo(bone.getWorldPosition(new THREE.Vector3())) > modelHeight * 0.08)) continue;
    plans.push(...targets);
    fitted++;
  }
  for (const [bone] of plans) {
    oldPositions.set(bone, bone.position.clone());
    oldRestPositions.set(bone, boneRestPositions.get(bone)?.clone() || bone.position.clone());
  }
  for (const [bone, target] of plans) {
    if (!bone.parent) continue;
    bone.position.copy(bone.parent.worldToLocal(target.clone()));
    avatarRoot.updateMatrixWorld(true);
    moved++;
  }
  if (moved) {
    avatarRoot.updateMatrixWorld(true);
    for (const skeleton of skeletons) preserveBindTransforms(skeleton, oldWorlds.get(skeleton), oldInverses.get(skeleton));
    for (const [bone] of oldPositions) boneRestPositions.set(bone, bone.position.clone());
    editHistory.record('bones', { positions: oldPositions, restPositions: oldRestPositions, inverses: oldInverses });
  }
  const message = moved
    ? `Ajuste provisional · ${moved} articulaciones en ${fitted} dedos, según sus vértices ponderados. Revisa la silueta y deshaz si se deforma.`
    : 'No hay suficientes vértices con pesos de dedos para ajustar el esqueleto. Primero usa el pincel o la corrección automática.';
  showStatus(avatarDisplayName, message);
  return { moved, fitted_digits: fitted, sample_count: sampleCount, message };
}

// Swap bone positions, bind inverses and manual offsets with their stored
// values and return the snapshot that reverses the swap (undo and redo share it).
function swapBoneSnapshot(action) {
  const inverse = {
    kind: action.kind, boneName: action.boneName, pivotEdit: action.pivotEdit,
    positions: new Map(), restPositions: new Map(), inverses: new Map(),
    positionOffsets: new Map(), pivotCompensations: new Map(),
  };
  for (const [bone, position] of action.positions) {
    inverse.positions.set(bone, bone.position.clone());
    inverse.restPositions.set(bone, boneRestPositions.get(bone)?.clone() || bone.position.clone());
    bone.position.copy(position);
    boneRestPositions.set(bone, action.restPositions.get(bone));
  }
  for (const name of action.positionOffsets?.keys() || []) {
    inverse.positionOffsets.set(name, manualBonePositions.has(name) ? [...manualBonePositions.get(name)] : null);
  }
  for (const name of action.pivotCompensations?.keys() || []) {
    inverse.pivotCompensations.set(name, bonePivotCompensations.has(name) ? [...bonePivotCompensations.get(name)] : null);
  }
  restoreNamedMap(manualBonePositions, action.positionOffsets);
  restoreNamedMap(bonePivotCompensations, action.pivotCompensations);
  avatarRoot?.updateMatrixWorld(true);
  for (const [skeleton, inverses] of action.inverses) {
    inverse.inverses.set(skeleton, skeleton.boneInverses.map((matrix) => matrix.clone()));
    skeleton.boneInverses = inverses.map((matrix) => matrix.clone());
    skeleton.update();
  }
  avatarRoot?.updateMatrixWorld(true);
  return inverse;
}

const boneHistoryHandler = {
  undo(action) {
    const inverse = swapBoneSnapshot(action);
    if (action.pivotEdit) {
      const index = bonePivotEdits.lastIndexOf(action.pivotEdit);
      if (index >= 0) bonePivotEdits.splice(index, 1);
    }
    showStatus(avatarDisplayName, 'Se deshizo el último ajuste de pivote/huesos.');
    return inverse;
  },
  redo(action) {
    const inverse = swapBoneSnapshot(action);
    if (action.pivotEdit && !bonePivotEdits.includes(action.pivotEdit)) bonePivotEdits.push(action.pivotEdit);
    showStatus(avatarDisplayName, 'Se rehizo el ajuste de pivote/huesos.');
    return inverse;
  },
};
editHistory.register('bones', boneHistoryHandler);

function autoCorrectSelectedBoneWeights(boneName = selectedBoneName) {
  const bone = findBone(boneName);
  if (!bone || !avatarRoot) return { changed: 0, message: 'Selecciona primero un hueso del esqueleto.' };
  if (manualBoneRotations.size) return { changed: 0, message: 'Restaura los giros manuales para corregir pesos en pose neutral.' };
  activeAction?.stop(); activeAction = null; clearProceduralOffsets(); manualPoseMode = true; restoreRestPose();
  avatarRoot.updateMatrixWorld(true);
  const selectedSegment = boneWorldSegment(bone);
  const height = new THREE.Box3().setFromObject(avatarRoot).getSize(new THREE.Vector3()).y;
  const segmentLength = Math.sqrt(selectedSegment.lengthSq);
  const radius = Math.max(0.008, height * 0.014, segmentLength * 0.24), radiusSq = radius * radius;
  const cleanupRadius = Math.max(radius * 1.8, height * 0.035), cleanupRadiusSq = cleanupRadius * cleanupRadius;
  const margin = Math.max(height * 0.008, radius * 0.2);
  const action = new Map(); let examined = 0, changed = 0, cleaned = 0;
  const world = new THREE.Vector3(), local = new THREE.Vector3();
  avatarRoot.traverse((mesh) => {
    if (!mesh.isSkinnedMesh) return;
    const indices = mesh.geometry.attributes.skinIndex, weights = mesh.geometry.attributes.skinWeight;
    if (!indices || !weights) return;
    const boneIndex = mesh.skeleton.bones.indexOf(bone);
    if (boneIndex < 0) return;
    const boneSegments = mesh.skeleton.bones.map((item) => ({ bone: item, segment: boneWorldSegment(item) }))
      .filter((item) => item.segment && item.segment.lengthSq >= 1e-10);
    const adjacent = new Set([boneIndex]);
    const parentIndex = mesh.skeleton.bones.indexOf(bone.parent);
    if (parentIndex >= 0) adjacent.add(parentIndex);
    for (const childBone of bone.children) {
      const childIndex = mesh.skeleton.bones.indexOf(childBone);
      if (childBone.isBone && childIndex >= 0) adjacent.add(childIndex);
    }
    const undo = new Map(), position = mesh.geometry.attributes.position;
    for (let vertex = 0; vertex < position.count; vertex++) {
      mesh.getVertexPosition(vertex, local); world.copy(local); mesh.localToWorld(world);
      const oldIndices = [0,1,2,3].map((slot) => indices.getComponent(vertex, slot));
      const oldWeights = [0,1,2,3].map((slot) => weights.getComponent(vertex, slot));
      if (!oldWeights.some((value) => value > 0)) continue;
      const influence = new Map(); oldIndices.forEach((index, slot) => influence.set(index, (influence.get(index) || 0) + oldWeights[slot]));
      const previous = influence.get(boneIndex) || 0;
      const targetDistanceSq = distanceToBoneSegmentSq(world, selectedSegment);
      if (targetDistanceSq > cleanupRadiusSq && previous < 0.08) continue;
      let closestIndex = boneIndex, closestDistanceSq = targetDistanceSq;
      for (const item of boneSegments) {
        const otherIndex = mesh.skeleton.bones.indexOf(item.bone);
        if (otherIndex === boneIndex) continue;
        const distanceSq = distanceToBoneSegmentSq(world, item.segment);
        if (distanceSq < closestDistanceSq) { closestDistanceSq = distanceSq; closestIndex = otherIndex; }
      }
      const clearlyOutside = previous >= 0.08 && targetDistanceSq > cleanupRadiusSq
        && closestIndex !== boneIndex
        && Math.sqrt(targetDistanceSq) > Math.sqrt(closestDistanceSq) + margin;
      const locallyEligible = targetDistanceSq <= radiusSq
        && (adjacent.has(closestIndex) || Math.sqrt(targetDistanceSq) <= Math.sqrt(closestDistanceSq) + margin);
      if (clearlyOutside) {
        influence.delete(boneIndex);
        let chosen = [...influence.entries()].filter(([, value]) => value > 1e-5).sort((a,b) => b[1]-a[1]).slice(0,4);
        if (!chosen.length) chosen = [[Math.max(0, closestIndex), 1]];
        undo.set(vertex, { indices: oldIndices, weights: oldWeights });
        const total = chosen.reduce((sum, item) => sum + item[1], 0) || 1;
        for (let slot = 0; slot < 4; slot++) { indices.setComponent(vertex, slot, chosen[slot]?.[0] ?? chosen[0][0]); weights.setComponent(vertex, slot, chosen[slot] ? chosen[slot][1] / total : 0); }
        cleaned++;
        continue;
      }
      if (!locallyEligible) continue;
      examined++;
      if (previous >= 0.72) continue;
      undo.set(vertex, { indices: oldIndices, weights: oldWeights });
      const target = Math.max(previous, 0.72);
      for (const [index, value] of influence) influence.set(index, index === boneIndex ? target : value * (1 - target) / Math.max(1e-8, 1 - previous));
      influence.set(boneIndex, target);
      const chosen = [...influence.entries()].filter(([, value]) => value > 1e-5).sort((a,b) => b[1]-a[1]).slice(0,4), total = chosen.reduce((sum, item) => sum + item[1], 0) || 1;
      for (let slot = 0; slot < 4; slot++) { indices.setComponent(vertex, slot, chosen[slot]?.[0] ?? boneIndex); weights.setComponent(vertex, slot, chosen[slot] ? chosen[slot][1] / total : 0); }
      if (target > previous + 1e-5) changed++;
    }
    if (undo.size) { indices.needsUpdate = true; weights.needsUpdate = true; action.set(mesh, undo); }
  });
  if (action.size) { editHistory.record('weights', action); buildHandWeightDiagnostic(boneName); handWeightsVisible = true; handWeightDiagnostic.visible = true; }
  const message = changed || cleaned
    ? `Propuesta para ${boneName}: ${changed} vértices reforzados cerca del segmento y ${cleaned} influencias lejanas retiradas. Revisa el mapa y deshaz si hace falta.`
    : `No se encontraron pesos corregibles para ${boneName} (${examined} vértices locales revisados).`;
  showStatus(avatarDisplayName, message);
  return { changed, cleaned, examined, radius, cleanup_radius: cleanupRadius, bone: boneName, message };
}

function centerSelectedBonePivotOnWeights(boneName = selectedBoneName) {
  const bone = findBone(boneName);
  if (!bone || !avatarRoot) return { moved: false, message: 'Selecciona primero un hueso del esqueleto.' };
  if (manualBoneRotations.size) return { moved: false, message: 'Restaura los giros manuales antes de proponer un pivote.' };
  activeAction?.stop(); activeAction = null; manualPoseMode = true; clearProceduralOffsets(); restoreRestPose(); avatarRoot.updateMatrixWorld(true);
  const height = new THREE.Box3().setFromObject(avatarRoot).getSize(new THREE.Vector3()).y;
  let target = null, samples = 0, method = 'centroide_local_de_pesos';
  if (boneName.toLowerCase() === 'hips') {
    // Hips is the pelvis root, not the centroid of every vertex it influences.
    // Its semantic landmark is the midpoint between both thigh-root joints.
    const leftHip = findBone('leftUpperLeg'), rightHip = findBone('rightUpperLeg');
    if (!leftHip || !rightHip) return { moved: false, message: 'No encuentro ambas raíces de muslo; no moveré hips usando el centroide de sus pesos.' };
    const left = leftHip.getWorldPosition(new THREE.Vector3()), right = rightHip.getWorldPosition(new THREE.Vector3());
    const span = left.distanceTo(right);
    if (span < height * 0.025 || span > height * 0.40) return { moved: false, message: 'Las raíces de muslo no forman una referencia fiable para hips. Ajusta el pivote manualmente y revisa la pose.' };
    target = left.add(right).multiplyScalar(0.5);
    samples = 2;
    method = 'midpoint_between_thigh_roots';
  } else {
    const weightedPoints = [], total = new THREE.Vector3(), point = new THREE.Vector3(); let sum = 0;
    avatarRoot.traverse((mesh) => {
      if (!mesh.isSkinnedMesh) return;
      const indices = mesh.geometry.attributes.skinIndex, weights = mesh.geometry.attributes.skinWeight, boneIndex = mesh.skeleton.bones.indexOf(bone);
      if (!indices || !weights || boneIndex < 0) return;
      for (let vertex = 0; vertex < indices.count; vertex++) {
        let influence = 0, dominantWeight = -1, dominantIndex = -1;
        for (let slot = 0; slot < 4; slot++) {
          const index = indices.getComponent(vertex, slot), weight = weights.getComponent(vertex, slot);
          if (index === boneIndex) influence += weight;
          if (weight > dominantWeight) { dominantWeight = weight; dominantIndex = index; }
        }
        if (influence < 0.55 || dominantIndex !== boneIndex) continue;
        mesh.getVertexPosition(vertex, point); mesh.localToWorld(point);
        weightedPoints.push([point.clone(), influence]); total.addScaledVector(point, influence); sum += influence; samples++;
      }
    });
    if (samples < 8 || sum <= 0) return { moved: false, samples, message: `No hay suficientes pesos dominantes de ${boneName} para proponer un pivote.` };
    target = total.multiplyScalar(1 / sum);
    const maxSpread = Math.max(...weightedPoints.map(([position]) => position.distanceTo(target)));
    if (maxSpread > height * 0.045) return { moved: false, samples, spread: maxSpread, message: `Los pesos de ${boneName} cubren una zona amplia (${(maxSpread / height * 100).toFixed(1)}% de la altura); su centroide no representa una articulación. No moví el hueso.` };
  }
  if (!bone.parent || !target) return { moved: false, samples, message: `No pude obtener un hito anatómico seguro para ${boneName}.` };
  const current = bone.getWorldPosition(new THREE.Vector3());
  if (target.distanceTo(current) > height * 0.08) return { moved: false, samples, message: `La propuesta ${method} queda demasiado lejos de ${boneName}; no moví el hueso. Ajusta el pivote manualmente.` };
  discardSelectedBonePivotProposal();
  pendingBonePivotProposal = {
    bone, target: target.clone(), method, samples, distance: target.distanceTo(current),
    bones: [...boneRestQuaternions.keys()].map((node) => [node, node.matrixWorld.clone()]),
  };
  const markerSize = Math.max(height * 0.009, 0.008);
  bonePivotProposalMarker = new THREE.Mesh(
    new THREE.SphereGeometry(markerSize, 12, 8),
    new THREE.MeshBasicMaterial({ color: 0x35e0d0, depthTest: false, transparent: true, opacity: 0.98 }),
  );
  bonePivotProposalMarker.name = `MeshingUp proposed pivot · ${boneName}`;
  bonePivotProposalMarker.position.copy(target);
  bonePivotProposalMarker.renderOrder = 20;
  scene.add(bonePivotProposalMarker);
  const message = bone.name.toLowerCase() === 'hips'
    ? 'Propuesta hips: punto medio entre ambas raíces de muslo. El rig no se movió; el marcador cian muestra el candidato.'
    : `Propuesta ${boneName}: hito estimado desde ${samples} vértices compactos. El rig no se movió; el marcador cian muestra el candidato.`;
  showStatus(avatarDisplayName, message);
  return { moved: false, proposed: true, bone: boneName, samples, method, distance: target.distanceTo(current), message };
}

function discardSelectedBonePivotProposal() {
  pendingBonePivotProposal = null;
  if (bonePivotProposalMarker) {
    scene.remove(bonePivotProposalMarker);
    bonePivotProposalMarker.geometry.dispose();
    bonePivotProposalMarker.material.dispose();
    bonePivotProposalMarker = null;
  }
  return true;
}

function applySelectedBonePivotProposal() {
  const proposal = pendingBonePivotProposal;
  if (!proposal || !avatarRoot) return { moved: false, message: 'Primero genera y revisa una propuesta de pivote.' };
  avatarRoot.updateMatrixWorld(true);
  if (proposal.bones.some(([node, matrix]) => !node.matrixWorld.equals(matrix))) {
    discardSelectedBonePivotProposal();
    return { moved: false, message: 'El rig cambió después de generar la propuesta; no se aplicó. Genera otra.' };
  }
  const { bone, target, method, samples, distance } = proposal;
  if (!bone.parent) {
    discardSelectedBonePivotProposal();
    return { moved: false, message: 'El hueso no tiene padre editable; no se aplicó la propuesta.' };
  }
  if (manualBoneRotations.size) {
    discardSelectedBonePivotProposal();
    return { moved: false, message: 'Restaura los giros manuales antes de aplicar un pivote.' };
  }
  const result = moveBonePivotToWorld(bone, target, { method });
  discardSelectedBonePivotProposal();
  const message = bone.name.toLowerCase() === 'hips'
    ? 'Se aplicó el pivote hips en el punto medio entre ambas raíces de muslo. Revisa la pelvis; puedes deshacer.'
    : `Se aplicó el pivote ${bone.name} desde ${samples} vértices dominantes y compactos. Revisa y deshaz si no coincide con la articulación.`;
  showStatus(avatarDisplayName, message);
  return { moved: result.moved, bone: bone.name, samples, method, distance, message: result.moved ? message : result.message };
}

function playSelectedBoneTest(boneName = selectedBoneName) {
  const bone = findBone(boneName);
  if (!bone || !mixer) return { ok: false, message: 'Selecciona un hueso en un rig animable.' };
  mixer.stopAllAction(); activeAction = null;
  clearProceduralOffsets(); restoreRestPose(); manualPoseMode = true;
  avatarRoot.updateMatrixWorld(true);
  const rest = boneRestQuaternions.get(bone) || bone.quaternion.clone();
  const times = [0,.45,.9,1.35,1.8,2.25,2.7,3.15,3.6], values = [];
  const poses = [[0,0,0],[25,0,0],[0,0,0],[-25,0,0],[0,0,0],[0,25,0],[0,0,0],[0,0,25],[0,0,0]];
  for (const [x,y,z] of poses) {
    const delta = new THREE.Quaternion().setFromEuler(new THREE.Euler(THREE.MathUtils.degToRad(x),THREE.MathUtils.degToRad(y),THREE.MathUtils.degToRad(z),'XYZ'));
    const q = rest.clone().multiply(delta); values.push(q.x,q.y,q.z,q.w);
  }
  manualPoseMode = true;
  const clip = new THREE.AnimationClip(`MeshingUp Bone Test ${boneName}`,3.6,[new THREE.QuaternionKeyframeTrack(`${bone.name}.quaternion`,times,values)]);
  const action = mixer.clipAction(clip); action.reset(); action.setLoop(THREE.LoopOnce,1); action.clampWhenFinished=false; action.play(); activeAction=action;
  action.getMixer().addEventListener('finished',event=>{ if(event.action===action){action.stop();restoreRestPose();activeAction=null;manualPoseMode=true;} });
  showStatus(avatarDisplayName,`Prueba de movimiento del hueso ${boneName} · tres ejes · vuelve a reposo al terminar`);
  return { ok: true, bone: boneName, message: `Probando ${boneName}.` };
}

function focusHand(side) {
  const hand = findBone(`${side}Hand`);
  if (!hand) return false;
  focusBone(`${side}Hand`);
  showStatus(avatarDisplayName, `Mano ${side === 'left' ? 'izquierda' : 'derecha'} · activa los marcadores de dedos para revisar articulaciones`);
  return true;
}
function focusBone(name) {
  const bone = findBone(name);
  if (!bone || !avatarRoot) return false;
  avatarRoot.updateMatrixWorld(true);
  const target = bone.getWorldPosition(new THREE.Vector3());
  const offset = camera.position.clone().sub(controls.target);
  if (offset.length() < 1e-4) offset.set(0, 0.15, 1);
  const height = new THREE.Box3().setFromObject(avatarRoot).getSize(new THREE.Vector3()).y;
  offset.setLength(Math.max(height * 0.065, Math.min(height * 0.14, 0.24)));
  controls.minDistance = Math.max(0.03, height * 0.025);
  controls.maxDistance = Math.max(4, height * 3);
  controls.target.copy(target); camera.position.copy(target).add(offset); camera.near = Math.max(0.001, height * 0.001); camera.updateProjectionMatrix(); controls.update();
  return true;
}

function setViewAxis(axis, sign = 1) {
  if (!['x', 'y', 'z'].includes(String(axis).toLowerCase())) return { ok: false, message: 'Eje de vista inválido.' };
  const normalizedAxis = String(axis).toLowerCase();
  const direction = Math.sign(Number(sign)) || 1;
  const bounds = avatarRoot ? new THREE.Box3().setFromObject(avatarRoot) : null;
  const target = bounds && !bounds.isEmpty() ? bounds.getCenter(new THREE.Vector3()) : controls.target.clone();
  const size = bounds && !bounds.isEmpty() ? bounds.getSize(new THREE.Vector3()) : new THREE.Vector3(1, 1, 1);
  const distance = Math.max(size.length() * 1.45, size.y * 2.2, 0.5);
  const offset = new THREE.Vector3();
  offset[normalizedAxis] = direction * distance;
  camera.up.set(0, 1, 0);
  if (normalizedAxis === 'y') camera.up.set(0, 0, direction > 0 ? -1 : 1);
  controls.target.copy(target);
  camera.position.copy(target).add(offset);
  camera.near = Math.max(0.001, size.y * 0.001);
  camera.far = Math.max(100, distance * 20);
  camera.updateProjectionMatrix();
  camera.lookAt(target);
  camera.updateMatrixWorld(true);
  controls.update();
  return { ok: true, axis: normalizedAxis, sign: direction, message: `Vista alineada con ${direction > 0 ? '+' : '−'}${normalizedAxis.toUpperCase()}.` };
}

function centerSelectedBoneOnAxis(axis) {
  const bone = findBone(selectedBoneName);
  const normalizedAxis = String(axis).toLowerCase();
  if (!bone || !avatarRoot || !['x', 'y'].includes(normalizedAxis)) {
    return { moved: false, message: 'Selecciona un hueso para centrarlo en X o Y.' };
  }
  if (manualBoneRotations.size) return { moved: false, message: 'Restaura los giros manuales antes de centrar el pivote.' };
  activeAction?.stop(); activeAction = null; clearProceduralOffsets(); restoreRestPose(); avatarRoot.updateMatrixWorld(true);
  const local = avatarRoot.worldToLocal(bone.getWorldPosition(new THREE.Vector3()));
  const modelCenter = avatarRoot.worldToLocal(new THREE.Box3().setFromObject(avatarRoot).getCenter(new THREE.Vector3()));
  local[normalizedAxis] = modelCenter[normalizedAxis];
  const target = avatarRoot.localToWorld(local);
  const result = moveBonePivotToWorld(bone, target, { method: `align_model_${normalizedAxis}_to_center` });
  if (result.moved) showStatus(avatarDisplayName, `Pivote ${bone.name} alineado con el centro geométrico del modelo en ${normalizedAxis.toUpperCase()}; puedes deshacer.`);
  return result;
}

function playHandTest(side) {
  if (!['left', 'right'].includes(side) || !mixer) return {ok:false,message:'No hay un rig animable para probar.'};
  mixer.stopAllAction(); activeAction = null;
  clearProceduralOffsets(); restoreRestPose(); manualPoseMode = true;
  const tracks = [], times = [0, 0.35, 0.75, 1.15, 1.55, 2.0];
  // Finger flexion is around the palm's width axis, which varies with the
  // imported rig's bone roll. A fixed local X axis makes some rigs twist.
  avatarRoot.updateMatrixWorld(true);
  const indexBase = findBone(`${side}IndexProximal`);
  const littleBase = findBone(`${side}LittleProximal`);
  const palmAxis = indexBase && littleBase
    ? littleBase.getWorldPosition(new THREE.Vector3()).sub(indexBase.getWorldPosition(new THREE.Vector3())).normalize()
    : new THREE.Vector3(1, 0, 0);
  for (const digit of ['Thumb','Index','Middle','Ring','Little']) {
    for (const [segment, amount] of [['Proximal',0.52],['Intermediate',0.72],['Distal',0.48]]) {
      const bone = findBone(`${side}${digit}${segment}`);
      if (!bone) continue;
      const rest = boneRestQuaternions.get(bone) || bone.quaternion.clone();
      const restWorld = bone.getWorldQuaternion(new THREE.Quaternion());
      const axisLocal = palmAxis.clone().applyQuaternion(restWorld.invert()).normalize();
      const values = [];
      for (const angle of [0, amount, amount * 1.25, amount * 0.55, -0.12, 0]) {
        const q = rest.clone().multiply(new THREE.Quaternion().setFromAxisAngle(axisLocal, angle));
        values.push(q.x,q.y,q.z,q.w);
      }
      tracks.push(new THREE.QuaternionKeyframeTrack(`${bone.name}.quaternion`, times, values));
    }
  }
  if (!tracks.length) return {ok:false,message:'No hay falanges para generar la prueba.'};
  manualPoseMode = true;
  const clip = new THREE.AnimationClip(`MeshingUp Hand Test ${side}`, 2, tracks);
  const action = mixer.clipAction(clip); action.reset(); action.setLoop(THREE.LoopOnce,1); action.clampWhenFinished = false; action.play(); activeAction = action;
  action.getMixer().addEventListener('finished', (event) => { if (event.action === action) { action.stop(); restoreRestPose(); activeAction = null; manualPoseMode = true; } });
  showStatus(avatarDisplayName, `Prueba de falanges · ${side} · vuelve a reposo al terminar`);
  return {ok:true,message:`Probando mano ${side}.`};
}

function playWristTest(side) {
  if (!['left','right'].includes(side) || !mixer) return {ok:false,message:'No hay un rig animable para probar la muñeca.'};
  mixer.stopAllAction(); activeAction = null;
  clearProceduralOffsets(); restoreRestPose(); manualPoseMode = true;
  const bone = findBone(`${side}Hand`);
  if (!bone) return {ok:false,message:`No encuentro el hueso ${side}Hand.`};
  const rest = boneRestQuaternions.get(bone) || bone.quaternion.clone();
  const times = [0,.45,.9,1.35,1.8,2.25,2.7,3.15,3.6], values = [];
  const poses = [[0,0,0],[32,0,0],[0,0,0],[-32,0,0],[0,0,0],[0,28,0],[0,0,0],[0,0,28],[0,0,0]];
  for (const [x,y,z] of poses) {
    const delta = new THREE.Quaternion().setFromEuler(new THREE.Euler(THREE.MathUtils.degToRad(x),THREE.MathUtils.degToRad(y),THREE.MathUtils.degToRad(z),'XYZ'));
    const q = rest.clone().multiply(delta); values.push(q.x,q.y,q.z,q.w);
  }
  manualPoseMode = true;
  const clip = new THREE.AnimationClip(`MeshingUp Wrist Test ${side}`, 3.6, [new THREE.QuaternionKeyframeTrack(`${bone.name}.quaternion`,times,values)]);
  const action = mixer.clipAction(clip); action.reset(); action.setLoop(THREE.LoopOnce,1); action.clampWhenFinished=false; action.play(); activeAction=action;
  action.getMixer().addEventListener('finished', event => { if (event.action===action) { action.stop(); restoreRestPose(); activeAction=null; manualPoseMode=true; } });
  showStatus(avatarDisplayName,`Prueba de muñeca ${side} · flexión, extensión e inclinación local`);
  return {ok:true,message:`Probando muñeca ${side}.`};
}
function setBoneRotation(name, degrees) {
  const node = findBone(name);
  if (!node) return false;
  manualPoseMode = true;
  activeAction?.stop(); activeAction = null;
  const radians = degrees.map((value) => THREE.MathUtils.degToRad(Number(value) || 0));
  manualBoneRotations.set(name, radians);
  const rest = boneRestQuaternions.get(node) || node.quaternion.clone();
  node.quaternion.copy(rest).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(...radians, 'XYZ')));
  proceduralOffsets.delete(node);
  return true;
}
function resetBone(name) {
  manualBoneRotations.delete(name);
  const node = findBone(name), rest = node && boneRestQuaternions.get(node);
  if (node && rest) node.quaternion.copy(rest);
  if (node) setBonePosition(name, [0, 0, 0]);
}
function resetAllBones() {
  manualBoneRotations.clear();
  manualBonePositions.clear();
  bonePivotCompensations.clear();
  manualPoseMode = true;
  activeAction?.stop(); activeAction = null;
  for (const [bone, position] of boneOriginalRestPositions) boneRestPositions.set(bone, position.clone());
  restoreRestPose();
  avatarRoot?.updateMatrixWorld(true);
  for (const [skeleton, inverses] of boneRestInverses) {
    skeleton.boneInverses = inverses.map((matrix) => matrix.clone());
    skeleton.update();
  }
  editHistory.clear('bones');
  bonePivotEdits.length = 0;
  avatarRoot?.updateMatrixWorld(true);
}
function selectBone(name) {
  if (handPaintSelection.size && handWeightPaint.boneName && handWeightPaint.boneName !== name) clearHandPaintSelection();
  selectedBoneName = String(name || '');
  if (selectedBoneMarker) selectedBoneMarker.visible = Boolean(findBone(selectedBoneName));
  if (handWeightsVisible) setHandWeightDiagnostics(true, selectedBoneName);
  if (handWeightPaint.enabled) handWeightPaint.boneName = selectedBoneName;
  if (handSegmentation.enabled) handSegmentation.targetBone = selectedBoneName;
}
function getBonePosition(name) { return manualBonePositions.get(name) || [0, 0, 0]; }
function setBonePosition(name, offsets) {
  const node = findBone(name), rest = node && boneRestPositions.get(node);
  if (!node || !rest || !node.parent) return false;
  manualPoseMode = true;
  activeAction?.stop(); activeAction = null;
  clearProceduralOffsets();
  restoreRestPose();
  avatarRoot.updateMatrixWorld(true);
  const value = offsets.map((item) => Number(item) || 0);
  const targetLocal = rest.clone().add(new THREE.Vector3(...value)).add(vectorMapValue(bonePivotCompensations, name));
  const targetWorld = node.parent.localToWorld(targetLocal);
  return moveBonePivotToWorld(node, targetWorld, { method: 'manual_local_position_control', coalesce: true }).moved;
}

function restoreRestPose() {
  for (const [node, rest] of boneRestQuaternions) {
    node.quaternion.copy(rest);
    const manual = manualBoneRotations.get(node.name);
    if (manual) node.quaternion.multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(...manual, 'XYZ')));
    node.position.copy(boneRestPositions.get(node))
      .add(vectorMapValue(manualBonePositions, node.name))
      .add(vectorMapValue(bonePivotCompensations, node.name));
  }
}

function addBoneRotation(name, x = 0, y = 0, z = 0) {
  const boneName = VRMHumanBoneName[name] ?? name;
  const node = vrm?.humanoid?.getNormalizedBoneNode(boneName) || findBone(name);
  if (!node) return;
  const offset = new THREE.Quaternion().setFromEuler(new THREE.Euler(x, y, z, 'XYZ'));
  node.quaternion.multiply(offset);
  const combined = proceduralOffsets.get(node);
  proceduralOffsets.set(node, combined ? combined.multiply(offset) : offset);
}

let gltfAnimations = [];
const GENERATED_ACTIONS = [
  { action: 'idle', name: 'MeshingUp · Idle', duration: 3.2 },
  { action: 'listen', name: 'MeshingUp · Listening', duration: 2.8 },
  { action: 'think', name: 'MeshingUp · Thinking', duration: 3.6 },
  { action: 'talk', name: 'MeshingUp · Speaking', duration: 2.4 },
  { action: 'wait', name: 'MeshingUp · Waiting', duration: 3.4 },
  { action: 'pace', name: 'MeshingUp · Walk cycle', duration: 1.2 },
  { action: 'recover', name: 'MeshingUp · Recover', duration: 2.6 },
];

function generatedBone(name) {
  const humanName = VRMHumanBoneName[name];
  return (humanName && vrm?.humanoid?.getNormalizedBoneNode(humanName)) || findBone(name);
}

function actionAngles(action, name, phase) {
  const wave = Math.sin(phase), wave2 = Math.sin(phase * 2);
  const angles = [0, 0, 0];
  const set = (axis, value) => { angles[axis] = value; };
  if (name === 'spine' || name === 'chest') set(0, Math.sin(phase) * 0.018);
  if (name === 'head') set(0, Math.sin(phase * 0.5) * 0.018);
  if (action === 'listen') {
    if (name === 'head') set(0, Math.sin(phase * 0.5) * 0.045 + Math.pow(Math.max(0, wave2), 5) * 0.07);
    if (name === 'chest') set(2, Math.sin(phase * 0.5) * 0.025);
  } else if (action === 'think') {
    if (name === 'head') { set(0, -0.035); set(2, Math.sin(phase * 0.5) * 0.065); }
    if (name === 'rightUpperArm') { set(0, -0.12); set(2, 0.12); }
    if (name === 'rightLowerArm') set(0, -0.16 + wave * 0.025);
  } else if (action === 'talk') {
    if (name === 'head') { set(0, wave2 * 0.035); set(1, wave * 0.025); }
    if (name === 'jaw') set(0, 0.025 + Math.max(0, Math.sin(phase * 4)) * 0.13);
    if (name === 'leftUpperArm') set(2, wave * 0.16);
    if (name === 'leftLowerArm') set(0, -Math.max(0, wave) * 0.12);
    if (name === 'rightUpperArm') set(2, -wave * 0.12);
    if (name === 'rightLowerArm') set(0, Math.max(0, -wave) * 0.09);
    if (name.endsWith('Proximal') && /Index|Middle|Ring|Little/.test(name)) set(0, Math.max(0, wave) * 0.06);
  } else if (action === 'pace') {
    if (name === 'leftUpperLeg') set(0, wave * 0.34);
    if (name === 'rightUpperLeg') set(0, -wave * 0.34);
    if (name === 'leftLowerLeg') set(0, Math.max(0, -wave2) * 0.48);
    if (name === 'rightLowerLeg') set(0, Math.max(0, wave2) * 0.48);
    if (name === 'leftUpperArm') set(0, -wave * 0.22);
    if (name === 'rightUpperArm') set(0, wave * 0.22);
    if (name === 'leftLowerArm' || name === 'rightLowerArm') set(0, -0.12 + Math.max(0, wave2) * 0.1);
    if (name === 'hips') set(1, wave * 0.035);
  } else if (action === 'recover') {
    if (name === 'head') set(0, -0.05 + wave * 0.035);
    if (name === 'spine') set(0, -0.025 + wave * 0.018);
  }
  return angles;
}

function createGeneratedClip({ action, name, duration }) {
  const times = [];
  const sampleCount = 36;
  for (let index = 0; index <= sampleCount; index++) times.push(duration * index / sampleCount);
  const tracks = [];
  const tracked = new Set();
  const bones = ['hips', 'spine', 'chest', 'neck', 'head', 'jaw', 'leftShoulder', 'rightShoulder', 'leftUpperArm', 'rightUpperArm', 'leftLowerArm', 'rightLowerArm', 'leftUpperLeg', 'rightUpperLeg', 'leftLowerLeg', 'rightLowerLeg', 'leftFoot', 'rightFoot', 'leftToeBase', 'rightToeBase', 'leftIndexProximal', 'leftMiddleProximal', 'leftRingProximal', 'leftLittleProximal', 'rightIndexProximal', 'rightMiddleProximal', 'rightRingProximal', 'rightLittleProximal'];
  for (const semanticName of bones) {
    const bone = generatedBone(semanticName);
    if (!bone || tracked.has(bone)) continue;
    tracked.add(bone);
    const rest = boneRestQuaternions.get(bone) || bone.quaternion;
    const values = [];
    for (let index = 0; index <= sampleCount; index++) {
      const phase = (index / sampleCount) * Math.PI * 2;
      const [x, y, z] = actionAngles(action, semanticName, phase);
      const quaternion = rest.clone().multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(x, y, z, 'XYZ')));
      values.push(quaternion.x, quaternion.y, quaternion.z, quaternion.w);
    }
    tracks.push(new THREE.QuaternionKeyframeTrack(`${bone.name}.quaternion`, times, values));
  }
  return tracks.length ? new THREE.AnimationClip(name, duration, tracks) : null;
}

function ensureGeneratedAnimations() {
  for (const definition of GENERATED_ACTIONS) {
    const preferred = {
      idle: ['idle', 'rest', 'stand', 'breath'], listen: ['listen', 'attent'],
      think: ['think', 'process', 'ponder'], talk: ['talk', 'speak', 'explain'],
      wait: ['wait'], pace: ['walk', 'pace', 'nerv'], recover: ['recover'],
    }[definition.action] || [definition.action];
    if (gltfAnimations.some((clip) => preferred.some((token) => clip.name.toLowerCase().includes(token)))) continue;
    const clip = createGeneratedClip(definition);
    if (clip) gltfAnimations.push(clip);
  }
}

function chooseClip(plan) {
  if (!gltfAnimations.length) return null;
  const tokens = {
    idle: ['idle', 'rest', 'stand', 'breath'], listen: ['listen', 'attent'],
    think: ['think', 'process', 'ponder'], talk: ['talk', 'speak', 'explain'],
    wait: ['wait', 'idle', 'stand'], pace: ['walk', 'pace', 'nerv'], recover: ['recover', 'idle', 'stand'],
  }[plan.action] || [plan.action];
  if (plan.intent) tokens.unshift(plan.intent);
  for (const token of tokens) {
    const clip = gltfAnimations.find((item) => item.name.toLowerCase().includes(token));
    if (clip) return clip;
  }
  return null;
}

function crossFadeForPlan(plan) {
  if (!mixer) return;
  const clip = chooseClip(plan);
  if (!clip) {
    if (activeAction) activeAction.fadeOut(Math.max(0.12, plan.transition_seconds || 0.4));
    activeAction = null;
    return;
  }
  const next = mixer.clipAction(clip);
  next.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).fadeIn(Math.max(0.12, plan.transition_seconds || 0.4)).play();
  if (activeAction && activeAction !== next) activeAction.crossFadeTo(next, Math.max(0.12, plan.transition_seconds || 0.4), true);
  activeAction = next;
}

function playClipByName(name, loop = true) {
  if (!mixer) return false;
  const clip = gltfAnimations.find((item) => item.name === name);
  if (!clip) return false;
  manualPoseMode = false;
  manualBoneRotations.clear();
  manualBonePositions.clear();
  restoreRestPose();
  const next = mixer.clipAction(clip);
  next.reset().setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1);
  next.clampWhenFinished = !loop;
  next.setEffectiveTimeScale(1).setEffectiveWeight(1).fadeIn(0.2).play();
  if (activeAction && activeAction !== next) activeAction.crossFadeTo(next, 0.2, true);
  activeAction = next;
  showStatus(avatarDisplayName, `Reproduciendo clip · ${clip.name}`);
  return { name: clip.name, duration: clip.duration, loop };
}

function applyPlan(plan) {
  const previousState = currentPlan.state;
  const previousAction = currentPlan.action;
  const previousIntent = currentPlan.intent;
  currentPlan = { ...currentPlan, ...plan };
  if (previousState !== currentPlan.state) {
    manualPoseMode = false;
    manualBoneRotations.clear();
    manualBonePositions.clear();
    restoreRestPose();
    elapsed = 0;
    nextNod = 2.3 + Math.random() * 2.7;
  }
  if (previousAction !== currentPlan.action || previousIntent !== currentPlan.intent) crossFadeForPlan(currentPlan);
  if (vrm?.lookAt) {
    const [gx, gy] = currentPlan.gaze || [0, 0];
    vrm.lookAt.target = gazeTarget;
    gazeTarget.position.set(gx * 0.8, rootRestY + 1.45 - gy * 0.5, 2.5);
  }
  setExpression('happy', currentPlan.expression === 'happy' ? 0.38 : 0);
  setExpression('sad', currentPlan.expression === 'sad' ? 0.28 : 0);
  applySpeechPose();
  showStatus(avatarDisplayName, `${currentPlan.state} · acción ${currentPlan.action || 'idle'}${gltfAnimations.length ? ` · ${gltfAnimations.length} clips` : ' · movimiento procedural'}`);
}

function applySpeechPose() {
  const speaking = currentPlan.state === 'speaking';
  const shape = speaking ? (currentPlan.viseme?.shape || 'silence') : 'silence';
  const visemeValues = VISEMES[shape] || {};
  const energy = speaking ? Math.max(0, Math.min(1, currentPlan.speech?.energy || 0)) : 0;
  for (const name of ['aa', 'ih', 'ou', 'ee', 'oh']) {
    setExpression(name, Math.max(visemeValues[name] || 0, name === 'aa' ? energy * 0.65 : 0));
  }
  if (speaking) addBoneRotation('jaw', 0.03 + energy * 0.17, 0, 0);
}

function loadModel(url, displayName = '') {
  if (!url) return Promise.reject(new Error('Falta la ruta local del VRM.'));
  modelLoadState = 'loading';
  modelLoadError = '';
  loaderOverlay.hidden = false;
  showStatus(displayName || 'Avatar VRM', 'Cargando modelo local…');
  return new Promise((resolve, reject) => {
    gltfLoader.load(url, (gltf) => {
      if (avatarRoot) {
        scene.remove(avatarRoot);
        VRMUtils.deepDispose(avatarRoot);
      }
      sourceGltf = gltf;
      vrm = gltf.userData.vrm;
      avatarRoot = vrm?.scene || gltf.scene;
      avatarDisplayName = vrm?.meta?.name || displayName || avatarRoot.name || 'Avatar GLB';
      if (vrm) {
        VRMUtils.removeUnnecessaryVertices(gltf.scene);
        VRMUtils.removeUnnecessaryJoints(gltf.scene);
        // VRM's front convention faces away from the viewer camera in this scene.
        avatarRoot.rotation.y = Math.PI;
      }
      scene.add(avatarRoot);
      gltfAnimations = gltf.animations || [];
      mixer = new THREE.AnimationMixer(avatarRoot);
      activeAction = null;
      proceduralOffsets.clear();
      let meshCount = 0;
      avatarRoot.traverse((node) => { if (node.isMesh && node.geometry?.attributes?.position) meshCount++; });
      if (!meshCount) {
        scene.remove(avatarRoot);
        avatarRoot = null;
        loaderOverlay.hidden = true;
        modelLoadState = 'error';
        modelLoadError = 'El archivo cargó, pero no contiene mallas 3D visibles.';
        return reject(new Error(modelLoadError));
      }
      const bounds = new THREE.Box3().setFromObject(avatarRoot);
      const rawHeight = Math.max(0.001, bounds.max.y - bounds.min.y);
      if (!Number.isFinite(rawHeight) || rawHeight <= 0.001) {
        scene.remove(avatarRoot);
        avatarRoot = null;
        loaderOverlay.hidden = true;
        modelLoadState = 'error';
        modelLoadError = 'La geometría no tiene dimensiones válidas para encuadrarla.';
        return reject(new Error(modelLoadError));
      }
      const scale = 1.8 / rawHeight;
      avatarRoot.scale.multiplyScalar(scale);
      const normalizedBounds = new THREE.Box3().setFromObject(avatarRoot);
      const center = normalizedBounds.getCenter(new THREE.Vector3());
      avatarRoot.position.x -= center.x;
      avatarRoot.position.z -= center.z;
      const height = Math.max(0.2, normalizedBounds.max.y - normalizedBounds.min.y);
      avatarRoot.position.y -= normalizedBounds.min.y;
      refreshBoneData();
      ensureGeneratedAnimations();
      rootRestY = avatarRoot.position.y;
      controls.target.set(0, height * 0.53, 0);
      camera.position.set(0, height * 0.78, height * 2.35);
      controls.update();
      loaderOverlay.hidden = true;
      currentPlan = { ...currentPlan, state: 'idle', action: 'idle', locomotion: 'stand' };
      crossFadeForPlan(currentPlan);
      applyPlan(currentPlan);
      modelLoadState = 'loaded';
      resolve({ name: avatarDisplayName, format: vrm ? 'vrm' : 'glb', animations: gltfAnimations.map((clip) => clip.name), height, meshes: meshCount });
    }, undefined, (error) => {
      loaderOverlay.hidden = true;
      modelLoadState = 'error';
      modelLoadError = String(error?.message || error);
      showStatus('Error al cargar modelo', modelLoadError);
      reject(error);
    });
  });
}

function proceduralMotion(dt) {
  if (!avatarRoot || activeAction || manualPoseMode) return;
  const state = currentPlan.state;
  if (state !== 'restless') {
    const settle = Math.min(1, dt * 2.5);
    avatarRoot.position.x += (0 - avatarRoot.position.x) * settle;
    avatarRoot.position.y += (rootRestY - avatarRoot.position.y) * settle;
  }
  const strength = Math.max(0, Math.min(1, currentPlan.intensity ?? 0.3));
  const breathe = Math.sin(elapsed * 1.12) * 0.025 * (0.35 + strength);
  addBoneRotation('spine', breathe, 0, 0);
  addBoneRotation('chest', breathe * 0.7, 0, 0);
  addBoneRotation('head', Math.sin(elapsed * 0.54 + 0.6) * 0.025, 0, Math.sin(elapsed * 0.38) * 0.02);
  if (state === 'listening') {
    if (elapsed > nextNod) { nodPhase = 0.0001; nextNod = elapsed + 2.7 + Math.random() * 3.6; }
    if (nodPhase > 0) {
      nodPhase += dt;
      if (nodPhase < 0.48) addBoneRotation('head', Math.sin(Math.PI * nodPhase / 0.48) * 0.075, 0, 0);
      else nodPhase = 0;
    }
    addBoneRotation('chest', -0.025, 0, Math.sin(elapsed * 0.52) * 0.014);
  } else if (state === 'thinking' || state === 'waiting') {
    const look = Math.sin(elapsed * 0.31);
    addBoneRotation('head', -0.11, look * 0.055, Math.sin(elapsed * 0.24) * 0.08);
    addBoneRotation('neck', 0, look * 0.04, 0);
    addBoneRotation('chest', 0.025, 0, Math.sin(elapsed * 0.42) * 0.025);
    addBoneRotation('rightUpperArm', -0.12, 0, 0.08);
    addBoneRotation('rightLowerArm', -0.18 + Math.sin(elapsed * 0.45) * 0.04, 0, 0);
  } else if (state === 'speaking') {
    const energy = currentPlan.speech?.energy ?? 0.28;
    addBoneRotation('head', Math.max(0, Math.sin(elapsed * 2.7)) * 0.09, Math.sin(elapsed * 1.1) * 0.04, 0);
    addBoneRotation('chest', Math.sin(elapsed * 1.8) * 0.025, 0, 0);
    const gesture = Math.sin(elapsed * 1.25) * (0.12 + energy * 0.24);
    addBoneRotation('leftUpperArm', gesture * 0.35, 0, gesture);
    addBoneRotation('leftLowerArm', -Math.max(0, gesture) * 0.25, 0, 0);
    addBoneRotation('rightUpperArm', -gesture * 0.25, 0, -gesture * 0.45);
    addBoneRotation('jaw', 0.08 + Math.max(energy, 0.18) * 0.18, 0, 0);
  } else if (state === 'restless') {
    gaitPhase += dt * (Math.PI * 2) * (1.1 + strength * 0.35);
    const stride = Math.sin(gaitPhase);
    const lift = Math.max(0, Math.cos(gaitPhase * 2));
    addBoneRotation('leftUpperLeg', stride * 0.2, 0, 0);
    addBoneRotation('rightUpperLeg', -stride * 0.2, 0, 0);
    addBoneRotation('leftLowerLeg', lift * 0.36, 0, 0);
    addBoneRotation('rightLowerLeg', Math.max(0, -Math.cos(gaitPhase * 2)) * 0.36, 0, 0);
    addBoneRotation('leftUpperArm', -stride * 0.1, 0, 0);
    addBoneRotation('rightUpperArm', stride * 0.1, 0, 0);
    avatarRoot.position.x = Math.sin(elapsed * 0.45) * 0.35;
    avatarRoot.position.y = rootRestY + Math.max(0, Math.sin(gaitPhase * 2)) * 0.012;
  } else {
    avatarRoot.position.x *= Math.max(0, 1 - dt * 2.5);
    avatarRoot.position.y = rootRestY;
  }
}

function animate(now) {
  requestAnimationFrame(animate);
  const dt = Math.min(0.05, Math.max(0, (now - lastTime) / 1000));
  lastTime = now;
  elapsed += dt;
  if (avatarRoot) {
    clearProceduralOffsets();
    mixer?.update(dt);
    proceduralMotion(dt);
    if (elapsed > nextBlink && blinkRemaining <= 0) {
      blinkRemaining = 0.16;
      nextBlink = elapsed + 2.4 + Math.random() * 3.5;
    }
    let blink = 0;
    if (blinkRemaining > 0) {
      blinkRemaining -= dt;
      blink = Math.max(0, Math.sin(Math.PI * (1 - blinkRemaining / 0.16)));
    }
    setExpression('blink', blink);
    applySpeechPose();
    vrm?.update(dt);
    if (handDiagnosticGroup?.visible) {
      avatarRoot.updateMatrixWorld(true);
      for (const marker of handDiagnosticGroup.children) marker.userData.bone?.getWorldPosition(marker.position);
    }
    if (handWeightDiagnostic?.visible) updateHandWeightDiagnostic();
    if (handPaintSelectionGroup?.visible) updateHandPaintSelectionOverlay();
  }
  controls.update();
  if (selectedBoneMarker?.visible) {
    const selectedBone = findBone(selectedBoneName);
    if (selectedBone) selectedBone.getWorldPosition(selectedBoneMarker.position);
    else selectedBoneMarker.visible = false;
  }
  renderer.render(scene, camera);
}
requestAnimationFrame(animate);

async function createGeometricRig(forwardAxis = '+Z') {
  if (!avatarRoot || vrm) throw new Error('El auto-rig geométrico acepta GLB genéricos, no VRM ya riggeados.');
  const meshes = [];
  avatarRoot.updateMatrixWorld(true);
  avatarRoot.traverse((node) => { if (node.isMesh && !node.isSkinnedMesh && node.geometry?.attributes?.position) meshes.push(node); });
  if (!meshes.length) throw new Error('No encuentro mallas estáticas que pueda riggear.');
  const vertexCount = meshes.reduce((sum, mesh) => sum + mesh.geometry.attributes.position.count, 0);
  if (vertexCount > 1500000) throw new Error(`El modelo tiene ${vertexCount.toLocaleString()} vértices. Para evitar quedarnos sin memoria, reduce la malla por debajo de 1.5 millones de vértices y vuelve a importar.`);
  const bounds = new THREE.Box3().setFromObject(avatarRoot);
  const height = bounds.max.y - bounds.min.y;
  if (height < 0.15) throw new Error('El modelo es demasiado plano o pequeño para inferir un esqueleto humanoide.');
  const cx = (bounds.min.x + bounds.max.x) / 2, cz = (bounds.min.z + bounds.max.z) / 2;
  const forward = ({ '+Z': [0, 1], '-Z': [0, -1], '+X': [1, 0], '-X': [-1, 0] })[forwardAxis] || [0, 1];
  const side = [forward[1], -forward[0]];
  const width = Math.abs(side[0]) * (bounds.max.x - bounds.min.x) + Math.abs(side[1]) * (bounds.max.z - bounds.min.z);
  const profileBins = 64;
  const profileSamples = Array.from({ length: profileBins }, () => []);
  const depthSamples = Array.from({ length: profileBins }, () => []);
  const sampleStride = Math.max(1, Math.ceil(vertexCount / 100000));
  const sampledPositions = new Float32Array(Math.ceil(vertexCount / sampleStride) * 3);
  const sampleVertex = new THREE.Vector3();
  let pointSampleCount = 0;
  let sampled = 0;
  for (const mesh of meshes) {
    const positions = mesh.geometry.attributes.position;
    for (let i = 0; i < positions.count; i += sampleStride) {
      sampleVertex.fromBufferAttribute(positions, i).applyMatrix4(mesh.matrixWorld);
      sampledPositions[pointSampleCount * 3] = sampleVertex.x;
      sampledPositions[pointSampleCount * 3 + 1] = sampleVertex.y;
      sampledPositions[pointSampleCount * 3 + 2] = sampleVertex.z;
      pointSampleCount++;
      const ratio = (sampleVertex.y - bounds.min.y) / height;
      if (ratio >= 0 && ratio <= 1) {
        const bin = Math.min(profileBins - 1, Math.floor(ratio * profileBins));
        profileSamples[bin].push(sampleVertex.x * side[0] + sampleVertex.z * side[1]);
        depthSamples[bin].push(sampleVertex.x * forward[0] + sampleVertex.z * forward[1]);
      }
      sampled++;
      if (sampled % 10000 === 0) {
        const pct = Math.round(sampled / Math.ceil(vertexCount / sampleStride) * 100);
        showStatus(avatarDisplayName, `Analizando proporciones del cuerpo · ${pct}%`);
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    }
  }
  const profile = profileSamples.map((values, index) => {
    if (values.length < 8) return { y: (index + 0.5) / profileBins, width: 0, center: null, depth: null };
    values.sort((a, b) => a - b);
    const quantile = (q) => values[Math.min(values.length - 1, Math.floor((values.length - 1) * q))];
    const depths = depthSamples[index];
    depths.sort((a, b) => a - b);
    return {
      y: (index + 0.5) / profileBins,
      width: quantile(.95) - quantile(.05),
      center: quantile(.5),
      depth: depths[Math.floor((depths.length - 1) * .5)],
    };
  });
  const headProfile = profile.filter((slice) => slice.width > 0 && slice.y >= .78 && slice.y <= .99);
  const headPeak = headProfile.reduce((best, slice) => !best || slice.width > best.width ? slice : best, null);
  const neckCandidates = profile.filter((slice) => slice.width > 0 && slice.y >= .48 && slice.y < (headPeak?.y ?? .9) - .04);
  const localNecks = neckCandidates.filter((slice) => {
    const index = profile.indexOf(slice), above = profile[index + 1], below = profile[index - 1];
    return above?.width > 0 && below?.width > 0 && slice.width <= above.width && slice.width <= below.width
      && headPeak && headPeak.width > slice.width * 1.32 && headPeak.y - slice.y > .07;
  }).sort((a, b) => b.y - a.y);
  const neckSlice = localNecks[0] || neckCandidates
    .filter((slice) => slice.y >= Math.max(.48, (headPeak?.y ?? .9) - .30))
    .reduce((best, slice) => !best || slice.width < best.width ? slice : best, null);
  const hasChibiHead = Boolean(headPeak && neckSlice && headPeak.width > neckSlice.width * 1.32 && headPeak.y - neckSlice.y > .07);
  const neckRatio = hasChibiHead ? neckSlice.y : .82;
  const shoulderPriorRatio = Math.max(.48, Math.min(.82, neckRatio - .035));
  const shoulderSearch = profile.filter((slice) => slice.width > 0 && slice.y >= .42 && slice.y <= .60);
  const shoulderPeak = shoulderSearch.reduce((best, slice) => !best || slice.width > best.width ? slice : best, null);
  const shoulderRatio = neckRatio < .70 && shoulderPeak ? shoulderPeak.y : shoulderPriorRatio;
  const hipRatio = Math.max(.27, shoulderRatio * .64);
  const spineRatio = hipRatio + (shoulderRatio - hipRatio) * .45;
  const chestRatio = hipRatio + (shoulderRatio - hipRatio) * .82;
  const headRatio = neckRatio + (1 - neckRatio) * .5;
  const jawRatio = neckRatio + (headRatio - neckRatio) * .20;
  // Use the neck-based slice for torso width. In a T-pose the widest slice is
  // often the arms, which would otherwise make the shoulder half-width too big.
  const shoulderSlice = profile.reduce((best, slice) => slice.width > 0 && Math.abs(slice.y - shoulderPriorRatio) < Math.abs((best?.y ?? Infinity) - shoulderPriorRatio) ? slice : best, null);
  const armsExtended = Boolean(shoulderPeak && shoulderSlice && shoulderPeak.width > shoulderSlice.width * 1.15);
  const centerSlices = profile.filter((slice) => slice.center !== null && slice.y >= hipRatio && slice.y <= shoulderRatio + .02);
  const bodyCenter = centerSlices.length ? centerSlices.reduce((sum, slice) => sum + slice.center, 0) / centerSlices.length : cx * side[0] + cz * side[1];
  const centerOffset = bodyCenter - (cx * side[0] + cz * side[1]);
  const depthCenter = (heightRatio) => {
    const position = Math.max(0, Math.min(profileBins - 1, heightRatio * profileBins - .5));
    const lower = Math.floor(position), upper = Math.min(profileBins - 1, lower + 1);
    const valueAt = (index) => {
      if (profile[index].depth !== null) return profile[index].depth;
      for (let distance = 1; distance < profileBins; distance++) {
        const before = profile[index - distance], after = profile[index + distance];
        if (before?.depth !== null && before?.depth !== undefined) return before.depth;
        if (after?.depth !== null && after?.depth !== undefined) return after.depth;
      }
      return cx * forward[0] + cz * forward[1];
    };
    const lowDepth = valueAt(lower), highDepth = valueAt(upper);
    return THREE.MathUtils.lerp(lowDepth, highDepth, position - lower) - (cx * forward[0] + cz * forward[1]);
  };
  const point = (x, y, z) => avatarRoot.worldToLocal(new THREE.Vector3(x, y, z));
  const bodyPoint = (lateral, heightRatio, depth = 0) => {
    const sagittalDepth = depthCenter(heightRatio) + depth;
    return point(
      cx + side[0] * (centerOffset + lateral) + forward[0] * sagittalDepth,
      bounds.min.y + height * heightRatio,
      cz + side[1] * (centerOffset + lateral) + forward[1] * sagittalDepth,
    );
  };
  const half = Math.max(height * .065, Math.min((shoulderSlice?.width || width * .35) * .43, height * .18));
  const handRatio = hipRatio + (shoulderRatio - hipRatio) * .12;
  const kneeRatio = hipRatio * .52;
  const worldShoulder = (sign) => avatarRoot.localToWorld(bodyPoint(sign * half * .45, shoulderRatio).clone());
  const forwardVector = new THREE.Vector3(forward[0], 0, forward[1]).normalize();
  const sideVector = new THREE.Vector3(side[0], 0, side[1]).normalize();
  const bodyCenterX = cx + side[0] * centerOffset, bodyCenterZ = cz + side[1] * centerOffset;
  const inferHand = (sideName, sign) => {
    const shoulder = worldShoulder(sign);
    const candidates = [];
    const armBand = armsExtended ? .025 : .14;
    const minY = armsExtended
      ? bounds.min.y + height * Math.max(.08, shoulderRatio - armBand)
      : bounds.min.y + height * Math.max(.08, hipRatio - .06);
    // Include arms extended at shoulder level and modestly raised; the old
    // neck-clamped ceiling excluded T-pose wrists on chibi characters.
    const maxY = bounds.min.y + height * (armsExtended ? Math.min(.78, shoulderRatio + armBand) : Math.min(.78, shoulderRatio + .14));
    for (let i = 0; i < pointSampleCount; i++) {
      const x = sampledPositions[i * 3], y = sampledPositions[i * 3 + 1], z = sampledPositions[i * 3 + 2];
      if (y < minY || y > maxY) continue;
      const lateral = ((x - bodyCenterX) * side[0] + (z - bodyCenterZ) * side[1]) * sign;
      if (lateral < half * .58) continue;
      const dx = x - shoulder.x, dy = y - shoulder.y, dz = z - shoulder.z;
      candidates.push({ x, y, z, lateral, distance: dx * dx + dy * dy + dz * dz });
    }
    if (candidates.length < 12) return { position: bodyPoint(sign * half * 1.9, handRatio), direction: new THREE.Vector3(0, -1, 0), across: sideVector.clone().multiplyScalar(sign), length: height * .055, width: height * .035, inferred: false, pose: 'fallback' };
    const rank = armsExtended ? 'lateral' : 'distance';
    candidates.sort((a, b) => b[rank] - a[rank]);
    const cutoff = candidates[Math.min(candidates.length - 1, Math.floor(candidates.length * .965))][rank];
    const distal = candidates.filter((sample) => sample[rank] >= cutoff);
    const endpointWorld = distal.reduce((sum, sample) => sum.add(new THREE.Vector3(sample.x, sample.y, sample.z)), new THREE.Vector3()).multiplyScalar(1 / distal.length);
    const direction = endpointWorld.clone().sub(shoulder).normalize();
    const wristWorld = endpointWorld.clone().addScaledVector(direction, -height * .035);
    const wrist = avatarRoot.worldToLocal(wristWorld.clone());
    let across = new THREE.Vector3().crossVectors(forwardVector, direction).normalize();
    if (across.lengthSq() < .5) across.copy(sideVector);
    if (across.dot(sideVector) * sign < 0) across.negate();
    const local = [];
    for (let i = 0; i < pointSampleCount; i++) {
      const point = new THREE.Vector3(sampledPositions[i * 3], sampledPositions[i * 3 + 1], sampledPositions[i * 3 + 2]);
      const relative = point.sub(wristWorld);
      if (relative.lengthSq() <= (height * .11) ** 2) local.push({ along: relative.dot(direction), across: relative.dot(across) });
    }
    const quantile = (values, q, fallback) => {
      if (!values.length) return fallback;
      values.sort((a, b) => a - b);
      return values[Math.floor((values.length - 1) * q)];
    };
    const positiveAlong = local.map((sample) => sample.along).filter((value) => value >= 0 && value <= height * .12);
    const acrossValues = local.map((sample) => sample.across);
    const inferredLength = quantile(positiveAlong, .9, height * .055);
    const inferredWidth = quantile(acrossValues, .95, height * .02) - quantile(acrossValues, .05, -height * .02);
    return {
      position: wrist,
      direction,
      across,
      length: Math.max(height * .025, Math.min(height * .075, inferredLength)),
      width: Math.max(height * .018, Math.min(height * .075, inferredWidth)),
      inferred: true,
      sampleCount: distal.length,
      side: sideName,
      pose: armsExtended ? 'extended' : 'relaxed',
    };
  };
  const hands = { left: inferHand('left', -1), right: inferHand('right', 1) };
  const armPoint = (sign, fraction) => {
    const shoulder = worldShoulder(sign);
    const wrist = avatarRoot.localToWorld(hands[sign < 0 ? 'left' : 'right'].position.clone());
    return avatarRoot.worldToLocal(shoulder.lerp(wrist, fraction));
  };
  const joints = [
    ['hips', bodyPoint(0,hipRatio)], ['spine', bodyPoint(0,spineRatio)], ['chest', bodyPoint(0,chestRatio)], ['neck', bodyPoint(0,neckRatio)], ['head', bodyPoint(0,headRatio)], ['jaw', bodyPoint(0,jawRatio,height*.035)],
    ['leftShoulder', bodyPoint(-half*.45,shoulderRatio)], ['leftUpperArm', armPoint(-1,.32)], ['leftLowerArm', armPoint(-1,.72)], ['leftHand', hands.left.position],
    ['rightShoulder', bodyPoint(half*.45,shoulderRatio)], ['rightUpperArm', armPoint(1,.32)], ['rightLowerArm', armPoint(1,.72)], ['rightHand', hands.right.position],
    ['leftUpperLeg', bodyPoint(-half*.38,hipRatio-.015)], ['leftLowerLeg', bodyPoint(-half*.4,kneeRatio)], ['leftFoot', bodyPoint(-half*.4,.045,height*.025)],
    ['rightUpperLeg', bodyPoint(half*.38,hipRatio-.015)], ['rightLowerLeg', bodyPoint(half*.4,kneeRatio)], ['rightFoot', bodyPoint(half*.4,.045,height*.025)],
  ];
  const parents = { spine:'hips', chest:'spine', neck:'chest', head:'neck', jaw:'head', leftShoulder:'chest', leftUpperArm:'leftShoulder', leftLowerArm:'leftUpperArm', leftHand:'leftLowerArm', rightShoulder:'chest', rightUpperArm:'rightShoulder', rightLowerArm:'rightUpperArm', rightHand:'rightLowerArm', leftUpperLeg:'hips', leftLowerLeg:'leftUpperLeg', leftFoot:'leftLowerLeg', rightUpperLeg:'hips', rightLowerLeg:'rightUpperLeg', rightFoot:'rightLowerLeg' };
  for (const [sideName, sign] of [['left', -1], ['right', 1]]) {
    const hand = hands[sideName];
    const wristWorld = avatarRoot.localToWorld(hand.position.clone());
    const dir = hand.direction.clone();
    const across = hand.across.clone();
    const fingerLayout = [['Index', .24], ['Middle', .06], ['Ring', -.14], ['Little', -.34]];
    for (const [finger, spread] of fingerLayout) {
      const prefix = sideName + finger;
      const offset = hand.width * spread;
      const base = avatarRoot.worldToLocal(wristWorld.clone().addScaledVector(dir, hand.length * .12).addScaledVector(across, offset));
      const middle = avatarRoot.worldToLocal(wristWorld.clone().addScaledVector(dir, hand.length * .56).addScaledVector(across, offset * 1.12));
      const tip = avatarRoot.worldToLocal(wristWorld.clone().addScaledVector(dir, hand.length).addScaledVector(across, offset * 1.22));
      joints.push([prefix + 'Proximal', base], [prefix + 'Intermediate', middle], [prefix + 'Distal', tip]);
      parents[prefix + 'Proximal'] = sideName + 'Hand';
      parents[prefix + 'Intermediate'] = prefix + 'Proximal';
      parents[prefix + 'Distal'] = prefix + 'Intermediate';
    }
    const thumbBase = avatarRoot.worldToLocal(wristWorld.clone().addScaledVector(dir, hand.length * .08).addScaledVector(across, hand.width * .5));
    const thumbMiddle = avatarRoot.worldToLocal(wristWorld.clone().addScaledVector(dir, hand.length * .25).addScaledVector(across, hand.width * .82));
    const thumbTip = avatarRoot.worldToLocal(wristWorld.clone().addScaledVector(dir, hand.length * .42).addScaledVector(across, hand.width * .94));
    joints.push([sideName + 'ThumbProximal', thumbBase], [sideName + 'ThumbIntermediate', thumbMiddle], [sideName + 'ThumbDistal', thumbTip]);
    parents[sideName + 'ThumbProximal'] = sideName + 'Hand';
    parents[sideName + 'ThumbIntermediate'] = sideName + 'ThumbProximal';
    parents[sideName + 'ThumbDistal'] = sideName + 'ThumbIntermediate';
    joints.push([sideName + 'ToeBase', bodyPoint(sign * half * 0.4, .035, height * .075)], [sideName + 'ToeEnd', bodyPoint(sign * half * 0.4, .03, height * .14)]);
    parents[sideName + 'ToeBase'] = sideName + 'Foot';
    parents[sideName + 'ToeEnd'] = sideName + 'ToeBase';
  }
  const byName = new Map(joints.map(([name, pos]) => [name, { name, pos, bone: new THREE.Bone() }]));
  for (const [name, item] of byName) {
    item.bone.name = name;
    const parent = byName.get(parents[name]);
    (parent ? parent.bone : avatarRoot).add(item.bone);
    item.bone.position.copy(parent ? item.pos.clone().sub(parent.pos) : item.pos);
  }
  avatarRoot.updateMatrixWorld(true);
  const segments = Object.entries(parents).map(([childName, parentName]) => ({
    a: byName.get(parentName).pos,
    b: byName.get(childName).pos,
    parentName,
    childName,
    // Skinning indices address the bone at the start of this segment.
    index: [...byName.keys()].indexOf(parentName),
  }));
  for (const [segmentIndex, segment] of segments.entries()) {
    segment.neighborIndices = segments.flatMap((candidate, candidateIndex) =>
      candidateIndex === segmentIndex
      || candidate.parentName === segment.parentName
      || candidate.parentName === segment.childName
      || candidate.childName === segment.parentName
      || candidate.childName === segment.childName
        ? [candidateIndex]
        : [],
    );
  }
  for (const segment of segments) {
    segment.dx = segment.b.x - segment.a.x;
    segment.dy = segment.b.y - segment.a.y;
    segment.dz = segment.b.z - segment.a.z;
    segment.lengthSq = Math.max(1e-10, segment.dx ** 2 + segment.dy ** 2 + segment.dz ** 2);
  }
  autoRigBones = [...byName.values()].map((item) => item.bone);
  let completedVertices = 0;
  let lastReportedPercent = -5;
  for (const [meshIndex, mesh] of meshes.entries()) {
    const geo = mesh.geometry.clone();
    const pos = geo.attributes.position;
    const indices = new Uint16Array(pos.count * 4), weights = new Float32Array(pos.count * 4);
    const meshToRoot = avatarRoot.matrixWorld.clone().invert().multiply(mesh.matrixWorld);
    const vertex = new THREE.Vector3();
    const segmentDistances = new Float64Array(segments.length);
    const batchSize = 6000;
    for (let start = 0; start < pos.count; start += batchSize) {
      const end = Math.min(pos.count, start + batchSize);
      for (let i = start; i < end; i++) {
        vertex.fromBufferAttribute(pos, i).applyMatrix4(meshToRoot);
        let nearestSegment = null, nearestSegmentDistance = Infinity;
        for (let segmentIndex = 0; segmentIndex < segments.length; segmentIndex++) {
          const seg = segments[segmentIndex];
          const vx = vertex.x - seg.a.x, vy = vertex.y - seg.a.y, vz = vertex.z - seg.a.z;
          const t = Math.max(0, Math.min(1, (vx * seg.dx + vy * seg.dy + vz * seg.dz) / seg.lengthSq));
          const dx = vertex.x - (seg.a.x + t * seg.dx), dy = vertex.y - (seg.a.y + t * seg.dy), dz = vertex.z - (seg.a.z + t * seg.dz);
          const distance = dx * dx + dy * dy + dz * dz;
          segmentDistances[segmentIndex] = distance;
          if (distance < nearestSegmentDistance) {
            nearestSegmentDistance = distance;
            nearestSegment = seg;
          }
        }
        const nearestSegments = [...nearestSegment.neighborIndices]
          .sort((a, b) => segmentDistances[a] - segmentDistances[b])
          .slice(0, 4);
        let total = 0;
        for (let j = 0; j < 4; j++) {
          const segmentIndex = nearestSegments[j];
          const distance = segmentIndex === undefined ? Infinity : segmentDistances[segmentIndex];
          total += 1 / Math.max(1e-7, distance);
        }
        for (let j = 0; j < 4; j++) {
          const segmentIndex = nearestSegments[j];
          const segment = segmentIndex === undefined ? null : segments[segmentIndex];
          const distance = segment ? segmentDistances[segmentIndex] : Infinity;
          indices[i * 4 + j] = segment?.index ?? nearestSegment.index;
          weights[i * 4 + j] = segment ? (1 / Math.max(1e-7, distance)) / total : 0;
        }
      }
      completedVertices += end - start;
      if (completedVertices % (batchSize * 4) === 0 || completedVertices === vertexCount) {
        const percent = Math.round(completedVertices / vertexCount * 100);
        const message = `Calculando pesos · ${percent}% · malla ${meshIndex + 1}/${meshes.length}`;
        showStatus(avatarDisplayName, message);
        if (percent >= lastReportedPercent + 5 || completedVertices === vertexCount) {
          await callHost('reportProgress', message);
          lastReportedPercent = percent;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    geo.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(indices, 4));
    geo.setAttribute('skinWeight', new THREE.Float32BufferAttribute(weights, 4));
    const skinned = new THREE.SkinnedMesh(geo, mesh.material);
    skinned.name = mesh.name; skinned.position.copy(mesh.position); skinned.quaternion.copy(mesh.quaternion); skinned.scale.copy(mesh.scale); skinned.matrix.copy(mesh.matrix); skinned.matrixAutoUpdate = mesh.matrixAutoUpdate;
    skinned.castShadow = mesh.castShadow; skinned.receiveShadow = mesh.receiveShadow; skinned.morphTargetDictionary = mesh.morphTargetDictionary; skinned.morphTargetInfluences = mesh.morphTargetInfluences;
    const parent = mesh.parent; const at = parent.children.indexOf(mesh); parent.remove(mesh); parent.add(skinned); parent.children.splice(parent.children.indexOf(skinned), 1); parent.children.splice(at, 0, skinned);
    skinned.updateMatrixWorld(true);
    skinned.bind(new THREE.Skeleton(autoRigBones), skinned.matrixWorld);
  }
  avatarRoot.updateMatrixWorld(true);
  refreshBoneData();
  ensureGeneratedAnimations();
  crossFadeForPlan(currentPlan);
  return { bones: autoRigBones.length, meshes: meshes.length, generated_animations: GENERATED_ACTIONS.map((item) => item.name).filter((name) => gltfAnimations.some((clip) => clip.name === name)), forward_axis: forwardAxis, proportions: { neck: neckRatio, shoulders: shoulderRatio, hips: hipRatio, chibi_head_detected: hasChibiHead }, hands: { left: { inferred: hands.left.inferred, samples: hands.left.sampleCount || 0, pose: hands.left.pose }, right: { inferred: hands.right.inferred, samples: hands.right.sampleCount || 0, pose: hands.right.pose } }, confidence: 'low', warnings: ['Las influencias se limitan a segmentos óseos conectados para reducir el arrastre entre regiones; las mallas fusionadas aún requieren revisión y corrección manual en articulaciones. La posición y orientación de dedos se infieren localmente de la nube de vértices de cada mano.'] };
}

async function autoRig(forwardAxis = '+Z') {
  if (!hostBridge) throw new Error('El canal local de MeshingUp no está disponible.');
  if (operationBusy) throw new Error('Ya hay una operación 3D en curso.');
  operationBusy = true;
  try {
  await callHost('reportProgress', 'Analizando geometría y preparando pesos…');
  const report = await createGeometricRig(forwardAxis);
  showStatus(avatarDisplayName, 'Exportando GLB…');
  await transferGlb(report);
  showStatus(avatarDisplayName, `Auto-rig guardado · ${report.bones} huesos · confianza baja`);
  return report;
  } finally { operationBusy = false; }
}

async function saveEditedModel() {
  if (!hostBridge || !avatarRoot || !autoRigBones.length) throw new Error('Primero crea o carga un esqueleto.');
  if (operationBusy) throw new Error('Ya hay una operación 3D en curso.');
  operationBusy = true;
  try {
  const report = { method: 'meshingup_threejs_pose_edit', bones: autoRigBones.length, confidence: 'user_review',
    region_weight_mappings: handMappingReports,
    bone_pivot_edits: bonePivotEdits,
    warnings: ['GLB actualizado desde la pose editada en MeshingUp; revisa la deformación antes de producción.', ...(handMappingReports.length ? ['Los pesos incluyen propuestas SAM 2 locales aceptadas por la persona usuaria; revisa su correspondencia anatómica.'] : [])] };
  await transferGlb(report);
  showStatus(avatarDisplayName, 'Rig y pose editados guardados en el proyecto.');
  return report;
  } finally { operationBusy = false; }
}

window.meshingupAvatar = {
  loadModel,
  setMoveBoneSection(enabled) { finishBonePivotDrag(); moveBoneSection = Boolean(enabled); },
  autoRig(forwardAxis) { return autoRig(forwardAxis).catch((error) => { showStatus(avatarDisplayName, `Error de auto-rig: ${error.message}`); if (hostBridge) callHost('reportFailure', error.message).catch(() => {}); throw error; }); },
  saveEditedModel() { return saveEditedModel().catch((error) => { showStatus(avatarDisplayName, `Error al guardar: ${error.message}`); if (hostBridge) callHost('reportFailure', error.message).catch(() => {}); throw error; }); },
  setPlan: applyPlan,
  playClip(name, options = {}) { return playClipByName(name, options.loop !== false); },
  setSkeletonVisible(visible) { skeletonVisible = Boolean(visible); if (skeletonHelper) skeletonHelper.visible = skeletonVisible; },
  setHandDiagnostics,
  setJointDiagnostics: setHandDiagnostics,
  setBoneLandmarkDiagnostics,
  setHandWeightDiagnostics,
  setBoneWeightDiagnostics: setHandWeightDiagnostics,
  configureHandWeightPaint,
  clearHandPaintSelection,
  configureHandSegmentation,
  receiveHandSegmentation,
  applyHandSegmentationProposal,
  discardHandSegmentationProposal,
  undoEdit() { return editHistory.undo(); },
  redoEdit() { return editHistory.redo(); },
  getHistoryState() { return editHistory.state(); },
  // Older names kept for callers; both now step the single shared history.
  undoHandWeightPaint() { return editHistory.undo(); },
  assignHandPaintSelectionToBone,
  autoCorrectFingerWeights,
  autoCorrectSelectedBoneWeights,
  fitFingerSkeletonToWeights,
  centerSelectedBonePivotOnWeights,
  configureBonePivotDrag,
  configureBonePivotAxis,
  setViewAxis,
  centerSelectedBoneOnAxis,
  applySelectedBonePivotProposal,
  discardSelectedBonePivotProposal,
  undoFingerSkeletonFit() { return editHistory.undo(); },
  focusHand,
  focusBone,
  playHandTest,
  playSelectedBoneTest,
  playWristTest,
  setBoneRotation,
  resetBone,
  resetAllBones,
  selectBone,
  getBoneRotation(name) { return (manualBoneRotations.get(name) || [0, 0, 0]).map((value) => THREE.MathUtils.radToDeg(value)); },
  getBonePosition,
  setBonePosition,
  status(message) { detailLabel.textContent = String(message); },
  getInfo() { const bones = []; avatarRoot?.traverse((node) => { if (node.isBone) bones.push(node.name); }); let meshes = 0; avatarRoot?.traverse((node) => { if (node.isMesh && node.geometry?.attributes?.position) meshes++; }); return { loaded: Boolean(avatarRoot) && modelLoadState === 'loaded', loadState: modelLoadState, loadError: modelLoadError, format: vrm ? 'vrm' : 'glb', animations: gltfAnimations.map((clip) => clip.name), bones, meshes, name: avatarDisplayName }; },
};
window.dispatchEvent(new Event('meshingup-avatar-ready'));
