import assert from 'node:assert/strict';
import * as THREE from 'three';
import { preserveBindTransforms, fingerInfluences } from './src/hand_rig.js';

const root = new THREE.Bone(), middle = new THREE.Bone(), tip = new THREE.Bone();
root.add(middle); middle.add(tip);
middle.position.x = 1; tip.position.x = 1;
root.position.set(3, 2, -1); root.rotation.z = 0.4;
root.updateMatrixWorld(true);
const skeleton = new THREE.Skeleton([root, middle, tip]);
// Include an imported bind transform that is not the identity pose.
skeleton.boneInverses.forEach(matrix => matrix.multiply(new THREE.Matrix4().makeTranslation(0, 0.2, 0)));
const worlds = skeleton.bones.map(bone => bone.matrixWorld.clone());
const inverses = skeleton.boneInverses.map(matrix => matrix.clone());
const before = skeleton.bones.map((bone, i) => bone.matrixWorld.clone().multiply(inverses[i]));
middle.position.set(1.2, 0.2, 0); tip.position.x = 0.7;
root.updateMatrixWorld(true);
preserveBindTransforms(skeleton, worlds, inverses);
skeleton.bones.forEach((bone, i) => {
  const after = bone.matrixWorld.clone().multiply(skeleton.boneInverses[i]);
  assert(after.elements.every((value, j) => Math.abs(value - before[i].elements[j]) < 1e-9), 'Rest skin must remain unchanged');
});
const joints = skeleton.bones.map((bone, index) => ({index, position: bone.getWorldPosition(new THREE.Vector3())}));
const weights = fingerInfluences(joints[2].position, joints);
assert.equal(weights[0][0], 2);
assert(weights[0][1] > 0.99, 'Fingertip must follow distal bone');
assert(Math.abs(weights.reduce((sum, [, weight]) => sum + weight, 0) - 1) < 1e-9);
const probe = joints[2].position.clone().add(new THREE.Vector3(0.3, 0, 0));
const beforeBend = probe.clone().applyMatrix4(tip.matrixWorld.clone().multiply(skeleton.boneInverses[2]));
tip.rotation.z = 0.6; root.updateMatrixWorld(true);
const afterBend = probe.clone().applyMatrix4(tip.matrixWorld.clone().multiply(skeleton.boneInverses[2]));
assert(beforeBend.distanceTo(afterBend) > 0.01, 'Distal rotation must move influenced geometry');
console.log('PASS: rest bind preservation, normalized finger weights, distal mobility');
