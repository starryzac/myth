import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

const build = path.dirname(fileURLToPath(import.meta.url));
const base = path.dirname(build);
const skill = 'C:/Users/starryzac/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations';
const python = 'C:/Users/starryzac/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe';
const { finalizePresentation, resolvePresentationFont } = await import(pathToFileURL(path.join(skill, 'container_tools/artifact_tool_utils.mjs')).href);
const family = resolvePresentationFont({ fontFamily: 'Microsoft YaHei' });
const packet = JSON.parse(await fs.readFile(path.join(base, 'pages.json'), 'utf8'));
const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
const ink = '#18352F', green = '#13725A', muted = '#566D66';

function text(slide, value, box, size, color = ink, bold = false) {
  const shape = slide.shapes.add({ geometry: 'textbox', position: box, fill: 'none', line: { fill: 'none', width: 0 } });
  shape.text = value;
  shape.text.style = { typeface: family, fontSize: size, color, bold, autoFit: 'none' };
  return shape;
}

for (const [index, data] of packet.slides.entries()) {
  const slide = presentation.slides.add();
  const cover = index === 0;
  slide.background.fill = cover ? green : '#FFFFFF';
  const fg = cover ? '#FFFFFF' : ink;
  text(slide, data.kicker, { left: 80, top: 37, width: 1120, height: 36 }, 18, cover ? '#D6EFE5' : green);
  text(slide, data.title, { left: 80, top: 98, width: 1120, height: 72 }, cover ? 64 : 44, fg, true);
  text(slide, data.headline, { left: 80, top: cover ? 222 : 198, width: 1120, height: 140 }, cover ? 43 : 35, fg, true);
  const start = cover ? 426 : 370;
  for (const [n, item] of data.body.entries()) {
    text(slide, item, { left: 80, top: start + n * 73, width: 1100, height: 66 }, 24, cover ? '#FFFFFF' : muted);
  }
  text(slide, `SOURCE ${data.refs.join(' · ')}  |  全部模拟 · WORKING / NOT ACCEPTANCE`, { left: 80, top: 657, width: 1040, height: 26 }, 14, cover ? '#D6EFE5' : muted);
  text(slide, `${index + 1} / 10`, { left: 1134, top: 657, width: 95, height: 26 }, 14, cover ? '#FFFFFF' : green);
  slide.speakerNotes.textFrame.setText(`${data.note}\n来源：${data.refs.map(id => `${id}: ../sources.json（精确原件及SHA）`).join('\n')}\n本页全部为可编辑文字；流程是SOURCE_SCHEMATIC_NOT_NATIVE_SCREENSHOT。效果结果缺失，不含实验柱形。`);
}

const staging = path.join(build, process.argv[2] ?? 'presentation-finalizer');
await fs.mkdir(staging, { recursive: true });
const candidate = path.join(staging, 'candidate.pptx');
const final = path.join(base, 'outputs', '钱途有界_当前功能答辩_10页.pptx');
await (await PresentationFile.exportPptx(presentation)).save(candidate);
const result = await finalizePresentation({
  explicitTotalSlideCount: 10,
  workspaceDir: base,
  candidatePath: candidate,
  finalPath: final,
  pythonExecutable: python,
  integrityValidatorPath: path.join(skill, 'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath: path.join(skill, 'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs: ['--expected-slide-size-emu', '12192000,6858000', '--validate-bullet-geometry', '--validate-heading-fit'],
  requiredNativeTableOwnerSlides: [],
  fontPolicy: { basis: 'design', families: [family], scriptFonts: { ea: family } },
  verifyArtifactToolImport: true,
  receiptPath: path.join(staging, 'validation.json'),
});
await fs.mkdir(path.join(build, 'deck-previews'), { recursive: true });
for (const [index, slide] of [...presentation.slides.items].entries()) {
  const preview = await presentation.export({ slide, format: 'png', scale: 1 });
  await fs.writeFile(path.join(build, 'deck-previews', `slide-${String(index+1).padStart(2,'0')}.png`), new Uint8Array(await preview.arrayBuffer()));
  const layout = await slide.export({ format: 'layout' });
  await fs.writeFile(path.join(build, 'deck-previews', `slide-${String(index+1).padStart(2,'0')}.layout.json`), await layout.text());
}
const bytes = await fs.readFile(final);
await fs.writeFile(path.join(build, 'deck-structure.json'), JSON.stringify({ status: 'STRUCTURAL_DELIVERY',
  path: path.relative(base,final), slides: 10, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'),
  editable_text: true, native_screenshot_count: 0, visual_review: 'PENDING', product_acceptance: 'INCOMPLETE', finalization: result },null,2)+'\n');
console.log(JSON.stringify({ path: final, slides: 10, bytes: bytes.length, product_acceptance: 'INCOMPLETE' }));
