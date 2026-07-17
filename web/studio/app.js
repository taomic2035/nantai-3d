import {
  normalizeSnapshot,
  normalizeStepState,
  viewerCapabilityTokens,
} from './model.mjs';
import { commandCapability } from './capabilities.mjs';
import {
  STUDIO_DAG,
  derivePrimaryAction,
  displayInputPath,
  preferredRunId,
  primaryNavigation,
} from './job-actions.mjs';
import { selectStudioAdapter } from './adapter-factory.mjs';
import { SCENARIO_NAMES } from './mock-adapter.mjs';
import { StudioViewerBridge } from './viewer-bridge.mjs';
import { loadOptionalCoverageAudit } from './coverage-audit-loader.mjs';
import { JobController } from './job-controller.mjs';
import {
  ingestConfirmationModel,
  validateIngestParameters,
} from './job-forms.mjs';

const VIEW_META = {
  sources: '图片 + 视频',
  align: 'Reconstruct evidence',
  reconstruct: '3DGS artifact',
  assets: 'validate + consume',
  compose: 'layout + world chunks',
  review: 'evidence join + export',
};
const STEP_DEFS = STUDIO_DAG.views.map((view) => ({
  key: view.stateKey,
  viewId: view.id,
  name: view.label,
  meta: VIEW_META[view.id],
  branch: view.branch,
}));

const SCENARIO_LABELS = {
  'ready-proxy': '正常 · proxy',
  empty: '空项目 · 只读',
  'missing-reconstruction': '待重建 · 只读',
  'align-warning': '坐标阻断',
  running: '任务运行中',
  failed: '输入失败',
  'assets-partial': '素材部分消费',
  'contract-complete-simulated': '契约完整 · 模拟',
};

const adapterSelection = await selectStudioAdapter();
const adapter = adapterSelection.adapter;
const adapterFallbackReason = adapterSelection.fallbackReason;
const serviceCapabilities = await adapter.loadCapabilities();
let snapshot;
let rawSnapshot;
let liveViewerCapabilities = null;
let selectedStep = 'review';
let selectedRunId = null;
let jobController = null;

const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '未知')
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

function chip(label, value, tone = '') {
  return `<span class="chip ${tone ? `chip-${tone}` : ''}"><b>${escapeHtml(label)}</b>${escapeHtml(value)}</span>`;
}

