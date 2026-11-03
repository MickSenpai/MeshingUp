import * as THREE from 'three';

// Preserve each bone's skin transform when changing its rest position.
export function preserveBindTransforms(skeleton, oldWorlds, oldInverses) {
  skeleton.boneInverses = skeleton.bones.map((bone, i) => bone.matrixWorld.clone().invert()
    .multiply(oldWorlds[i]).multiply(oldInverses[i]));
  skeleton.update();
}

// Adjacent joints share influence, allowing each phalanx to bend independently.
export function fingerInfluences(point, joints) {
  const distances = joints.map(({ index, position }) => ({ index, distance: point.distanceToSquared(position) }))
    .sort((a, b) => a.distance - b.distance).slice(0, 2);
  const total = distances.reduce((sum, item) => sum + 1 / Math.max(item.distance, 1e-10), 0);
  return distances.map(({ index, distance }) => [index, (1 / Math.max(distance, 1e-10)) / total]);
}
