// renderer/__tests__/workspaces.test.js — workspace list rendering helpers.
import { test } from "node:test";
import assert from "node:assert/strict";
import { workspaceRowsHtml } from "../modules/workspaces.js";

const workspaces = [
  { id: "default", name: "Default", builtin: true },
  { id: "ws_abc", name: "Work" },
  { id: "ws_xyz", name: "Experiments" },
];

test("renders one row per workspace with escaped name", () => {
  const html = workspaceRowsHtml(workspaces, "default");
  assert.equal((html.match(/class="ws-row"/g) || []).length, 3);
  assert.match(html, /Default/);
  assert.match(html, /Experiments/);
});

test("active workspace shows a badge and no switch button", () => {
  const html = workspaceRowsHtml(workspaces, "ws_abc");
  const activeRow = html.split('class="ws-row"')[2];
  assert.match(activeRow, /ws-active-badge/);
  // The active row must not contain a switch button.
  assert.equal(/ws-switch/.test(activeRow), false);
});

test("non-active rows get a switch button with their id", () => {
  const html = workspaceRowsHtml(workspaces, "default");
  assert.match(html, /ws-switch" data-id="ws_abc"/);
  assert.match(html, /ws-switch" data-id="ws_xyz"/);
});

test("default workspace cannot be removed; custom workspaces can", () => {
  const html = workspaceRowsHtml(workspaces, "default");
  const rows = html.split('<div class="ws-row"');
  const defaultRow = rows[1];
  const customRow = rows[2];
  assert.equal(/ws-remove/.test(defaultRow), false);
  assert.match(customRow, /ws-remove" data-id="ws_abc"/);
});

test("every row offers rename", () => {
  const html = workspaceRowsHtml(workspaces, "default");
  assert.equal((html.match(/ws-rename/g) || []).length, 3);
});

test("empty input renders empty string", () => {
  assert.equal(workspaceRowsHtml([], "default"), "");
  assert.equal(workspacesRowsSafe(), "");
  function workspacesRowsSafe() {
    return workspaceRowsHtml(undefined, "default");
  }
});
