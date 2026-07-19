// 离线发行门禁: 证明 viewer 的模块图闭合在 ./vendor, 无网络也能加载 full 3DGS。
// 无需网络运行。被 `node --test web/viewer/*.test.mjs` 收录。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, posix } from 'node:path';

const VIEWER_DIR = dirname(fileURLToPath(import.meta.url));
const read = (p) => readFileSync(resolve(VIEWER_DIR, p), 'utf8');

// index.html 里 vendored 的文件 (相对 vendor/), 模块图闭合与 sha256 都以此为准。
const VENDORED = [
  'three/three.module.js',
  'three/three.core.js',
  'three/addons/controls/OrbitControls.js',
  'three/addons/loaders/GLTFLoader.js',
  'three/addons/loaders/KTX2Loader.js',
  'three/addons/libs/ktx-parse.module.js',
  'three/addons/libs/zstddec.module.js',
  'three/addons/math/ColorSpaces.js',
  'three/addons/postprocessing/Pass.js',
  'three/addons/utils/BufferGeometryUtils.js',
  'three/addons/utils/WorkerPool.js',
  'three/examples/jsm/libs/basis/basis_transcoder.js',
  'three/examples/jsm/libs/basis/basis_transcoder.wasm',
  'spark/spark.module.js',
];

const LICENSES = {
  'three/LICENSE': 'bfe119ea4fd413f5f7ca3fcd63adb0c4a073ed39daa2fe7d3e6b769e21272601',
  'spark/LICENSE': '7ab7f9c7c389f20899bc02d2b4be19e33fcef27ce97ec08ff77cb052b06c5c6a',
};

// ---- importmap 解析 (来自 index.html) ----
function loadImportmap() {
  const html = read('index.html');
  const m = html.match(/<script type="importmap">\s*([\s\S]*?)<\/script>/);
  assert.ok(m, 'index.html 必须含 importmap');
  return JSON.parse(m[1]).imports;
}

const stripLeadingDot = (p) => posix.normalize(p.replace(/^\.\//, ''));

// ---- 说明符解析: 复刻浏览器 importmap + 相对解析 ----
function resolveSpecifier(spec, importerRel, imports) {
  if (spec.startsWith('data:') || spec.startsWith('blob:')) return null; // 内联, 非文件
  if (/^https?:\/\//.test(spec)) {
    throw new Error(`网络依赖未 vendored: ${importerRel} → ${spec}`);
  }
  if (spec.startsWith('./') || spec.startsWith('../')) {
    return posix.normalize(posix.join(posix.dirname(importerRel), spec));
  }
  if (imports[spec]) return stripLeadingDot(imports[spec]); // 精确 bare
  for (const [key, val] of Object.entries(imports)) {       // 前缀映射 (以 / 结尾)
    if (key.endsWith('/') && spec.startsWith(key)) {
      return stripLeadingDot(val + spec.slice(key.length));
    }
  }
  throw new Error(`说明符无法经 importmap 解析: ${importerRel} → ${spec}`);
}

// ---- 提取一个模块的静态/动态字符串字面量 import ----
function extractSpecifiers(src) {
  const specs = new Set();
  const re =
    /(?:import|export)[\s\S]*?from\s*['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)/g;
  let m;
  while ((m = re.exec(src))) specs.add(m[1] ?? m[2]);
  return [...specs];
}

test('importmap 无任何 CDN / 外部 URL', () => {
  const imports = loadImportmap();
  for (const [k, v] of Object.entries(imports)) {
    assert.ok(!/^https?:\/\//.test(v), `importmap["${k}"] 仍指向外部: ${v}`);
    assert.ok(v.startsWith('./vendor/'), `importmap["${k}"] 应指向 ./vendor/: ${v}`);
  }
});

test('模块图从入口起完全闭合在本地 (BFS, 无未 vendored 依赖)', () => {
  const imports = loadImportmap();
  // 入口: index.html 的 module 入口 main.js, 加上 splat-layer 懒加载的 spark 根。
  const seeds = [
    'main.js',
    stripLeadingDot(imports['@sparkjsdev/spark']),
    'vendor/three/addons/loaders/KTX2Loader.js',
    'vendor/three/examples/jsm/libs/basis/basis_transcoder.js',
    'vendor/three/examples/jsm/libs/basis/basis_transcoder.wasm',
  ];
  const seen = new Set();
  const queue = [...seeds];
  while (queue.length) {
    const rel = queue.shift();
    if (seen.has(rel)) continue;
    seen.add(rel);
    const abs = resolve(VIEWER_DIR, rel);
    assert.ok(existsSync(abs), `模块图指向缺失文件: ${rel}`);
    if (!rel.endsWith('.js') && !rel.endsWith('.mjs')) continue;
    for (const spec of extractSpecifiers(readFileSync(abs, 'utf8'))) {
      const target = resolveSpecifier(spec, rel, imports);
      if (target) queue.push(target);
    }
  }
  for (const f of VENDORED) {
    assert.ok(
      seen.has(`vendor/${f}`),
      `模块图未闭合到 vendor/${f} (seen: ${[...seen].join(', ')})`,
    );
  }
});

test('无 vendored 文件残留 `from "http..."` / `import("http...")` 外部依赖', () => {
  for (const f of VENDORED) {
    if (!f.endsWith('.js')) continue;
    const src = read(`vendor/${f}`)
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/.*$/gm, '');
    assert.ok(!/from\s*['"]https?:\/\//.test(src), `vendor/${f} 含外部 from 'http...'`);
    assert.ok(
      !/import\(\s*['"]https?:\/\//.test(src),
      `vendor/${f} 含外部 import('http...')`,
    );
  }
});

test('vendored 文件 sha256 与 VENDOR.md 锁定一致', () => {
  const doc = read('vendor/VENDOR.md');
  const rows = [
    ...doc.matchAll(
      /\|\s*`([^`]+\.(?:js|wasm))`\s*\|[^|]*\|\s*`([0-9a-f]{64})`\s*\|\s*(\d+)\s*\|/g,
    ),
  ];
  assert.equal(rows.length, VENDORED.length, `VENDOR.md 应锁定 ${VENDORED.length} 个文件`);
  for (const [, relInVendor, sha, size] of rows) {
    const buf = readFileSync(resolve(VIEWER_DIR, 'vendor', relInVendor));
    assert.equal(buf.length, Number(size), `${relInVendor} 字节数漂移`);
    assert.equal(
      createHash('sha256').update(buf).digest('hex'),
      sha,
      `${relInVendor} sha256 与 VENDOR.md 不一致 (被改动或版本漂移)`,
    );
  }
});

test('vendored 第三方代码包含锁定的完整许可证', () => {
  for (const [relInVendor, sha] of Object.entries(LICENSES)) {
    const license = readFileSync(resolve(VIEWER_DIR, 'vendor', relInVendor));
    assert.equal(
      createHash('sha256').update(license).digest('hex'),
      sha,
      `${relInVendor} 缺失或与锁定的 npm 发布包不一致`,
    );
  }
});
