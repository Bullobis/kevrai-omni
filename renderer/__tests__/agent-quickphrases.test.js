// renderer/__tests__/agent-quickphrases.test.js — 纯 helper：数据结构完整性 +
// findPhraseById + composePhraseValue 纯函数行为。不依赖 DOM。
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  QUICK_PHRASE_GROUPS, findPhraseById, allPhrases, composePhraseValue,
} from "../modules/agent-quickphrases.js";

test("分组数与短语总数满足入门门槛（≥5 组 / ≥12 条）", () => {
  assert.ok(QUICK_PHRASE_GROUPS.length >= 5,
    `expected >=5 groups, got ${QUICK_PHRASE_GROUPS.length}`);
  const total = allPhrases().length;
  assert.ok(total >= 12, `expected >=12 phrases, got ${total}`);
});

test("覆盖要求的五个主题分组", () => {
  const ids = new Set(QUICK_PHRASE_GROUPS.map((g) => g.id));
  for (const need of ["models", "hardware", "install", "creation", "media"]) {
    assert.ok(ids.has(need), `missing group: ${need}`);
  }
});

test("每条短语都有非空 label / text，扁平 id 全局唯一", () => {
  const seen = new Set();
  for (const g of QUICK_PHRASE_GROUPS) {
    assert.ok(g.icon && g.label, `group ${g.id} needs icon+label`);
    assert.ok(Array.isArray(g.phrases) && g.phrases.length > 0,
      `group ${g.id} needs phrases`);
    for (const p of g.phrases) {
      assert.ok(p.id && p.label && p.text, `phrase incomplete: ${p.id}`);
      assert.equal(seen.has(p.id), false, `duplicate phrase id: ${p.id}`);
      seen.add(p.id);
    }
  }
});

test("短语文本是可直接发送的自然语言，不含软文词", () => {
  for (const { phrase } of allPhrases()) {
    assert.ok(phrase.text.trim().length >= 8, `too short: ${phrase.id}`);
    for (const bad of ["最强", "SOTA", "第一", "最好用", "吊打"]) {
      assert.ok(!phrase.text.includes(bad), `phrase ${phrase.id} contains fluff: ${bad}`);
    }
  }
});

test("findPhraseById：命中返回 {group, phrase}，未命中返回 null", () => {
  const hit = findPhraseById("models.list");
  assert.ok(hit);
  assert.equal(hit.phrase.id, "models.list");
  assert.equal(hit.group.id, "models");
  assert.equal(findPhraseById("does.not.exist"), null);
  assert.equal(findPhraseById(undefined), null);
});

test("composePhraseValue：空输入直接填短语", () => {
  assert.equal(composePhraseValue("", "你好"), "你好");
  assert.equal(composePhraseValue("   \n ", "你好"), "你好");
});

test("composePhraseValue：已有内容换行追加，保留用户所写", () => {
  assert.equal(
    composePhraseValue("我的问题", "追加这句"),
    "我的问题\n追加这句",
  );
});

test("composePhraseValue：空短语不污染输入框，null/undefined 安全", () => {
  assert.equal(composePhraseValue("原样", ""), "原样");
  assert.equal(composePhraseValue("原样", null), "原样");
  assert.equal(composePhraseValue(null, "短语"), "短语");
  assert.equal(composePhraseValue(undefined, undefined), "");
});
