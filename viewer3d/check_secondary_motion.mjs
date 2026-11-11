import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createSecondaryMotion, SECONDARY_BONE_PATTERN } from './src/secondary_motion.js';

const head = new THREE.Bone(); head.name = 'head';
const hair = new THREE.Bone(); hair.name = 'hairBack';
const jaw = new THREE.Bone(); jaw.name = 'jaw';
head.add(hair); head.add(jaw);
hair.position.set(0, 0.2, -0.2);
hair.quaternion.setFromEuler(new THREE.Euler(Math.PI, 0, 0)); // local +Y points down: hanging hair
head.updateMatrixWorld(true);
const rest = hair.quaternion.clone();
const motion = createSecondaryMotion();
assert.equal(motion.bind([head, hair, jaw]), 1, 'Only secondary bones are simulated');
assert.deepEqual(motion.names(), ['hairBack']);
assert(SECONDARY_BONE_PATTERN.test('hairLeft') && !SECONDARY_BONE_PATTERN.test('leftHand') && !SECONDARY_BONE_PATTERN.test('head'));

// At rest nothing moves.
for (let i = 0; i < 120; i++) motion.update(1 / 60);
assert(hair.quaternion.angleTo(rest) < 1e-3, 'Hanging hair stays at rest');

// A sudden head turn makes the hair lag, bounded by the swing limit, and it settles afterwards.
head.quaternion.setFromEuler(new THREE.Euler(0, 0, 0.6));
let peak = 0;
for (let i = 0; i < 20; i++) { motion.update(1 / 60); peak = Math.max(peak, hair.quaternion.angleTo(rest)); }
assert(peak > 0.05, 'Hair lags behind a fast head rotation');
assert(peak <= 0.6 + 1e-6, 'Swing stays within the limit');
for (let i = 0; i < 600; i++) motion.update(1 / 60);
const settled = hair.quaternion.angleTo(rest);
assert(settled < peak, 'Hair settles after the motion stops');
assert(settled > 0.01, 'Gravity keeps hair hanging while the head stays tilted');
assert(Number.isFinite(hair.quaternion.x) && Math.abs(hair.quaternion.length() - 1) < 1e-6);

// Reset returns the exact rest rotation; rest overrides (edited rigs) are honoured.
motion.reset();
assert(hair.quaternion.angleTo(rest) < 1e-6, 'Reset restores the rest pose');
const edited = new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI, 0, 0.2));
motion.bind([head, hair], (bone) => (bone === hair ? edited : null));
motion.reset();
assert(hair.quaternion.angleTo(edited) < 1e-6, 'Edited rest pose is used');
motion.update(0); motion.update(NaN);
console.log('PASS: secondary motion binds hair bones, lags, stays bounded, settles and resets');
