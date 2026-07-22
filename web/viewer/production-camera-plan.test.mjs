import assert from 'node:assert/strict';
import test from 'node:test';

let planModule;
try {
  planModule = await import('./production-camera-plan.mjs');
} catch (error) {
  planModule = { __loadError: error };
}

function subject() {
  assert.equal(
    planModule.__loadError,
    undefined,
    `production-camera-plan.mjs must load: ${planModule.__loadError?.message}`,
  );
  return planModule;
}

const GROUP_BUDGETS = {
  'ground-route': 72,
  'elevated-pedestrian': 48,
  'perimeter-inward': 32,
  'environment-corridor': 16,
  'audit-overview': 12,
};

const PLACED_GROUPS = [
  'ground-route',
  'perimeter-inward',
  'environment-corridor',
  'audit-overview',
];

function camera(groupId, number, sequenceIndex) {
  const auditOnly = groupId === 'audit-overview';
  return {
    camera_id: `camera-${groupId}-${String(number).padStart(3, '0')}`,
    group_id: groupId,
    sequence_index: sequenceIndex,
    topology_ref: `${groupId}-topology-${number % 3}`,
    arc_length_m: auditOnly ? null : number * 3.25,
    position_m: [sequenceIndex * 3.5, sequenceIndex * -1.25, auditOnly ? 190 : 1.6],
    look_at_m: [sequenceIndex * 3.5 + 1, sequenceIndex * -1.25 + 2, 1.6],
    eye_height_m: auditOnly ? 190 : 1.6,
    fov_x_deg: 65,
    intrinsics: {
      width_px: 1024,
      height_px: 576,
      fx: 784.1,
      fy: 784.1,
      cx: 512,
      cy: 288,
    },
    c2w_opencv: [
      [1, 0, 0, sequenceIndex * 3.5],
      [0, 1, 0, sequenceIndex * -1.25],
      [0, 0, 1, auditOnly ? 190 : 1.6],
      [0, 0, 0, 1],
    ],
    audit_only: auditOnly,
    disclosure: auditOnly
      ? 'audit-only-aerial-overview-not-a-pedestrian-viewpoint'
      : 'pedestrian-pose-derived-from-declared-topology',
  };
}

function incompletePlan() {
  const cameras = [];
  for (const groupId of PLACED_GROUPS) {
    for (let number = 1; number <= GROUP_BUDGETS[groupId]; number += 1) {
      cameras.push(camera(groupId, number, cameras.length + 1));
    }
  }
  const groupCoverage = PLACED_GROUPS.map((groupId) => ({
    group_id: groupId,
    camera_count: GROUP_BUDGETS[groupId],
    topology_ref_count: new Set(
      cameras
        .filter((item) => item.group_id === groupId)
        .map((item) => item.topology_ref),
    ).size,
  }));
  return {
    schema_version: 1,
    plan_schema: 'nantai.synthetic-village.production-camera-plan.v1',
    profile_id: 'synthetic-village-coverage-180-v1',
    journal_schema: 'nantai.synthetic-village.production-render-journal.v1',
    scene_plan_sha256: 'a'.repeat(64),
    elevated_topology_sha256: 'b'.repeat(64),
    coordinate_system: 'opencv-c2w-right-down-forward-meters',
    synthetic: true,
    geometry_trust: 'simplified-pbr-not-render-parity',
    verification_level: 'L2',
    declared_target_count: 180,
    camera_count: cameras.length,
    complete: false,
    cameras,
    group_coverage: groupCoverage,
    unplaced_groups: [{
      group_id: 'elevated-pedestrian',
      camera_count: 48,
      reason: 'verified topology exists but this incomplete fixture withheld camera placement',
    }],
    route_loops: [
      {
        loop_id: 'central-loop',
        ground_attachment_node_ids: ['central-ground-east', 'central-ground-west'],
        elevated_edge_ids: [
          'edge-central-gallery-001',
          'edge-central-ramp-001',
          'edge-central-stair-001',
        ],
        ground_connected: true,
      },
      {
        loop_id: 'upper-loop',
        ground_attachment_node_ids: ['upper-ground-east', 'upper-ground-west'],
        elevated_edge_ids: [
          'edge-upper-ascent-001',
          'edge-upper-descent-001',
          'edge-upper-gallery-001',
        ],
        ground_connected: true,
      },
      {
        loop_id: 'bridge-loop',
        ground_attachment_node_ids: ['bridge-ground-east', 'bridge-ground-west'],
        elevated_edge_ids: [
          'edge-bridge-ascent-001',
          'edge-bridge-descent-001',
          'edge-bridge-path-001',
        ],
        ground_connected: true,
      },
      {
        loop_id: 'valley-loop',
        ground_attachment_node_ids: ['valley-ground-north', 'valley-ground-south'],
        elevated_edge_ids: [
          'edge-valley-ascent-001',
          'edge-valley-descent-001',
          'edge-valley-path-001',
        ],
        ground_connected: true,
      },
    ],
    undelivered_requirements: [
      {
        requirement_id: 'req-3-front-back-facade-coverage',
        status: 'not-implemented',
        reason: 'front and reverse facade identity is not represented by machine evidence',
      },
      {
        requirement_id: 'req-5-pose-quality-fail-closed',
        status: 'not-implemented',
        reason: 'bad frame and valid pixel rejection has not run before production rendering',
      },
      {
        requirement_id: 'req-6-production-release-evidence',
        status: 'structurally-unreachable',
        reason: 'release evidence cannot exist before the withheld cameras are rendered',
      },
    ],
  };
}