function facts(rows) {
  return `<dl class="fact-list">${rows.map(([label, value]) => (
    `<div class="fact-row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`
  )).join('')}</dl>`;
}

function statusFor(state) {
  if (state.execution === 'failed') return ['失败', 'failed'];
  if (state.execution === 'running') return ['运行中', 'running'];
  if (state.trust === 'untrusted') return ['不可信', 'warning'];
  if (state.trust === 'proxy') return ['Proxy', 'warning'];
  if (state.execution === 'succeeded') return ['完成', 'success'];
  return ['待处理', 'warning'];
}

function renderPipeline() {
  const list = byId('pipeline-list');
  list.replaceChildren();
  STEP_DEFS.forEach((definition) => {
    const normalized = normalizeStepState(snapshot.pipeline?.[definition.key]).state;
    const [status, tone] = statusFor(normalized);
    const li = document.createElement('li');
    li.className = 'pipeline-step';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'step-button';
    button.dataset.step = definition.key;
    button.dataset.testid = `pipeline-step-${definition.key}`;
    if (definition.key === selectedStep) button.setAttribute('aria-current', 'step');
    button.innerHTML = `
      <span class="step-number step-branch">${escapeHtml(definition.branch.slice(0, 1))}</span>
      <span class="step-copy"><span class="step-name">${definition.name}</span><span class="step-meta">${definition.meta}</span></span>
      <span class="step-status status-${tone}">${status}</span>`;
    button.addEventListener('click', () => selectStep(definition.key, button));
    li.append(button);
    list.append(li);
  });
}

function summaryCard(title, body, tone = '') {
  return `<section class="summary-card ${tone ? `is-${tone}` : ''}" tabindex="-1" data-error-summary="${tone === 'danger'}">
    <h3>${escapeHtml(title)}</h3><p>${escapeHtml(body)}</p></section>`;
}

function sourceInspector() {
  const sources = snapshot.sources ?? {};
  const failed = (sources.files ?? []).filter((item) => item.status === 'failed');
  const sourceCount = (sources.images ?? 0) + (sources.videos ?? 0);
  if (sourceCount === 0) {
    const ingest = commandCapability(serviceCapabilities, 'ingest');
    const inputPath = displayInputPath(snapshot.project?.storage);
    return `<section class="summary-card is-warning" tabindex="-1" data-source-empty-state>
      <h3>输入目录还没有可处理的媒体</h3>
      <p>将图片或视频放入 ${escapeHtml(inputPath)}。支持 JPG、PNG、WebP、HEIC、TIFF、MP4、MOV、AVI、MKV 与 WebM。</p>
    </section>
    <div class="inline-actions"><button class="button" type="button" id="rescan-sources"
      ${ingest.enabled ? '' : 'disabled'}>重新扫描</button></div>
    <span class="action-reason">${escapeHtml(ingest.enabled ? '' : ingest.reason)}</span>`;
  }
  return `${summaryCard(
    failed.length ? `${failed.length} 个输入需要处理` : '混合输入可进入配准',
    failed.length ? failed.map((item) => `${item.name}: ${item.reason}`).join('；')
      : '图片与视频已分组。重复检测尚未由核心提供，界面不伪造该数字。',
    failed.length ? 'danger' : '',
  )}
  <p class="eyebrow">输入摘要</p>
  ${facts([
    ['图片', `${sources.images ?? 0} 张`], ['视频', `${sources.videos ?? 0} 个`],
    ['抽取帧', `${sources.frames ?? 0} 帧`], ['拒绝', `${sources.rejected ?? 0}`],
    ['重复检测', sources.duplicate_detection ?? '未知'],
  ])}
  <p class="eyebrow">文件证据</p>
  ${(sources.files ?? []).map((item) => `<div class="evidence-card"><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.kind)} · ${escapeHtml(item.status)}${item.reason ? ` · ${escapeHtml(item.reason)}` : ''}</p></div>`).join('')}`;
}

function alignInspector() {
  const coordinate = snapshot.coordinate ?? {};
  const blocked = snapshot.derived.geometryUsability !== 'measurable';
  return `${summaryCard(
    blocked ? '只可预览，不可用于米制测量' : '坐标证据满足米制验收',
    blocked ? 'frame、单位或 metric evidence 不完整。Studio 已 fail closed，不从 COLMAP 名字推断尺度。'
      : '右手 ENU、Z-up、meters 与 metric evidence 均可验证。',
    blocked ? 'warning' : '',
  )}
  <p class="eyebrow">坐标契约</p>
  ${facts([
    ['Source frame', coordinate.source_frame], ['World frame', coordinate.world_frame],
    ['单位', coordinate.units], ['手性', coordinate.handedness], ['Up axis', coordinate.up_axis],
    ['注册覆盖', `${coordinate.registered_images ?? 0} / ${coordinate.total_images ?? 0}`],
    ['变换次数', (coordinate.transform_chain ?? []).length],
    ['Metric evidence', (coordinate.metric_evidence ?? []).join(', ') || '无'],
  ])}`;
}

function reconstructInspector() {
  const reconstruction = snapshot.reconstruction ?? {};
  return `${summaryCard(
    snapshot.derived.renderFidelity === 'dc-point-preview' ? '当前只是 DC point preview' : 'Gaussian Splat renderer contract 可用',
    `UI 依据 artifact attributes 与 viewer capabilities 交叉判定：${snapshot.derived.renderFidelity}。`,
    snapshot.derived.renderFidelity === 'dc-point-preview' ? 'warning' : '',
  )}
  <p class="eyebrow">重建产物</p>
  ${facts([
    ['请求引擎', reconstruction.requested_engine], ['实际引擎', reconstruction.actual_engine],
    ['Synthetic', String(reconstruction.synthetic)], ['Gaussian count', reconstruction.gaussian_count ?? 0],
    ['Geometry evidence', reconstruction.geometry_usability ?? 'unknown'],
    ['SH degree', reconstruction.sh_degree ?? '未知'], ['Viewer fidelity', snapshot.derived.renderFidelity],
    ['Artifact', reconstruction.artifact?.uri ?? '无'], ['SHA-256', reconstruction.artifact?.sha256 ?? '无'],
  ])}`;
}

function stitchInspector() {
  const stitch = snapshot.stitch ?? {};
  return `${summaryCard('图像与视频会话已进入同一声明 frame', '拼接成功不自动证明米制；最终可用性仍由 Align 证据决定。')}
  <p class="eyebrow">拼接与可变清晰</p>
  ${facts([
    ['会话', stitch.sessions ?? 0], ['Overlap', `${Math.round((stitch.overlap_ratio ?? 0) * 100)}%`],
    ['去重体素', `${stitch.dedup_voxel_m ?? 0} m`], ['区域替换', stitch.replacement_regions ?? 0],
    ['LOD 0 / 1 / 2', (stitch.lod_counts ?? []).join(' / ') || '无'],
  ])}`;
}

function assetsInspector() {
  const assets = snapshot.assets ?? {};
  const currentHandoff = assets.current_handoff;
  const validation = commandCapability(serviceCapabilities, 'validate-assets');
  const canValidate = validation.enabled && Boolean(currentHandoff?.id);
  const validationReason = currentHandoff?.id
    ? validation.reason
    : '当前 Registry 未与唯一 handoff 的完整 ID、SHA-256 和 kind 集合精确匹配。';
  const cards = (assets.items ?? []).map((item) => `<article class="asset-card">
    <b title="${escapeHtml(item.id)}">${escapeHtml(item.id)}</b>
    <span>${escapeHtml(item.kind)} · v${escapeHtml(item.version)}</span>
    <span class="${item.consumed ? 'consumed' : 'blocked'}">${item.validated ? '格式 PASS' : '待验证'} · ${item.consumed ? '世界已消费' : '渲染器未消费'}</span>
  </article>`).join('');
  return `${summaryCard(
    `${assets.registered ?? 0} 个已注册，${assets.consumed ?? 0} 个有消费证据`,
    (assets.blocked ?? 0)
      ? `${assets.blocked} 个素材仍阻断。注册成功与世界消费是两个独立状态。`
      : '所有已注册素材都有世界消费证据；替换仍需通过版本与哈希门禁。',
    (assets.blocked ?? 0) ? 'warning' : '',
  )}
  <p class="eyebrow">${escapeHtml(currentHandoff?.id ?? '来源 handoff 未匹配')}</p>
  ${facts([
    ['Registry revision', assets.registry_revision ?? 'unknown'],
    ['Manifest SHA-256', currentHandoff?.manifest_sha256 ?? '无可验证匹配'],
    ['视觉源 handoff', currentHandoff?.source_handoff ?? '未声明'],
  ])}
  <div class="asset-grid">${cards}</div>
  ${currentHandoff?.preview_uri
    ? `<img class="contact-sheet" src="${escapeHtml(currentHandoff.preview_uri)}" alt="${escapeHtml(currentHandoff.id)} 素材接触表">`
    : ''}
  <div class="inline-actions"><button class="button" type="button" id="validate-assets"
    ${canValidate ? '' : 'disabled'} aria-describedby="validate-assets-reason">复验 ${escapeHtml(
      currentHandoff?.id ?? '当前素材',
    )} · ${escapeHtml(currentHandoff?.item_count ?? assets.registered ?? 0)} 项</button></div>
  <span class="action-reason" id="validate-assets-reason">${escapeHtml(
    canValidate ? '' : validationReason,
  )}</span>`;
}

function reviewInspector() {
  const derived = snapshot.derived;
  const blocked = derived.geometryUsability !== 'measurable';
  return `${summaryCard(
    blocked ? '允许导出 Proxy，阻断米制发布' : '可进入可测量产物冻结',
    blocked ? '当前是模拟数据或缺少 metric evidence。预览仍可使用，但不会显示“真实/米制/完整”。'
      : '坐标、artifact hash 与 renderer capability 已通过 gate。',
    blocked ? 'warning' : '',
  )}
  <p class="eyebrow">发布前 Gate</p>
  ${facts([
    ['Geometry', derived.geometryUsability], ['Trust', derived.trust],
    ['Render fidelity', derived.renderFidelity], ['Adapter', snapshot.adapter.kind],
    ['Artifact immutable', String(snapshot.reconstruction?.artifact?.immutable ?? false)],
    ['可导出格式', blocked ? 'proxy-ply' : 'proxy-ply / 3dgs-ply'],
  ])}
  <p class="eyebrow">诊断</p>
  ${derived.diagnostics.map((item) => `<div class="evidence-card"><p>${escapeHtml(item)}</p></div>`).join('') || '<div class="evidence-card"><p>无阻断诊断。</p></div>'}`;
}

function renderInspector() {
  const definition = STEP_DEFS.find((item) => item.key === selectedStep);
  byId('inspector-kicker').textContent = `VIEW · ${definition.branch.toUpperCase()}`;
  byId('inspector-title').textContent = definition.name;
  const renderers = {
    sources: sourceInspector, align: alignInspector, reconstruct: reconstructInspector,
    stitch: stitchInspector, assets: assetsInspector, review: reviewInspector,
  };
  byId('inspector-content').innerHTML = renderers[selectedStep]();
  if (selectedStep === 'sources' && !byId('rescan-sources')) {
    const ingest = commandCapability(serviceCapabilities, 'ingest');
    const actions = document.createElement('div');
    actions.className = 'inline-actions';
    actions.innerHTML = `<button class="button" type="button" id="rescan-sources"
      ${ingest.enabled ? '' : 'disabled'}>处理输入素材</button>
      <span class="action-reason">${escapeHtml(ingest.enabled ? '' : ingest.reason)}</span>`;
    byId('inspector-content').append(actions);
  }
  byId('rescan-sources')?.addEventListener('click', openIngestConfirmation);
  byId('validate-assets')?.addEventListener('click', async () => {
    const validation = commandCapability(serviceCapabilities, 'validate-assets');
    const currentHandoff = snapshot.assets?.current_handoff;
    if (!validation.enabled || !currentHandoff?.id) return;
    try {
      const report = await adapter.validateAssetCandidate({ asset_id: currentHandoff.id });
      announce(`素材验证 ${report.passed ? '通过' : '失败'}`);
    } catch (error) {
      announce(`素材验证不可用：${error.message}`);
    }
  });
}

function renderProvenance() {
  const reconstruction = snapshot.reconstruction ?? {};
  const coordinate = snapshot.coordinate ?? {};
  const derived = snapshot.derived;
  const pieces = [
    chip('actual', reconstruction.actual_engine, reconstruction.synthetic ? 'warning' : ''),
    chip('frame', coordinate.source_frame, coordinate.source_frame === 'sfm-local' ? 'warning' : ''),
    chip('units', coordinate.units, coordinate.units !== 'meters' ? 'danger' : ''),
    chip('geometry', derived.geometryUsability, derived.geometryUsability !== 'measurable' ? 'warning' : 'success'),
    chip('fidelity', derived.renderFidelity, derived.renderFidelity === 'dc-point-preview' ? 'warning' : 'success'),
  ];
  byId('provenance-bar').innerHTML = pieces.join('');
}

function selectStep(key, focusTarget = null) {
  selectedStep = key;
  renderPipeline();
  renderInspector();
  if (window.matchMedia('(max-width: 1099px)').matches) byId('inspector').classList.add('is-open');
  focusTarget?.focus();
}

function renderTopbar() {
  byId('project-meta').textContent = `${snapshot.project?.name ?? '未命名项目'} · ${snapshot.project?.storage ?? '未知位置'}`;
  const adapterBadge = byId('adapter-badge');
  adapterBadge.textContent = snapshot.adapter.kind === 'mock'
    ? '模拟数据' : snapshot.adapter.kind === 'local' ? '本地管线' : '适配器未知';
  adapterBadge.classList.toggle('badge-mock', snapshot.adapter.kind === 'mock');
  adapterBadge.classList.toggle('badge-local', snapshot.adapter.kind === 'local');
  adapterBadge.title = adapterFallbackReason ?? '已连接项目真值源';
  byId('freshness-badge').textContent = `产物 · ${snapshot.pipeline?.review?.freshness ?? 'unknown'}`;
  const modeLabel = serviceCapabilities.mode === 'read-write' ? '可写' : '只读';
  byId('capability-summary').textContent = `服务模式 · ${modeLabel} · ${serviceCapabilities.reason}`;
  byId('capability-summary').title = serviceCapabilities.reason;
  const watermark = document.querySelector('.stage-watermark');
  if (watermark) {
    const simulated = snapshot.adapter.kind === 'mock' || snapshot.reconstruction?.synthetic === true;
    watermark.hidden = !simulated;
    watermark.textContent = snapshot.adapter.kind === 'mock' ? 'SIMULATED STATE' : 'SYNTHETIC ARTIFACT';
  }
  const action = derivePrimaryAction(snapshot, serviceCapabilities);
  const button = byId('primary-action');
  button.dataset.action = action.id;
  button.textContent = action.label;
  button.disabled = !action.enabled;
  button.title = action.enabled ? '' : action.reason;
  byId('primary-action-reason').textContent = action.enabled ? '' : action.reason;
}

function renderJobs(runs) {
  const list = byId('run-list');
  list.replaceChildren();
  selectedRunId = preferredRunId(runs, selectedRunId, snapshot.active_run?.id);
  runs.forEach((run) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `run-item ${run.id === selectedRunId ? 'is-selected' : ''}`;
    button.innerHTML = `<b>${escapeHtml(run.command)} · ${escapeHtml(run.status)}</b><span>${escapeHtml(run.id)} · ${escapeHtml(run.adapter_kind)}</span>`;
    button.addEventListener('click', () => { selectedRunId = run.id; renderJobs(runs); });
    list.append(button);
  });
  const selected = runs.find((run) => run.id === selectedRunId);
  const timeline = byId('event-timeline');
  const events = selected?.events ?? [];
  timeline.innerHTML = events.length ? events.map((event) => `<article class="event-row">
    <time>#${escapeHtml(event.seq)}</time><span>${escapeHtml(event.phase ?? 'event')}</span><p>${escapeHtml(event.message)}</p>
  </article>`).join('') : '<p class="empty-events">该运行还没有结构化事件。</p>';
  byId('drawer-summary').textContent = `${runs.length} 次运行 · ${selected?.status ?? '无记录'}`;
  const dot = document.querySelector('.run-dot');
  if (dot) dot.style.background = selected?.status === 'failed' ? 'var(--red)'
    : selected?.status === 'running' ? 'var(--cyan)' : 'var(--green)';
}

async function refreshJobs() {
  if (jobController) {
    await jobController.pollOnce({ reschedule: false });
    return;
  }
  const { items } = await adapter.listRuns();
  renderJobs(items);
}

function announce(message) {
  byId('live-region').textContent = '';
  requestAnimationFrame(() => { byId('live-region').textContent = message; });
}

async function loadScenario({ focusError = false, refreshRuns = true } = {}) {
  rawSnapshot = await adapter.loadProject();
  const evidenced = structuredClone(rawSnapshot);
  if (liveViewerCapabilities && evidenced.reconstruction) {
    evidenced.reconstruction.renderer_capabilities = viewerCapabilityTokens(
      liveViewerCapabilities,
    );
  }
  snapshot = normalizeSnapshot(evidenced);
  renderTopbar();
  renderPipeline();
  renderInspector();
  renderProvenance();
  if (refreshRuns) await refreshJobs();
  if (focusError) {
    byId('inspector-content').querySelector('[data-error-summary="true"]')?.focus();
  }
}

function setupScenarioControl() {
  const select = byId('scenario-select');
  if (adapter.kind !== 'mock') {
    const option = document.createElement('option');
    option.value = 'local';
    option.textContent = '本地项目真值';
    select.append(option);
    select.disabled = true;
    return;
  }
  SCENARIO_NAMES.forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = SCENARIO_LABELS[name];
    select.append(option);
  });
  select.addEventListener('change', async () => {
    const wasRunning = snapshot?.active_run?.status === 'running';
    adapter.setScenario(select.value);
    if (select.value === 'failed') selectedStep = 'sources';
    else if (select.value === 'align-warning') selectedStep = 'align';
    else if (select.value === 'assets-partial') selectedStep = 'assets';
    await loadScenario({ focusError: wasRunning || select.value === 'failed' });
    announce(`已切换模拟场景：${SCENARIO_LABELS[select.value]}`);
  });
}

function setDrawerOpen(open, { focus = false } = {}) {
  const drawer = byId('job-drawer');
  const toggle = byId('drawer-toggle');
  drawer.classList.toggle('is-open', open);
  toggle.setAttribute('aria-expanded', String(open));
  byId('drawer-title').textContent = open ? '任务记录 · 已展开' : '任务记录';
  if (focus) requestAnimationFrame(() => toggle.focus());
}

function setupDrawer() {
  const drawer = byId('job-drawer');
  const toggle = byId('drawer-toggle');
  toggle.addEventListener('click', () => {
    setDrawerOpen(!drawer.classList.contains('is-open'));
  });
}

function setupPrimaryAction() {
  byId('primary-action').addEventListener('click', async () => {
    const action = derivePrimaryAction(snapshot, serviceCapabilities);
    if (!action.enabled) return;
    const intent = primaryNavigation(action);
    if (action.id === 'reconnect') {
      announce('当前 adapter 无自动重连能力；请确认本地 Studio 服务仍在运行。');
      return;
    }
    if (intent.step) selectStep(intent.step);
    if (intent.openDrawer) setDrawerOpen(true, { focus: intent.focus === 'drawer' });
    if (intent.focus === 'source-empty') {
      requestAnimationFrame(() => {
        byId('inspector-content').querySelector('[data-source-empty-state]')?.focus();
      });
    }
    if (intent.submitCommand) {
      try {
        if (intent.submitCommand === 'ingest') openIngestConfirmation();
        await loadScenario();
      } catch (error) {
        announce(`无法启动重建：${error.message}`);
      }
    }
  });
}

function openIngestConfirmation() {
  const capability = commandCapability(serviceCapabilities, 'ingest');
  if (!capability.enabled || adapter.kind !== 'local') return;
  const model = ingestConfirmationModel({
    inputPath: displayInputPath(snapshot.project?.storage),
  });
  byId('ingest-input-path').textContent = model.inputPath;
  byId('ingest-staging-path').textContent = model.stagingPath;
  byId('ingest-impact').textContent = model.impact;
  byId('ingest-cancel-notice').textContent = model.cancelNotice;
  Object.entries(model.parameters).forEach(([key, value]) => {
    byId(`ingest-${key}`).value = value;
  });
  byId('ingest-dialog').showModal();
  requestAnimationFrame(() => byId('ingest-fps').focus());
}

function setupIngestDialog() {
  const dialog = byId('ingest-dialog');
  byId('ingest-dialog-cancel').addEventListener('click', () => dialog.close());
  byId('ingest-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = byId('ingest-submit');
    submit.disabled = true;
    try {
      const parameters = validateIngestParameters({
        fps: Number(byId('ingest-fps').value),
        max_frames: Number(byId('ingest-max_frames').value),
        blur_threshold: Number(byId('ingest-blur_threshold').value),
        max_long_edge: Number(byId('ingest-max_long_edge').value),
      });
      const result = await adapter.startJob('ingest', parameters);
      selectedStep = 'sources';
      selectedRunId = result.run.id;
      dialog.close();
      setDrawerOpen(true);
      await jobController.pollOnce({ reschedule: false });
      announce('真实本地素材处理已提交；此里程碑不支持中途取消。');
    } catch (error) {
      announce(`无法提交素材处理：${error.message}`);
    } finally {
      submit.disabled = false;
    }
  });
}

function setupB1PrimaryAction() {
  byId('primary-action').addEventListener('click', () => {
    const action = derivePrimaryAction(snapshot, serviceCapabilities);
    if (!action.enabled) return;
    const intent = primaryNavigation(action);
    if (intent.step) selectStep(intent.step);
    if (intent.openDrawer) setDrawerOpen(true, { focus: intent.focus === 'drawer' });
    if (intent.focus === 'source-empty') {
      requestAnimationFrame(() => {
        byId('inspector-content').querySelector('[data-source-empty-state]')?.focus();
      });
    }
    if (intent.submitCommand === 'ingest') openIngestConfirmation();
  });
}

function setupViewerBridge() {
  const frame = byId('viewer-frame');
  const status = byId('viewer-status');
  let coverageProbe = null;
  const viewerButtons = [...document.querySelectorAll('[data-viewer-command]')];
  viewerButtons.forEach((button) => { button.disabled = true; });
  const bridge = new StudioViewerBridge({
    frameWindow: frame.contentWindow,
    timeoutMs: 8000,
    onStatus: (next, capabilities) => {
      status.classList.toggle('is-ready', next === 'ready');
      status.lastChild.textContent = next === 'ready'
        ? `Viewer · ${capabilities.renderer?.fidelity ?? 'capability ready'}`
        : 'Viewer · degraded';
      viewerButtons.forEach((control) => {
        control.disabled = !bridge.supports(control.dataset.viewerCommand);
        control.title = control.disabled ? `Viewer 不支持 ${control.dataset.viewerCommand}` : '';
      });
      if (next === 'ready') {
        liveViewerCapabilities = capabilities;
        if (rawSnapshot) {
          const evidenced = structuredClone(rawSnapshot);
          if (evidenced.reconstruction) {
            evidenced.reconstruction.renderer_capabilities = viewerCapabilityTokens(capabilities);
          }
          snapshot = normalizeSnapshot(evidenced);
          renderTopbar();
          renderPipeline();
          renderInspector();
          renderProvenance();
        }
        if (!coverageProbe) {
          coverageProbe = loadOptionalCoverageAudit({ bridge })
            .then((result) => {
              if (result.status === 'loaded') {
                announce(`覆盖审计已加载：${result.coverage.status}`);
              }
              return result;
            })
            .catch((error) => {
              announce(`覆盖审计加载失败：${error.message}`);
            })
            .finally(() => {
              coverageProbe = null;
            });
        }
      }
    },
  });
  bridge.start();
  frame.src = frame.dataset.src;

  byId('reset-camera').addEventListener('click', () => {
    bridge.command('resetCamera').catch((error) => announce(error.message));
  });
  const jumpToCoordinates = () => {
    if (byId('coord-jump-btn').disabled) return;
    const east = parseFloat(byId('coord-east').value);
    const north = parseFloat(byId('coord-north').value);
    const up = parseFloat(byId('coord-up').value);
    if (![east, north, up].every(Number.isFinite)) {
      announce('坐标必须是有限数字');
      return;
    }
    bridge.command('setCameraPose', { position: { east, north, up } })
      .catch((error) => announce(error.message));
  };
  byId('coord-jump-btn').addEventListener('click', jumpToCoordinates);
  ['coord-east', 'coord-north', 'coord-up'].forEach((id) => {
    byId(id).addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        jumpToCoordinates();
      }
    });
  });
  byId('lod-select').addEventListener('change', (event) => {
    const value = event.target.value;
    bridge.command('setLOD', { lod: value === 'auto' ? null : Number(value) })
      .catch((error) => announce(error.message));
  });
  document.querySelectorAll('[data-layer]').forEach((button) => {
    button.addEventListener('click', () => {
      const active = button.classList.toggle('is-active');
      button.setAttribute('aria-pressed', String(active));
      bridge.command('setLayer', { layer: button.dataset.layer, visible: active })
        .catch((error) => announce(error.message));
    });
  });
}

byId('inspector-close').addEventListener('click', () => {
  byId('inspector').classList.remove('is-open');
  document.querySelector(`[data-step="${selectedStep}"]`)?.focus();
});
document.querySelector('.skip-link')?.addEventListener('click', () => {
  requestAnimationFrame(() => byId('stage').focus());
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && byId('inspector').classList.contains('is-open')) {
    byId('inspector-close').click();
  }
});

setupScenarioControl();
setupDrawer();
setupB1PrimaryAction();
setupIngestDialog();
setupViewerBridge();
jobController = new JobController({
  adapter,
  onUpdate: (runs) => renderJobs(runs),
  onTerminal: async () => {
    await loadScenario({ refreshRuns: false });
    byId('viewer-frame').contentWindow?.location.reload();
  },
});
await loadScenario();
jobController.start();
