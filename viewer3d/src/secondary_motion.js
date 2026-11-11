import * as THREE from 'three';

// Bones that follow their parent with inertia instead of rigidly (hair, tails, ribbons…).
export const SECONDARY_BONE_PATTERN = /^(hair|ahoge|ponytail|pigtail|braid|tail|ribbon|skirt|cape|scarf|ear)/i;

const DEFAULTS = { stiffness: 0.9, drag: 0.25, gravity: 0.35, maxAngle: 0.6, length: 0.25 };

// Verlet springs in the style of VRM spring bones: each bone keeps a virtual
// tail point in world space that lags behind the bone and is pulled back
// towards its rest direction. Only bone rotations change; rest data is never
// modified, so `reset()` always returns the exact rest pose.
export function createSecondaryMotion(options = {}) {
  const settings = { ...DEFAULTS, ...options };
  let entries = [];
  const parentRotation = new THREE.Quaternion();
  const origin = new THREE.Vector3();
  const restDirection = new THREE.Vector3();
  const next = new THREE.Vector3();
  const local = new THREE.Vector3();
  const swing = new THREE.Quaternion();
  const scale = new THREE.Vector3();

  function restOf(entry) { return entry.rest(entry.bone) || entry.fallbackRest; }

  function measure(entry) {
    const { bone } = entry;
    bone.parent.updateWorldMatrix(true, false);
    bone.parent.matrixWorld.decompose(origin, parentRotation, scale);
    origin.copy(bone.position).applyMatrix4(bone.parent.matrixWorld);
    restDirection.copy(entry.axis).applyQuaternion(restOf(entry)).applyQuaternion(parentRotation).normalize();
  }

  return {
    get count() { return entries.length; },
    names() { return entries.map((entry) => entry.bone.name); },
    // `rest(bone)` returns the bone's current rest quaternion (edits included).
    bind(bones, rest = () => null) {
      entries = bones.filter((bone) => bone.parent && SECONDARY_BONE_PATTERN.test(bone.name)).map((bone) => {
        // Blender exports bones along local +Y; a child bone, when present, is more precise.
        const child = bone.children.find((node) => node.isBone);
        const axis = child && child.position.lengthSq() > 1e-12 ? child.position.clone().normalize() : new THREE.Vector3(0, 1, 0);
        const entry = { bone, axis, rest, fallbackRest: bone.quaternion.clone(), tail: new THREE.Vector3(), previous: new THREE.Vector3() };
        measure(entry);
        entry.tail.copy(origin).addScaledVector(restDirection, settings.length);
        entry.previous.copy(entry.tail);
        return entry;
      });
      return entries.length;
    },
    reset() {
      for (const entry of entries) {
        entry.bone.quaternion.copy(restOf(entry));
        measure(entry);
        entry.tail.copy(origin).addScaledVector(restDirection, settings.length);
        entry.previous.copy(entry.tail);
      }
    },
    update(dt) {
      if (!(dt > 0)) return;
      const step = Math.min(dt, 0.05);
      for (const entry of entries) {
        const { bone } = entry;
        measure(entry);
        next.copy(entry.tail).sub(entry.previous).multiplyScalar(Math.max(0, 1 - settings.drag))
          .add(entry.tail)
          .addScaledVector(restDirection, settings.stiffness * step);
        next.y -= settings.gravity * step;
        next.sub(origin);
        if (next.lengthSq() < 1e-12) next.copy(restDirection);
        next.normalize();
        // Keep the swing bounded so hair never folds through the head.
        const angle = restDirection.angleTo(next);
        if (angle > settings.maxAngle) {
          swing.setFromUnitVectors(restDirection, next);
          const limited = new THREE.Quaternion().slerp(swing, settings.maxAngle / angle);
          next.copy(restDirection).applyQuaternion(limited);
        }
        entry.previous.copy(entry.tail);
        entry.tail.copy(origin).addScaledVector(next, settings.length);
        const rest = restOf(entry);
        local.copy(next).applyQuaternion(parentRotation.clone().invert()).normalize();
        swing.setFromUnitVectors(entry.axis.clone().applyQuaternion(rest).normalize(), local);
        bone.quaternion.copy(swing).multiply(rest);
      }
    },
  };
}
