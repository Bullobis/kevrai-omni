// renderer/__tests__/helpers/dom.js — deterministic jsdom bootstrap for the
// renderer unit tests. The renderer modules are browser ESM that reference the
// bare globals `window` and `document`; this wires jsdom to globalThis and
// provides a manually-flushed requestAnimationFrame so scroll/resize throttling
// can be tested without real timers.
import { JSDOM } from "jsdom";

export function setupDom(bodyHtml = '<div id="host"></div>') {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${bodyHtml}</body></html>`, {
    pretendToBeVisual: false, // we install a controllable rAF below
  });
  const { window } = dom;

  const rafQueue = new Map();
  let rafSeq = 0;
  const requestAnimationFrame = (cb) => {
    const id = ++rafSeq;
    rafQueue.set(id, cb);
    return id;
  };
  const cancelAnimationFrame = (id) => rafQueue.delete(id);
  const flushFrames = async (frames = 1) => {
    for (let f = 0; f < frames; f++) {
      const cbs = [...rafQueue.values()];
      rafQueue.clear();
      cbs.forEach((cb) => cb());
      // let any queued microtasks/then handlers settle
      // eslint-disable-next-line no-await-in-loop
      await Promise.resolve();
    }
  };

  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.requestAnimationFrame = requestAnimationFrame;
  globalThis.cancelAnimationFrame = cancelAnimationFrame;
  // A handful of constructors used when dispatching events in tests.
  globalThis.Event = window.Event;
  globalThis.KeyboardEvent = window.KeyboardEvent;
  globalThis.MouseEvent = window.MouseEvent;

  return { dom, window, document: window.document, flushFrames };
}

// jsdom performs no layout, so clientWidth/clientHeight read 0. For geometry
// tests we stub them to fixed values.
export function stubSize(el, { width, height }) {
  if (width != null) {
    Object.defineProperty(el, "clientWidth", { configurable: true, value: width });
  }
  if (height != null) {
    Object.defineProperty(el, "clientHeight", { configurable: true, value: height });
  }
}
