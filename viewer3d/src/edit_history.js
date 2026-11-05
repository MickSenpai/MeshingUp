// One linear undo/redo history across independent kinds of edits (vertex
// weights, bone pivots). Each kind keeps its own action objects and knows how
// to swap them; this module only remembers the order and delegates, so a single
// Undo/Redo pair always reverses the most recent edit whatever its kind.
//
// A handler for a kind provides:
//   undo(action) -> redoAction   apply the old state, return how to redo it
//   redo(action) -> undoAction   apply the new state, return how to undo it
export function createEditHistory({ onChange = () => {} } = {}) {
  const handlers = new Map();
  let undoStack = [];
  let redoStack = [];

  const state = () => ({ canUndo: undoStack.length > 0, canRedo: redoStack.length > 0 });
  const notify = () => onChange(state());

  return {
    register(kind, handler) { handlers.set(kind, handler); },

    // A new edit invalidates anything that could have been redone.
    record(kind, action) {
      undoStack.push({ kind, action });
      redoStack = [];
      notify();
    },

    // Most recent edit overall (for coalescing repeated tweaks of one control).
    peek(kind) {
      const last = undoStack[undoStack.length - 1];
      return last && (!kind || last.kind === kind) ? last.action : null;
    },

    undo() {
      const entry = undoStack.pop();
      if (!entry) return false;
      const handler = handlers.get(entry.kind);
      const inverse = handler.undo(entry.action);
      redoStack.push({ kind: entry.kind, action: inverse });
      notify();
      return true;
    },

    redo() {
      const entry = redoStack.pop();
      if (!entry) return false;
      const handler = handlers.get(entry.kind);
      const inverse = handler.redo(entry.action);
      undoStack.push({ kind: entry.kind, action: inverse });
      notify();
      return true;
    },

    // Forget one kind (for example bone edits after the rig is reset) or everything.
    clear(kind) {
      if (kind) {
        undoStack = undoStack.filter((entry) => entry.kind !== kind);
        redoStack = redoStack.filter((entry) => entry.kind !== kind);
      } else {
        undoStack = [];
        redoStack = [];
      }
      notify();
    },

    state,
  };
}
