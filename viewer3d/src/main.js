import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { VRMLoaderPlugin, VRMUtils, VRMHumanBoneName } from '@pixiv/three-vrm';
import './style.css';

const canvas = document.querySelector('#avatar');
const nameLabel = document.querySelector('#model-name');
const detailLabel = document.querySelector('#model-detail');
const loaderOverlay = document.querySelector('#loader');
const scene = new THREE.Scene();
scene.background = new THREE.Color('#17191d');
scene.fog = new THREE.Fog('#17191d', 7, 18);
const camera = new THREE.PerspectiveCamera(32, 1, 0.01, 100);
camera.position.set(0, 1.45, 4.6);
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
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

const gltfLoader = new GLTFLoader();
gltfLoader.register((parser) => new VRMLoaderPlugin(parser));
let vrm = null;
let avatarRoot = null;
let sourceGltf = null;
let autoRigBones = [];
let hostBridge = null;
let skeletonHelper = null;
let selectedBoneMarker = null;
let selectedBoneName = '';
let skeletonVisible = true;
let manualPoseMode = false;
let operationBusy = false;
const boneRestQuaternions = new Map();
const boneRestPositions = new Map();
const manualBoneRotations = new Map();
const manualBonePositions = new Map();
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
  new QWebChannel(window.qt.webChannelTransport, (channel) => { hostBridge = channel.objects.rigmHost || null; });
}
connectHost();

function callHost(method, ...args) {
  return new Promise((resolve, reject) => {
    if (!hostBridge || typeof hostBridge[method] !== 'function') return reject(new Error('Canal de RigM no disponible.'));
    const timeout = setTimeout(() => reject(new Error(`RigM no respondió a ${method}.`)), 60000);
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
  if (!transferId) throw new Error('RigM rechazó el archivo generado.');
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
    if (!saved) throw new Error('RigM no pudo guardar el GLB generado.');
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
      if (position) node.position.copy(position).add(new THREE.Vector3(...(manualBonePositions.get(node.name) || [0, 0, 0])));
    }
  }
  proceduralOffsets.clear();
}

function refreshBoneData() {
  boneRestQuaternions.clear();
  boneRestPositions.clear();
  autoRigBones = [];
  avatarRoot?.traverse((node) => { if (node.isBone) { boneRestQuaternions.set(node, node.quaternion.clone()); boneRestPositions.set(node, node.position.clone()); autoRigBones.push(node); } });
  if (skeletonHelper) scene.remove(skeletonHelper);
  skeletonHelper = avatarRoot ? new THREE.SkeletonHelper(avatarRoot) : null;
  if (skeletonHelper) {
    skeletonHelper.material = new THREE.LineBasicMaterial({ color: 0x70f0d0, depthTest: false, transparent: true, opacity: 0.95 });
    skeletonHelper.visible = skeletonVisible;
    skeletonHelper.renderOrder = 10;
    scene.add(skeletonHelper);
  }
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
  manualBonePositions.delete(name);
  const position = node && boneRestPositions.get(node);
  if (node && position) node.position.copy(position);
}
function resetAllBones() {
  manualBoneRotations.clear();
  manualBonePositions.clear();
  manualPoseMode = true;
  activeAction?.stop(); activeAction = null;
  restoreRestPose();
}
function selectBone(name) {
  selectedBoneName = String(name || '');
  if (selectedBoneMarker) selectedBoneMarker.visible = Boolean(findBone(selectedBoneName));
}
function getBonePosition(name) { return manualBonePositions.get(name) || [0, 0, 0]; }
function setBonePosition(name, offsets) {
  const node = findBone(name), rest = node && boneRestPositions.get(node);
  if (!node || !rest) return false;
  manualPoseMode = true;
  activeAction?.stop(); activeAction = null;
  const value = offsets.map((item) => Number(item) || 0);
  manualBonePositions.set(name, value);
  node.position.copy(rest).add(new THREE.Vector3(...value));
  return true;
}

