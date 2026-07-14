import { RunLedger } from './ledger.mjs';

const CORE_ATTRIBUTES = [
  'x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity',
  'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3',
];

function baseSnapshot() {
  return {
    schema_version: 2,
    project: {
      id: 'nantai-demo', name: '南台村 · 接管验证',
      updated_at: '2026-07-14T12:00:00+08:00', storage: './',
    },
    adapter: { kind: 'mock', connected: true },
    sources: {
      images: 5, videos: 1, frames: 8, rejected: 0,
      files: [
        { name: 'photo_00.jpg', kind: 'image', status: 'accepted' },
        { name: 'drone_orbit.mp4', kind: 'video', status: 'accepted', duration_s: 2 },
      ],
      duplicate_detection: 'not-supported',
    },
    coordinate: {
      source_frame: 'world-enu', world_frame: 'world-enu', units: 'meters',
      handedness: 'right', up_axis: 'z', transform_chain: [],
      metric_evidence: ['synthetic-layout-v1'], registered_images: 13, total_images: 13,
    },
    reconstruction: {
      requested_engine: 'mock', actual_engine: 'mock-proxy', synthetic: true,
      geometry_usability: 'preview-proxy',
      artifact: {
        id: 'artifact-recon-mock', kind: '3dgs-ply', uri: '../data/recon/recon_full.ply',
        sha256: '0'.repeat(64), bytes: 0, evidence_status: 'fixture-only',
        created_at: '2026-07-14T12:00:00+08:00', immutable: false,
      },
      attributes: CORE_ATTRIBUTES,
      sh_degree: 0,
      renderer_capabilities: ['dc-color'],
      gaussian_count: 7700, lod: [0, 1, 2],
    },
    stitch: {
      sessions: 2, overlap_ratio: 0.31, dedup_voxel_m: 0.1,
      replacement_regions: 1, lod_counts: [616, 2310, 7700],
    },
    assets: {
      registered: 11, consumed: 11, blocked: 0, registry_revision: 'mock-r2',
      items: [
        ...['house_barn_01', 'house_stone_01', 'house_thatch_01', 'house_wood_01', 'house_wood_02']
          .map((id) => ({ id, kind: 'building', version: 1, validated: true, consumed: true })),
        ...['stone_lamp_01', 'stone_wall_01', 'fence_wood_01']
          .map((id) => ({ id, kind: 'prop', version: 1, validated: true, consumed: true })),
        ...['tree_bamboo_01', 'tree_broadleaf_01', 'tree_pine_01']
          .map((id) => ({ id, kind: 'vegetation', version: 1, validated: true, consumed: true })),
      ],
    },
    pipeline: {
      sources: { availability: 'ready', execution: 'succeeded', freshness: 'current', preview: 'ready', trust: 'proxy' },
      align: { availability: 'ready', execution: 'succeeded', freshness: 'current', preview: 'ready', trust: 'proxy' },
      reconstruct: { availability: 'ready', execution: 'succeeded', freshness: 'current', preview: 'ready', trust: 'proxy' },
      stitch: { availability: 'ready', execution: 'succeeded', freshness: 'current', preview: 'ready', trust: 'proxy' },
      assets: { availability: 'ready', execution: 'succeeded', freshness: 'current', preview: 'ready', trust: 'proxy' },
      review: { availability: 'ready', execution: 'idle', freshness: 'current', preview: 'ready', trust: 'proxy' },
    },
    active_run: { id: 'run-mock-001', status: 'succeeded' },
  };
}

function scenario(name) {
  const snapshot = baseSnapshot();
  if (name === 'align-warning') {
    Object.assign(snapshot.coordinate, {
      source_frame: 'sfm-local', world_frame: 'world-enu', units: 'arbitrary',
      up_axis: 'unknown', transform_chain: [], metric_evidence: [],
    });
    snapshot.pipeline.align.trust = 'untrusted';
    snapshot.pipeline.review.trust = 'untrusted';
  } else if (name === 'running') {
    snapshot.active_run = { id: 'run-mock-running', status: 'running' };
    snapshot.pipeline.reconstruct.execution = 'running';
    snapshot.pipeline.reconstruct.preview = 'loading';
    snapshot.pipeline.reconstruct.freshness = 'stale';
  } else if (name === 'failed') {
    snapshot.active_run = { id: 'run-mock-failed', status: 'failed' };
    snapshot.pipeline.sources.execution = 'failed';
    snapshot.pipeline.sources.freshness = 'stale';
    snapshot.pipeline.sources.preview = 'degraded';
    snapshot.pipeline.sources.trust = 'untrusted';
    snapshot.sources.rejected = 1;
    snapshot.sources.files.push({ name: 'broken_clip.mp4', kind: 'video', status: 'failed', reason: '0 frames decoded' });
  } else if (name === 'assets-partial') {
    snapshot.assets.consumed = 8;
    snapshot.assets.blocked = 3;
    snapshot.assets.registry_revision = 'mock-partial-r1';
    snapshot.assets.items
      .filter((item) => item.kind === 'vegetation')
      .forEach((item) => { item.consumed = false; });
  } else if (name === 'contract-complete-simulated') {
    snapshot.reconstruction.renderer_capabilities = [
      'dc-color', 'anisotropic-covariance', 'alpha-composite',
    ];
  }
  return snapshot;
}

