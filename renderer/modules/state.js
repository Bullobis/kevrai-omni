// renderer/modules/state.js — tiny shared store with subscriber notifications.
"use strict";

const subs = new Set();

export const state = {
  categories: [],
  models: [],
  ggufRepos: [],
  engines: [],
  local: [],
  pending: [],
  selectedId: null,
  downloads: {},         // taskId -> {taskId, downloaded, total, status, filename}
};

export function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
export function emit() { for (const fn of subs) { try { fn(state); } catch (_) {} } }

export function setState(patch) { Object.assign(state, patch); emit(); }