function restoreRestPose() {
  for (const [node, rest] of boneRestQuaternions) {
    node.quaternion.copy(rest);
    const manual = manualBoneRotations.get(node.name);
    if (manual) node.quaternion.multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(...manual, 'XYZ')));
    node.position.copy(boneRestPositions.get(node)).add(new THREE.Vector3(...(manualBonePositions.get(node.name) || [0, 0, 0])));
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
  { action: 'idle', name: 'RigM · Idle', duration: 3.2 },
  { action: 'listen', name: 'RigM · Listening', duration: 2.8 },
  { action: 'think', name: 'RigM · Thinking', duration: 3.6 },
  { action: 'talk', name: 'RigM · Speaking', duration: 2.4 },
  { action: 'wait', name: 'RigM · Waiting', duration: 3.4 },
  { action: 'pace', name: 'RigM · Walk cycle', duration: 1.2 },
  { action: 'recover', name: 'RigM · Recover', duration: 2.6 },
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
    if (values.length < 8) return { y: (index + 0.5) / profileBins, width: 0, center: null };
    values.sort((a, b) => a - b);
    const quantile = (q) => values[Math.min(values.length - 1, Math.floor((values.length - 1) * q))];
    return { y: (index + 0.5) / profileBins, width: quantile(.95) - quantile(.05), center: quantile(.5) };
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
  const shoulderRatio = Math.max(.48, Math.min(.82, neckRatio - .035));
  const hipRatio = Math.max(.27, shoulderRatio * .64);
  const spineRatio = hipRatio + (shoulderRatio - hipRatio) * .45;
  const chestRatio = hipRatio + (shoulderRatio - hipRatio) * .82;
  const headRatio = neckRatio + (1 - neckRatio) * .5;
  const jawRatio = neckRatio + (headRatio - neckRatio) * .20;
  const shoulderSlice = profile.reduce((best, slice) => slice.width > 0 && Math.abs(slice.y - shoulderRatio) < Math.abs((best?.y ?? Infinity) - shoulderRatio) ? slice : best, null);
  const centerSlices = profile.filter((slice) => slice.center !== null && slice.y >= hipRatio && slice.y <= shoulderRatio + .02);
  const bodyCenter = centerSlices.length ? centerSlices.reduce((sum, slice) => sum + slice.center, 0) / centerSlices.length : cx * side[0] + cz * side[1];
  const centerOffset = bodyCenter - (cx * side[0] + cz * side[1]);
  const point = (x, y, z) => avatarRoot.worldToLocal(new THREE.Vector3(x, y, z));
  const bodyPoint = (lateral, heightRatio, depth = 0) => point(cx + side[0] * (centerOffset + lateral) + forward[0] * depth, bounds.min.y + height * heightRatio, cz + side[1] * (centerOffset + lateral) + forward[1] * depth);
  const half = Math.max(height * .065, Math.min((shoulderSlice?.width || width * .35) * .43, height * .18));
  const elbowRatio = shoulderRatio - (shoulderRatio - hipRatio) * .58;
  const handRatio = hipRatio + (shoulderRatio - hipRatio) * .12;
  const kneeRatio = hipRatio * .52;
  const worldShoulder = (sign) => avatarRoot.localToWorld(bodyPoint(sign * half * .45, shoulderRatio).clone());
  const worldElbow = (sign) => avatarRoot.localToWorld(bodyPoint(sign * half * 1.55, elbowRatio).clone());
  const forwardVector = new THREE.Vector3(forward[0], 0, forward[1]).normalize();
  const sideVector = new THREE.Vector3(side[0], 0, side[1]).normalize();
  const bodyCenterX = cx + side[0] * centerOffset, bodyCenterZ = cz + side[1] * centerOffset;
  const inferHand = (sideName, sign) => {
    const shoulder = worldShoulder(sign), elbow = worldElbow(sign);
    const candidates = [];
    const minY = bounds.min.y + height * Math.max(.08, hipRatio - .06);
    const maxY = bounds.min.y + height * Math.min(shoulderRatio + .025, neckRatio - .045);
    for (let i = 0; i < pointSampleCount; i++) {
      const x = sampledPositions[i * 3], y = sampledPositions[i * 3 + 1], z = sampledPositions[i * 3 + 2];
      if (y < minY || y > maxY) continue;
      const lateral = ((x - bodyCenterX) * side[0] + (z - bodyCenterZ) * side[1]) * sign;
      if (lateral < half * .58) continue;
      const dx = x - shoulder.x, dy = y - shoulder.y, dz = z - shoulder.z;
      candidates.push({ x, y, z, distance: dx * dx + dy * dy + dz * dz });
    }
    if (candidates.length < 12) return { position: bodyPoint(sign * half * 1.9, handRatio), direction: new THREE.Vector3(0, -1, 0), across: sideVector.clone().multiplyScalar(sign), length: height * .055, width: height * .035, inferred: false };
    candidates.sort((a, b) => b.distance - a.distance);
    const cutoff = candidates[Math.min(candidates.length - 1, Math.floor(candidates.length * .965))].distance;
    const distal = candidates.filter((sample) => sample.distance >= cutoff);
    const endpointWorld = distal.reduce((sum, sample) => sum.add(new THREE.Vector3(sample.x, sample.y, sample.z)), new THREE.Vector3()).multiplyScalar(1 / distal.length);
    const direction = endpointWorld.clone().sub(elbow).normalize();
    if (direction.lengthSq() < .5) direction.copy(endpointWorld).sub(shoulder).normalize();
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
    };
  };
  const hands = { left: inferHand('left', -1), right: inferHand('right', 1) };
  const joints = [
    ['hips', bodyPoint(0,hipRatio)], ['spine', bodyPoint(0,spineRatio)], ['chest', bodyPoint(0,chestRatio)], ['neck', bodyPoint(0,neckRatio)], ['head', bodyPoint(0,headRatio)], ['jaw', bodyPoint(0,jawRatio,height*.035)],
    ['leftShoulder', bodyPoint(-half*.45,shoulderRatio)], ['leftUpperArm', bodyPoint(-half,shoulderRatio-.025)], ['leftLowerArm', bodyPoint(-half*1.55,elbowRatio)], ['leftHand', hands.left.position],
    ['rightShoulder', bodyPoint(half*.45,shoulderRatio)], ['rightUpperArm', bodyPoint(half,shoulderRatio-.025)], ['rightLowerArm', bodyPoint(half*1.55,elbowRatio)], ['rightHand', hands.right.position],
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
  const segments = Object.entries(parents).map(([child, parent]) => ({ a: byName.get(parent).pos, b: byName.get(child).pos, index: [...byName.keys()].indexOf(child) }));
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
    const nearestIndex = new Int32Array(4), nearestDistance = new Float64Array(4);
    const batchSize = 6000;
    for (let start = 0; start < pos.count; start += batchSize) {
      const end = Math.min(pos.count, start + batchSize);
      for (let i = start; i < end; i++) {
        vertex.fromBufferAttribute(pos, i).applyMatrix4(meshToRoot);
        nearestIndex.fill(-1); nearestDistance.fill(Infinity);
        for (const seg of segments) {
          const vx = vertex.x - seg.a.x, vy = vertex.y - seg.a.y, vz = vertex.z - seg.a.z;
          const t = Math.max(0, Math.min(1, (vx * seg.dx + vy * seg.dy + vz * seg.dz) / seg.lengthSq));
          const dx = vertex.x - (seg.a.x + t * seg.dx), dy = vertex.y - (seg.a.y + t * seg.dy), dz = vertex.z - (seg.a.z + t * seg.dz);
          const distance = dx * dx + dy * dy + dz * dz;
          if (distance >= nearestDistance[3]) continue;
          let at = 3;
          while (at > 0 && distance < nearestDistance[at - 1]) {
            nearestDistance[at] = nearestDistance[at - 1]; nearestIndex[at] = nearestIndex[at - 1]; at--;
          }
          nearestDistance[at] = distance; nearestIndex[at] = seg.index;
        }
        let total = 0;
        for (let j = 0; j < 4; j++) total += 1 / Math.max(1e-7, nearestDistance[j]);
        for (let j = 0; j < 4; j++) { indices[i * 4 + j] = nearestIndex[j]; weights[i * 4 + j] = (1 / Math.max(1e-7, nearestDistance[j])) / total; }
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
  return { bones: autoRigBones.length, meshes: meshes.length, generated_animations: GENERATED_ACTIONS.map((item) => item.name).filter((name) => gltfAnimations.some((clip) => clip.name === name)), forward_axis: forwardAxis, proportions: { neck: neckRatio, shoulders: shoulderRatio, hips: hipRatio, chibi_head_detected: hasChibiHead }, hands: { left: { inferred: hands.left.inferred, samples: hands.left.sampleCount || 0 }, right: { inferred: hands.right.inferred, samples: hands.right.sampleCount || 0 } }, confidence: 'low', warnings: ['Rig y clips base generados localmente sin Blender; proporciones y pesos son aproximados. La posición y orientación de dedos se infieren localmente de la nube de vértices de cada mano; revisa ambos lados.'] };
}

async function autoRig(forwardAxis = '+Z') {
  if (!hostBridge) throw new Error('El canal local de RigM no está disponible.');
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
  const report = { method: 'rigm_threejs_pose_edit', bones: autoRigBones.length, confidence: 'user_review', warnings: ['GLB actualizado desde la pose editada en RigM; revisa la deformación antes de producción.'] };
  await transferGlb(report);
  showStatus(avatarDisplayName, 'Rig y pose editados guardados en el proyecto.');
  return report;
  } finally { operationBusy = false; }
}

window.rigmAvatar = {
  loadModel,
  autoRig(forwardAxis) { return autoRig(forwardAxis).catch((error) => { showStatus(avatarDisplayName, `Error de auto-rig: ${error.message}`); if (hostBridge) callHost('reportFailure', error.message).catch(() => {}); throw error; }); },
  saveEditedModel() { return saveEditedModel().catch((error) => { showStatus(avatarDisplayName, `Error al guardar: ${error.message}`); if (hostBridge) callHost('reportFailure', error.message).catch(() => {}); throw error; }); },
  setPlan: applyPlan,
  setSkeletonVisible(visible) { skeletonVisible = Boolean(visible); if (skeletonHelper) skeletonHelper.visible = skeletonVisible; },
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
window.dispatchEvent(new Event('rigm-avatar-ready'));
