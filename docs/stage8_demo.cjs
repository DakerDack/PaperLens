'use strict';

// Real local API and PDF parsing; model answers are explicitly marked synthetic Mock results.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
const requireFrontend = Module.createRequire(path.join(frontend, 'package.json'));
const { chromium, expect } = requireFrontend('@playwright/test');
const rehearsal = process.argv.includes('--rehearse');
const output = path.join(root, 'reports');
process.chdir(frontend);
async function expectCanvasInk(page, number) {
  const canvas = page.locator(`canvas[aria-label="PDF 第 ${number} 页"]`);
  await expect(canvas).toBeVisible();
  await expect.poll(() => canvas.evaluate(e => {
    const pixels = e.getContext('2d').getImageData(0, 0, e.width, e.height).data;
    let ink = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i + 3] && pixels[i] < 150 && pixels[i + 1] < 150 && pixels[i + 2] < 150) ink++;
    }
    return ink;
  })).toBeGreaterThan(100);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    baseURL: 'http://127.0.0.1:5173',
    ...(rehearsal ? {} : { recordVideo: { dir: output, size: { width: 1440, height: 900 } } }),
  });
  const page = await context.newPage();
  const steps = [];
  const started = Date.now();
  let complete = false;
  async function caption(text) {
    await page.locator('#demo-caption').evaluate((e, value) => { e.textContent = value; }, text);
    steps.push({ step: text, elapsed_seconds: (Date.now() - started) / 1000 });
    console.log(text);
    await page.waitForTimeout(rehearsal ? 50 : 1600);
  }
  async function click(locator, label) {
    await locator.scrollIntoViewIfNeeded();
    await expect(locator, label).toBeVisible();
    const box = await locator.boundingBox();
    if (!box) throw new Error('Demo target has no visible bounds: ' + label);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 15 });
    await page.waitForTimeout(rehearsal ? 30 : 400);
    await locator.click();
    await page.waitForTimeout(rehearsal ? 50 : 1300);
  }
  try {
    await page.goto('/');
    await page.evaluate(() => {
      const label = document.createElement('div');
      label.style.cssText = 'position:fixed;left:50%;top:10px;transform:translateX(-50%);z-index:99999;background:#fff3bd;color:#362800;padding:8px 18px;font:16px sans-serif;border:1px solid #8a6700;pointer-events:none';
      label.textContent = 'Mock 模型预置结果 · 非实时 Hy3 · 真实本地 API / 文本解析';
      document.body.appendChild(label);
      const caption = document.createElement('div');
      caption.id = 'demo-caption';
      caption.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:99999;background:#132b25;color:white;text-align:center;font:18px sans-serif;padding:9px;pointer-events:none';
      document.body.appendChild(caption);
      document.querySelector('.workspace').style.height = 'calc(100vh - 200px)';
      const cursor = document.createElement('div');
      cursor.style.cssText = 'position:fixed;z-index:100000;pointer-events:none';
      cursor.innerHTML = '<svg width="25" height="27" viewBox="0 0 25 27"><path d="M3 2L21 14L13 15L9 24Z" fill="white" stroke="black" stroke-width="2"/></svg>';
      document.body.appendChild(cursor);
      document.addEventListener('mousemove', e => { cursor.style.left = e.clientX + 'px'; cursor.style.top = e.clientY + 'px'; });
    });
    await caption('1 / 上传有权使用的合成 PDF，实际运行 pdfplumber 文本解析');
    await page.getByLabel('选择 PDF').setInputFiles(path.join(root, 'backend/tests/fixtures/simple_2page.pdf'));
    await click(page.getByRole('checkbox', { name: /确认拥有处理权限/ }), '确认权限');
    await click(page.getByRole('button', { name: '上传论文 PDF', exact: true }), '上传解析');
    await expect(page.getByText('Mock', { exact: true })).toBeVisible();
    await expectCanvasInk(page, 1);
    await caption('2 / 联合生成五区解读，自动快速检查');
    await click(page.getByRole('button', { name: '生成五区解读', exact: true }), '生成');
    await expect(page.getByText('快速检查完成', { exact: true }).first()).toBeVisible();
    await caption('3 / 完整审计：展示预置八维结果，不代表模型效果');
    await click(page.getByRole('button', { name: '运行完整审计', exact: true }), '完整审计');
    await expect(page.getByText('完整审计完成', { exact: true })).toBeVisible();
    await caption('4 / 点击句子，回到第 2 页并核对摘录');
    await click(page.getByRole('button', { name: /The fixture identifies the second page as page two/ }), '句子证据');
    await expectCanvasInk(page, 2);
    await expect(page.getByText('第 2 / 2 页')).toBeVisible();
    await expect(page.getByRole('region', { name: '句子证据' }).getByText('PaperLens fixture - page two')).toBeVisible();
    await page.waitForTimeout(rehearsal ? 50 : 3000);
    await page.screenshot({ path: path.join(output, 'stage8_demo_evidence.png') });
    await caption('5 / 提出句子修改，预览前后差异；当前版本尚未改变');
    await click(page.getByLabel('修改意图'), '修改意图');
    await page.getByLabel('修改意图').pressSequentially('只追加安全标点', { delay: rehearsal ? 1 : 65 });
    await click(page.getByRole('button', { name: '生成补丁预览', exact: true }), '补丁预览');
    await expect(page.getByRole('region', { name: '修改前后差异' })).toBeVisible();
    await page.getByRole('region', { name: '修改前后差异' }).scrollIntoViewIfNeeded();
    await page.waitForTimeout(rehearsal ? 50 : 4000);
    await page.screenshot({ path: path.join(output, 'stage8_demo_preview.png') });
    await caption('6 / 明确接受后才生成新版本，并自动快速复核');
    await click(page.getByRole('button', { name: '接受修改', exact: true }), '接受修改');
    await expect(page.locator('.project-summary').getByText('版本 2')).toBeVisible();
    await caption('7 / 对新版本重新运行完整审计');
    await click(page.getByRole('button', { name: '运行完整审计', exact: true }), '复核');
    await expect(page.getByText('完整审计完成', { exact: true })).toBeVisible();
    await caption('8 / 查看历史，回退到版本 1；保留全部历史');
    await click(page.getByRole('button', { name: '回退到版本 1', exact: true }), '版本回退');
    await expect(page.locator('.project-summary').getByText('版本 3')).toBeVisible();
    await expect(page.locator('.version-list > li')).toHaveCount(3);
    await caption('9 / 导出当前 Markdown，保留来源与 AI 模式说明');
    const downloadPromise = page.waitForEvent('download');
    await click(page.getByRole('button', { name: '导出当前 Markdown', exact: true }), '导出');
    await (await downloadPromise).saveAs(path.join(output, 'stage8_demo_export.md'));
    await caption('演示结束 · 本地闭环完成；Mock 模型结果不证明实时 Hy3 效果');
    await page.waitForTimeout(rehearsal ? 50 : 2500);
    complete = true;
  } finally {
    const duration = (Date.now() - started) / 1000;
    await context.close();
    if (!rehearsal) await page.video().saveAs(path.join(output, 'stage8_demo.webm'));
    await browser.close();
    fs.writeFileSync(path.join(output, rehearsal ? 'stage8_demo_rehearsal.json' : 'stage8_demo_steps.json'), JSON.stringify({ mode: 'mock_model_real_local_api', real_hy3: false, real_backend: true, parser: 'pdfplumber', complete, duration_seconds: duration, steps }, null, 2) + '\n');
    if (!complete || duration > 120) throw new Error('Demo incomplete or exceeds 120 seconds');
  }
})().catch(error => { console.error('DEMO_FAILED:', error.message); process.exitCode = 1; });
