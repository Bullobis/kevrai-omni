// renderer/modules/agent-quickphrases.js — Agent 面板「快捷短语」纯数据与纯函数。
//
// 对标 Cherry Studio 的快捷短语：把新手最常问、最容易写错的话预先做成
// 可点击的小药丸（chip），点击即填入输入框（不自动发送，留给用户修改）。
//
// 本模块刻意保持「纯」：
//   - 不 import DOM、不 import i18n、不触碰 window/document；
//   - 导出分组短语数据 + 若干纯函数，便于 node:test 直接断言；
//   - HTML 渲染与事件接线留在 agent.js（那里有 esc/t/input/sendBtn），
//     本模块只负责「数据是什么」「点完之后输入框该变成什么」。
"use strict";

// 每个分组：{ id, icon, label, phrases: [{ id, label, text }] }
//   id     —— 全局唯一（点号命名空间），用于事件委托与测试断言；
//   icon   —— 分组前的 emoji，仅作视觉分组；
//   label  —— 分组名（中文）；
//   phrase.label —— chip 上显示的短标签；
//   phrase.text  —— 点下去真正填进输入框的自然语言（可直接发送）。
//
// 内容选择原则：
//   - 都是新手会问、但不知道怎么措辞的话；
//   - 不写「最强 / SOTA / 第一」这类软文；
//   - 以祈使句结尾，冒号结尾的预留位置给用户补素材。
export const QUICK_PHRASE_GROUPS = Object.freeze([
  {
    id: "models",
    icon: "🔍",
    label: "模型查询",
    phrases: [
      {
        id: "models.list",
        label: "我有哪些模型？",
        text: "列出我当前已安装和可下载的本地模型，并简要说明每个分别适合做什么任务。",
      },
      {
        id: "models.recommend",
        label: "按用途推荐",
        text: "我主要用来做中文写作、日常问答和长文总结，帮我推荐几个合适的本地模型，并说明理由。",
      },
      {
        id: "models.choose",
        label: "帮我挑一个",
        text: "我想选一个用来写东西的模型，参数不要太大、中文要好，帮我挑一个并说明为什么。",
      },
    ],
  },
  {
    id: "hardware",
    icon: "💻",
    label: "硬件适配",
    phrases: [
      {
        id: "hardware.detect",
        label: "我能跑什么？",
        text: "检测一下这台电脑的配置（内存、显卡、显存），告诉我大概能流畅跑多大参数的本地模型。",
      },
      {
        id: "hardware.lowresource",
        label: "低配可用",
        text: "我的电脑内存和显存都不大，推荐几个占用资源低、日常对话还够用的轻量模型。",
      },
      {
        id: "hardware.speed",
        label: "怎么加速",
        text: "本地模型跑得很慢，帮我检查设置，给出能在这台机器上提速的具体办法。",
      },
    ],
  },
  {
    id: "install",
    icon: "⬇️",
    label: "安装下载",
    phrases: [
      {
        id: "install.pick",
        label: "挑一个并教我下载",
        text: "我想下载一个适合中文写作的模型，帮我挑一个，并写出从下载到能对话的完整步骤。",
      },
      {
        id: "install.engine",
        label: "装推理引擎",
        text: "我还没装本地推理引擎，帮我从零说明怎么安装和配置，以及需要留意哪些依赖。",
      },
      {
        id: "install.mirror",
        label: "换国内镜像",
        text: "模型下载速度太慢，帮我配置国内镜像源，然后从断点继续下载刚才那个模型。",
      },
    ],
  },
  {
    id: "creation",
    icon: "✍️",
    label: "创作",
    phrases: [
      {
        id: "creation.rewrite",
        label: "改写短文",
        text: "帮我把下面这段素材改写成一篇通顺、口语化的中文短文，保留原意：\n",
      },
      {
        id: "creation.drama",
        label: "写短剧大纲",
        text: "用短剧创作工坊，把这个点子写成一部三幕竖屏短剧大纲（有人物、冲突和钩子）：\n",
      },
      {
        id: "creation.music",
        label: "做背景音乐",
        text: "我想做一段背景音乐，帮我写好音乐生成用的提示词，并建议风格、速度和时长参数。",
      },
    ],
  },
  {
    id: "media",
    icon: "🎬",
    label: "媒体处理",
    phrases: [
      {
        id: "media.transcribe",
        label: "语音转文字",
        text: "帮我把这个音频文件转写成文字，并整理成带时间轴的字幕（srt）格式：\n",
      },
      {
        id: "media.separate",
        label: "人声伴奏分离",
        text: "把这段音频里的人声和伴奏分离开来，分别导出成两个文件。",
      },
      {
        id: "media.tts",
        label: "文字转语音",
        text: "把下面这段文字合成为自然的中文语音，语速平稳：\n",
      },
      {
        id: "media.image",
        label: "生成配图",
        text: "根据下面这段描述生成一张配图，并先写出适合本地图像模型的英文提示词：\n",
      },
    ],
  },
]);

// 扁平化一次，供 findPhraseById O(1) 查找（模块加载期构建，只读）。
const _flat = new Map();
for (const group of QUICK_PHRASE_GROUPS) {
  for (const phrase of group.phrases) {
    _flat.set(phrase.id, { groupId: group.id, group, phrase });
  }
}

/**
 * findPhraseById(id) — 按扁平 id 取短语。
 * 返回 { group, phrase }；找不到返回 null。纯函数。
 */
export function findPhraseById(id) {
  const hit = _flat.get(id);
  return hit ? { group: hit.group, phrase: hit.phrase } : null;
}

/**
 * allPhrases() — 扁平返回所有短语，顺序与分组一致。
 */
export function allPhrases() {
  const out = [];
  for (const group of QUICK_PHRASE_GROUPS) {
    for (const phrase of group.phrases) out.push({ group, phrase });
  }
  return out;
}

/**
 * composePhraseValue(currentValue, phraseText) — 纯函数：决定点了某个 chip 之后，
 * 输入框的新值应该是什么。
 *
 * 规则：
 *   - 当前输入为空（或全空白）→ 直接填短语；
 *   - 当前已有内容 → 末尾补一个换行再追加短语（保留用户已写的内容，可多选叠加）；
 *   - phraseText 为空 → 原样返回 currentValue（防御，不污染输入框）。
 *
 * 不在此处做「重复点击去重」：Cherry Studio 的行为就是允许叠加，用户可自行删改。
 */
export function composePhraseValue(currentValue, phraseText) {
  const text = String(phraseText == null ? "" : phraseText);
  if (!text) return String(currentValue == null ? "" : currentValue);
  const cur = String(currentValue == null ? "" : currentValue).replace(/\s+$/, "");
  return cur ? cur + "\n" + text : text;
}
