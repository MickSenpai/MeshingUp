// Run with: node viewer3d/check_edit_history.mjs
import assert from 'node:assert/strict';
import { createEditHistory } from './src/edit_history.js';

// A fake document with two independent kinds of state, edited through swaps
// exactly like the viewer does (weights per vertex, bones per name).
const doc = { weights: 0, bones: 0 };
const seen = [];
const history = createEditHistory({ onChange: (s) => seen.push(s) });
const swap = (key) => ({
  undo: (a) => { const now = doc[key]; doc[key] = a.value; return { value: now }; },
  redo: (a) => { const now = doc[key]; doc[key] = a.value; return { value: now }; },
});
history.register('weights', swap('weights'));
history.register('bones', swap('bones'));
const edit = (kind, value) => { history.record(kind, { value: doc[kind] }); doc[kind] = value; };

// Interleaved edits come back in exactly the reverse order, across kinds.
edit('weights', 1); edit('bones', 5); edit('weights', 2);
assert.deepEqual(doc, { weights: 2, bones: 5 });
assert.equal(history.undo(), true); assert.deepEqual(doc, { weights: 1, bones: 5 });
assert.equal(history.undo(), true); assert.deepEqual(doc, { weights: 1, bones: 0 });
assert.equal(history.undo(), true); assert.deepEqual(doc, { weights: 0, bones: 0 });
assert.equal(history.undo(), false, 'nothing left to undo');
assert.deepEqual(history.state(), { canUndo: false, canRedo: true });

// Redo replays them forward in order, and can be undone again.
assert.equal(history.redo(), true); assert.deepEqual(doc, { weights: 1, bones: 0 });
assert.equal(history.redo(), true); assert.deepEqual(doc, { weights: 1, bones: 5 });
assert.equal(history.undo(), true); assert.deepEqual(doc, { weights: 1, bones: 0 });
assert.equal(history.redo(), true); assert.equal(history.redo(), true);
assert.deepEqual(doc, { weights: 2, bones: 5 });
assert.equal(history.redo(), false, 'nothing left to redo');

// A new edit after an undo throws the redo branch away.
history.undo(); assert.deepEqual(doc, { weights: 1, bones: 5 });
assert.equal(history.state().canRedo, true);
edit('bones', 9);
assert.equal(history.state().canRedo, false);
assert.equal(history.redo(), false);
history.undo(); assert.deepEqual(doc, { weights: 1, bones: 5 });

// peek sees only the most recent edit, optionally of one kind.
edit('weights', 7);
assert.equal(history.peek('bones'), null, 'last edit was not a bone edit');
assert.notEqual(history.peek('weights'), null);
assert.notEqual(history.peek(), null);

// clear(kind) drops one kind; the other keeps working in order.
edit('bones', 11);
history.clear('bones');
assert.equal(history.undo(), true); assert.deepEqual(doc, { weights: 1, bones: 11 });
history.clear();
assert.deepEqual(history.state(), { canUndo: false, canRedo: false });
assert.equal(history.undo(), false);

// Listeners were told about every change.
assert.ok(seen.length > 10 && seen.every((s) => typeof s.canUndo === 'boolean' && typeof s.canRedo === 'boolean'));
console.log(`edit history OK (${seen.length} change notifications)`);
