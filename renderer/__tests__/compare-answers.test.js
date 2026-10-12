// renderer/__tests__/compare-answers.test.js — pure helpers for compare panel.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  answerColumnsHtml, modelCheckboxesHtml, shortLabel,
} from "../modules/compare-answers.js";

test("shortLabel takes the part after the slash", () => {
  assert.equal(shortLabel("HuggingFaceTB/SmolLM2-135M-Instruct"),
    "SmolLM2-135M-Instruct");
  assert.equal(shortLabel("plain"), "plain");
});

test("modelCheckboxesHtml renders each model, first checked", () => {
  const html = modelCheckboxesHtml([
    { repo: "a/x", label: "X" },
    { repo: "a/y", label: "Y" },
  ]);
  assert.match(html, /value="a\/x" checked/);
  assert.match(html, /value="a\/y"/);
  assert.match(html, />X</);
  assert.doesNotMatch(html, /value="a\/y" checked/);
});

test("modelCheckboxesHtml empty list message", () => {
  assert.match(modelCheckboxesHtml([]), /noModels/);
});

test("answerColumnsHtml renders answers", () => {
  const html = answerColumnsHtml([
    { repo: "a/x", answer: "first answer", error: null },
  ]);
  assert.match(html, /ca-answer/);
  assert.match(html, /first answer/);
});

test("answerColumnsHtml renders errors distinctly", () => {
  const html = answerColumnsHtml([
    { repo: "a/x", answer: "", error: "boom" },
  ]);
  assert.match(html, /ca-error/);
  assert.match(html, /boom/);
  assert.doesNotMatch(html, /ca-answer/);
});