export const SCENARIO_NAMES = Object.freeze([
  'ready-proxy', 'align-warning', 'running', 'failed',
  'assets-partial', 'contract-complete-simulated',
]);

export class MockStudioAdapter {
  constructor({ storage = globalThis.localStorage } = {}) {
    this.kind = 'mock';
    this.scenarioName = 'ready-proxy';
    this.ledger = new RunLedger({ storage });
    this.listeners = new Set();
    if (this.ledger.listRuns().length === 0) {
      this.ledger.upsertRun({
        id: 'run-mock-001', command: 'reconstruct', status: 'succeeded', retry_of: null,
        input_summary: { images: 5, videos: 1 }, parameters: { engine: 'mock' },
        adapter_kind: 'mock', started_at: '2026-07-14T04:00:00.000Z',
        finished_at: '2026-07-14T04:00:04.000Z', artifact_ids: ['artifact-recon-mock'],
        last_event_id: 'evt-done', events: [
          { id: 'evt-start', seq: 1, phase: 'ingest', progress: 0.1, message: '读取 5 张图片与 1 个视频' },
          { id: 'evt-align', seq: 2, phase: 'align', progress: 0.42, message: '建立 synthetic ENU proxy frame' },
          { id: 'evt-done', seq: 3, phase: 'export', progress: 1, message: '导出 3 级 LOD proxy' },
        ],
      });
    }
  }

  setScenario(name) {
    if (!SCENARIO_NAMES.includes(name)) throw new Error(`unknown scenario: ${name}`);
    this.scenarioName = name;
    this.#emit({ type: 'snapshot', snapshot: scenario(name) });
  }

  async loadProject() { return scenario(this.scenarioName); }
  async listRuns() { return { items: this.ledger.listRuns(), cursor: 'mock-local' }; }
  subscribe(_cursor, listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  #emit(event) { for (const listener of this.listeners) listener(event); }

  async startJob(command, payload = {}) {
    const id = `run-mock-${Date.now()}`;
    const record = this.ledger.upsertRun({
      id, command, status: 'running', retry_of: payload.retry_of ?? null,
      input_summary: { images: 5, videos: 1 }, parameters: payload,
      adapter_kind: 'mock', started_at: new Date().toISOString(), finished_at: null,
      artifact_ids: [], last_event_id: null, events: [],
    });
    this.scenarioName = 'running';
    this.#emit({ type: 'run', run: record });
    return record;
  }

  async cancelJob(runId) {
    const run = this.ledger.getRun(runId);
    if (!run) throw new Error(`unknown run: ${runId}`);
    return this.ledger.upsertRun({ ...run, status: 'canceled', finished_at: new Date().toISOString() });
  }

  async validateAssetCandidate(candidate) {
    return {
      candidate, passed: true, active_revision: 'mock-r1',
      commit_token: `mock-token-${candidate.asset_id}`, expires_at: '2099-01-01T00:00:00Z',
      rules: [{ id: 'ply-schema', passed: true }, { id: 'bounds', passed: true }],
    };
  }

  async commitAssetVersion(assetId, expectedVersion, token) {
    if (token !== `mock-token-${assetId}`) throw new Error('invalid mock commit token');
    return { revision: 'mock-r2', asset_id: assetId, version: expectedVersion + 1 };
  }

  async getConsumptionReport() { return scenario(this.scenarioName).assets; }
  async freezeExport() { throw new Error('mock adapter cannot freeze a real export'); }
  async getPreviewUrl() { return '../viewer/index.html?embed=1'; }
}
