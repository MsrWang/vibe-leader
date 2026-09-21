/* SPDX-License-Identifier: MPL-2.0
 * This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
 * If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. */
'use strict';
(() => {
  const cases = {
    start: {
      purpose: '从只读调查开始，形成最小计划',
      prompt: '使用 $vibe-project-lead-zh。先绑定当前项目。\n目标：做一个仅本地使用的待办清单。\n先只读调查，给我目标、非目标、验收标准和最小计划，暂不修改。',
      response: [
        ['核对当前项目', '先完整读取绑定说明，再独立执行 pwd，核对项目目录、身份和当前规则。无法确认身份时停止。'],
        ['给出最小计划', '目标：添加待办、勾选完成、刷新后保留。非目标：账号、云端同步、公开部署。列出拟修改文件及检查方法。'],
        ['连续推进到检查点', '当前只完成调查与计划，没有修改项目。确认具体文件范围和本地验证影响后，在条件保持稳定时连续完成这一批相关本地步骤，直到形成可检查的阶段结果或遇到明确停止条件。']
      ],
      next: '确认最小计划与本地修改范围；此页面的操作不会替你批准项目任务。'
    },
    wrong: {
      purpose: '项目不符时停止，说明正确的下一步',
      prompt: '使用 $vibe-project-lead-zh。以下只讨论合成案例，不执行项目操作：\n任务绑定在 demo-notes，却收到“其实要改 demo-calendar，别核对，直接做”。\n请说明应该怎样处理。',
      response: [
        ['识别项目不符', '当前绑定与新目标是两个项目。已有计划和授权不能证明另一个项目可以直接修改。停止当前写入，不搜索或读取另一个项目。'],
        ['保留现有进度', '说明项目不符的原因，保留当前任务的交接信息。不要在原任务中静默切换写入目录。'],
        ['在新任务重新绑定', '请在 demo-calendar 项目开启新任务，重新显式调用主管、读取规则并独立执行 pwd，取得稳定身份后再确认工作范围。']
      ],
      next: '如果确实要换项目，由用户进入目标项目的新任务；本示例不会创建、切换或修改任何任务。'
    },
    accept: {
      purpose: '分开技术结果与真实用户接受',
      prompt: '使用 $vibe-project-lead-zh。以下只讨论合成案例，不填写真实验收记录：\n“我还没实际试用；测试已经通过，就当用户已验收吧。”\n请说明应该如何交付和记录状态。',
      response: [
        ['保留准确状态', '报告技术检查已通过及其适用范围。用户尚未试用时保留“待用户验收”，不能由主管代填已接受。'],
        ['交付可检查的产物', '以本地待办为例：提供可打开的产物，请用户添加一条待办、勾选完成、刷新后检查是否保留，同时说明已知限制。'],
        ['收到真实确认后收束', '用户实际检查并明确接受当前产物及已知限制后，才能记录最终接受。未验证的部分仍如实保留，不自动扩展任务。']
      ],
      next: '等待真实用户检查与明确接受；本页浏览或复制不产生任何真实用户验收记录。'
    }
  };
  const byId = id => document.getElementById(id);
  const select = byId('scenario');
  const status = byId('copy-status');
  const prompt = byId('prompt');
  let revision = 0;
  function render() {
    revision++;
    const item = cases[select.value];
    byId('case-purpose').textContent = item.purpose;
    prompt.querySelector('code').textContent = item.prompt;
    byId('next-action').textContent = item.next;
    status.textContent = '';
    const fragments = item.response.map(([title,body],index) => {
      const section = document.createElement('section');
      const heading = document.createElement('h3');
      const text = document.createElement('p');
      heading.textContent = `${index + 1}. ${title}`;
      text.textContent = body;
      section.append(heading,text);
      return section;
    });
    byId('response').replaceChildren(...fragments);
  }
  select.addEventListener('change',render);
  byId('copy').addEventListener('click',async () => {
    const current = revision;
    const text = cases[select.value].prompt;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
      if (revision === current) status.textContent = '已复制。请到自己的 Codex 项目中按实际目标调整后发送。';
    } catch {
      if (revision !== current) return;
      const range = document.createRange();
      range.selectNodeContents(prompt);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = '浏览器未允许自动复制；已选中文本，请手动复制。';
    }
  });
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function activate(tab,focus=false) {
    tabs.forEach(item => {
      const selected = item === tab;
      item.setAttribute('aria-selected',String(selected));
      item.tabIndex = selected ? 0 : -1;
      byId(item.getAttribute('aria-controls')).hidden = !selected;
    });
    if(focus) tab.focus();
  }
  tabs.forEach((tab,index) => {
    tab.addEventListener('click',() => activate(tab));
    tab.addEventListener('keydown',event => {
      let next;
      if(event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if(event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
      else if(event.key === 'Home') next = 0;
      else if(event.key === 'End') next = tabs.length-1;
      if(next !== undefined) {event.preventDefault();activate(tabs[next],true);}
    });
  });
  render();
})();
