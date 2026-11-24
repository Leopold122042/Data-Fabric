// A grab only takes over after a small movement. A tap or Enter still activates
// the star. All three kinds of handle share the same capture and cancel rules.
const arrows = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};

export function bindAtlasDrag(atlas, selector, signal, resolve) {
  let gesture, suppressClick;
  const options = {signal};

  function finish(commit, repaint = true) {
    if (!gesture) return;
    const {node, pointer, moved, drag} = gesture;
    gesture = null;
    node.classList.remove('is-dragging');
    if (node.hasPointerCapture(pointer)) node.releasePointerCapture(pointer);
    if (moved) suppressClick = {node, pointer};
    drag.finish(moved, commit, repaint);
  }

  atlas.addEventListener('pointerdown', event => {
    if (!event.isPrimary || event.button !== 0 || gesture) return;
    suppressClick = null;
    const node = event.target.closest(selector);
    const drag = node && !node.disabled && resolve(node);
    if (!drag) return;
    gesture = {node, drag, pointer: event.pointerId, x: event.clientX, y: event.clientY, moved: false};
    node.setPointerCapture(event.pointerId);
  }, options);

  function move(event) {
    if (gesture?.pointer !== event.pointerId) return;
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    if (!gesture.moved && Math.hypot(dx, dy) < 5) return;
    if (!gesture.moved) {
      gesture.moved = true;
      gesture.node.classList.add('is-dragging');
      gesture.drag.start?.();
    }
    gesture.drag.move(dx, dy);
  }
  atlas.addEventListener('pointermove', move, options);
  atlas.addEventListener('pointerup', event => {
    if (gesture?.pointer !== event.pointerId) return;
    move(event);
    finish(true);
  }, options);
  for (const name of ['pointercancel', 'lostpointercapture']) {
    atlas.addEventListener(name, event => { if (gesture?.pointer === event.pointerId) finish(false); }, options);
  }
  atlas.addEventListener('click', event => {
    if (!suppressClick || event.detail === 0 || !suppressClick.node.contains(event.target)) return;
    if ('pointerId' in event && event.pointerId !== suppressClick.pointer) return;
    suppressClick = null;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, {...options, capture: true});
  atlas.addEventListener('keydown', event => {
    if (event.key === 'Escape' && gesture) {
      event.preventDefault();
      event.stopPropagation();
      finish(false);
      return;
    }
    if (!Object.hasOwn(arrows, event.key) || gesture || event.altKey || event.ctrlKey || event.metaKey) return;
    const direction = arrows[event.key];
    const node = event.target.closest(selector);
    const drag = node && !node.disabled && resolve(node);
    if (!drag) return;
    event.preventDefault();
    drag.start?.();
    const step = event.shiftKey ? 32 : 8;
    drag.move(direction[0] * step, direction[1] * step);
    drag.finish(true, true, true);
  }, options);
  const cancel = () => finish(false);
  window.addEventListener('scroll', cancel, {...options, capture: true, passive: true});
  window.addEventListener('resize', cancel, options);
  window.addEventListener('blur', cancel, options);
  signal.addEventListener('abort', () => finish(false, false), {once: true});
  return (repaint = true) => finish(false, repaint);
}