test('missing or malformed production plans remain unknown', () => {
  const { isProductionCameraPlan, productionCameraPlanViewModel } = subject();

  assert.equal(isProductionCameraPlan(null), false);
  assert.equal(productionCameraPlanViewModel(null).status, 'unknown');
  assert.equal(
    productionCameraPlanViewModel({ plan_schema: 'named-only' }).status,
    'unknown',
  );
});

test('132 placed poses remain explicitly incomplete and are never 360 evidence', () => {
  const { isProductionCameraPlan, productionCameraPlanViewModel } = subject();
  const plan = incompletePlan();

  assert.equal(isProductionCameraPlan(plan), true);
  const model = productionCameraPlanViewModel(plan);
  assert.equal(model.status, 'incomplete');
  assert.equal(model.placed, 132);
  assert.equal(model.target, 180);
  assert.equal(model.unplaced, 48);
  assert.match(model.summary, /132\/180/);
  assert.match(model.unplaced_label, /elevated-pedestrian.*48/i);
  assert.match(model.requirements_label, /3 undelivered/i);
  assert.match(model.requirements_label, /structurally-unreachable/i);
  assert.match(model.summary, /not 360.*evidence/i);
  assert.doesNotMatch(
    [
      model.summary,
      model.unplaced_label,
      model.requirements_label,
      model.provenance_label,
    ].join(' '),
    /360.?ready|360.?coverage|production.?ready/i,
  );
});

test('count, completion, group and identity contradictions fail closed', () => {
  const { isProductionCameraPlan } = subject();
  const mutations = [
    (plan) => { plan.camera_count = 180; },
    (plan) => { plan.complete = true; },
    (plan) => { plan.unplaced_groups[0].camera_count = 47; },
    (plan) => { plan.group_coverage[0].camera_count = 71; },
    (plan) => { plan.cameras[1].camera_id = plan.cameras[0].camera_id; },
    (plan) => { plan.cameras[1].position_m = [...plan.cameras[0].position_m]; },
    (plan) => { plan.geometry_trust = 'metric-aligned'; },
    (plan) => { plan.plan_schema = 'production-camera-plan-by-name'; },
    (plan) => { delete plan.elevated_topology_sha256; },
    (plan) => { plan.route_loops[0].ground_connected = false; },
    (plan) => { plan.route_loops.pop(); },
    (plan) => {
      plan.route_loops[2].elevated_edge_ids[2] = 'edge-bridge-invented-999';
    },
  ];

  for (const mutate of mutations) {
    const plan = incompletePlan();
    mutate(plan);
    assert.equal(isProductionCameraPlan(plan), false);
  }
});

test('180 placed poses still disclose undelivered evidence instead of claiming readiness', () => {
  const { isProductionCameraPlan, productionCameraPlanViewModel } = subject();
  const plan = incompletePlan();
  const elevated = [];
  for (let number = 1; number <= GROUP_BUDGETS['elevated-pedestrian']; number += 1) {
    elevated.push(camera('elevated-pedestrian', number, plan.cameras.length + number));
  }
  plan.cameras.push(...elevated);
  plan.camera_count = 180;
  plan.complete = true;
  plan.unplaced_groups = [];
  plan.group_coverage.push({
    group_id: 'elevated-pedestrian',
    camera_count: 48,
    topology_ref_count: 3,
  });

  assert.equal(isProductionCameraPlan(plan), true);
  const model = productionCameraPlanViewModel(plan);
  assert.equal(model.status, 'evidence-incomplete');
  assert.match(model.summary, /180\/180.*evidence pending/i);
  assert.doesNotMatch(model.summary, /360.?ready|360.?coverage/i);
});
