export const PRODUCTION_CAMERA_PLAN_SCHEMA =
  'nantai.synthetic-village.production-camera-plan.v1';

export const PRODUCTION_PLAN_STATUS_COLORS = Object.freeze({
  unknown: '#9aa4ad',
  incomplete: '#ffbf47',
  'evidence-incomplete': '#ffbf47',
  planned: '#7fff7f',
});

const GROUP_BUDGETS = Object.freeze({
  'ground-route': 72,
  'elevated-pedestrian': 48,
  'perimeter-inward': 32,
  'environment-corridor': 16,
  'audit-overview': 12,
});
const GROUP_IDS = new Set(Object.keys(GROUP_BUDGETS));
const REQUIREMENT_STATUSES = new Set([
  'not-implemented',
  'structurally-unreachable',
]);
const SHA256 = /^[0-9a-f]{64}$/;
const CAMERA_ID = new RegExp(
  '^camera-(?:ground-route|elevated-pedestrian|perimeter-inward'
  + '|environment-corridor|audit-overview)-[0-9]{3}$',
);
const ROUTE_LOOP_CONTRACT = Object.freeze([
  Object.freeze({
    loop_id: 'central-loop',
    ground_attachment_node_ids: Object.freeze([
      'central-ground-east',
      'central-ground-west',
    ]),
    elevated_edge_ids: Object.freeze([
      'edge-central-gallery-001',
      'edge-central-ramp-001',
      'edge-central-stair-001',
    ]),
  }),
  Object.freeze({
    loop_id: 'upper-loop',
    ground_attachment_node_ids: Object.freeze([
      'upper-ground-east',
      'upper-ground-west',
    ]),
    elevated_edge_ids: Object.freeze([
      'edge-upper-ascent-001',
      'edge-upper-descent-001',
      'edge-upper-gallery-001',
    ]),
  }),
]);

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function finiteVector(value, length) {
  return (
    Array.isArray(value)
    && value.length === length
    && value.every(Number.isFinite)
  );
}

function finiteMatrix4(value) {
  return (
    Array.isArray(value)
    && value.length === 4
    && value.every((row) => finiteVector(row, 4))
  );
}

function validIntrinsics(value) {
  return (
    isRecord(value)
    && Number.isSafeInteger(value.width_px)
    && value.width_px > 0
    && Number.isSafeInteger(value.height_px)
    && value.height_px > 0
    && ['fx', 'fy', 'cx', 'cy'].every((key) => Number.isFinite(value[key]))
    && value.fx > 0
    && value.fy > 0
  );
}

function validCamera(camera, expectedSequence) {
  return (
    isRecord(camera)
    && GROUP_IDS.has(camera.group_id)
    && typeof camera.camera_id === 'string'
    && CAMERA_ID.test(camera.camera_id)
    && camera.camera_id.startsWith(`camera-${camera.group_id}-`)
    && camera.sequence_index === expectedSequence
    && typeof camera.topology_ref === 'string'
    && camera.topology_ref.length > 0
    && (camera.arc_length_m === null || Number.isFinite(camera.arc_length_m))
    && finiteVector(camera.position_m, 3)
    && finiteVector(camera.look_at_m, 3)
    && Number.isFinite(camera.eye_height_m)
    && camera.eye_height_m > 0
    && Number.isFinite(camera.fov_x_deg)
    && camera.fov_x_deg > 0
    && camera.fov_x_deg < 180
    && validIntrinsics(camera.intrinsics)
    && finiteMatrix4(camera.c2w_opencv)
    && typeof camera.audit_only === 'boolean'
    && (camera.group_id === 'audit-overview') === camera.audit_only
    && typeof camera.disclosure === 'string'
    && camera.disclosure.length >= 10
  );
}

function validUnplacedGroups(value, cameraCounts) {
  if (!Array.isArray(value)) return false;
  const seen = new Set();
  for (const row of value) {
    if (
      !isRecord(row)
      || !GROUP_IDS.has(row.group_id)
      || seen.has(row.group_id)
      || !Number.isSafeInteger(row.camera_count)
      || row.camera_count < 1
      || typeof row.reason !== 'string'
      || row.reason.length < 20
    ) return false;
    seen.add(row.group_id);
  }
  const unplaced = new Map(value.map((row) => [row.group_id, row.camera_count]));
  return [...GROUP_IDS].every(
    (groupId) => (cameraCounts.get(groupId) ?? 0) + (unplaced.get(groupId) ?? 0)
      === GROUP_BUDGETS[groupId],
  );
}

function validGroupCoverage(value, cameras, cameraCounts) {
  if (!Array.isArray(value)) return false;
  const seen = new Set();
  for (const row of value) {
    if (
      !isRecord(row)
      || !GROUP_IDS.has(row.group_id)
      || seen.has(row.group_id)
      || !Number.isSafeInteger(row.camera_count)
      || row.camera_count < 1
      || !Number.isSafeInteger(row.topology_ref_count)
      || row.topology_ref_count < 1
      || row.camera_count !== (cameraCounts.get(row.group_id) ?? 0)
    ) return false;
    const topologyCount = new Set(
      cameras
        .filter((camera) => camera.group_id === row.group_id)
        .map((camera) => camera.topology_ref),
    ).size;
    if (row.topology_ref_count !== topologyCount) return false;
    seen.add(row.group_id);
  }
  const placedGroups = new Set(
    [...cameraCounts].filter(([, count]) => count > 0).map(([groupId]) => groupId),
  );
  return seen.size === placedGroups.size
    && [...seen].every((groupId) => placedGroups.has(groupId));
}

function validRequirements(value) {
  if (!Array.isArray(value)) return false;
  const ids = new Set();
  for (const row of value) {
    if (
      !isRecord(row)
      || typeof row.requirement_id !== 'string'
      || row.requirement_id.length === 0
      || ids.has(row.requirement_id)
      || !REQUIREMENT_STATUSES.has(row.status)
      || typeof row.reason !== 'string'
      || row.reason.length < 20
    ) return false;
    ids.add(row.requirement_id);
  }
  return true;
}

function exactStringArray(value, expected) {
  return (
    Array.isArray(value)
    && value.length === expected.length
    && value.every((item, index) => item === expected[index])
  );
}

function validRouteLoops(value) {
  return (
    Array.isArray(value)
    && value.length === ROUTE_LOOP_CONTRACT.length
    && value.every((row, index) => {
      const expected = ROUTE_LOOP_CONTRACT[index];
      return (
        isRecord(row)
        && row.loop_id === expected.loop_id
        && exactStringArray(
          row.ground_attachment_node_ids,
          expected.ground_attachment_node_ids,
        )
        && exactStringArray(row.elevated_edge_ids, expected.elevated_edge_ids)
        && row.ground_connected === true
      );
    })
  );
}

export function isProductionCameraPlan(plan) {
  if (
    !isRecord(plan)
    || plan.schema_version !== 1
    || plan.plan_schema !== PRODUCTION_CAMERA_PLAN_SCHEMA
    || plan.profile_id !== 'synthetic-village-coverage-180-v1'
    || plan.journal_schema !== 'nantai.synthetic-village.production-render-journal.v1'
    || typeof plan.scene_plan_sha256 !== 'string'
    || !SHA256.test(plan.scene_plan_sha256)
    || typeof plan.elevated_topology_sha256 !== 'string'
    || !SHA256.test(plan.elevated_topology_sha256)
    || plan.coordinate_system !== 'opencv-c2w-right-down-forward-meters'
    || plan.synthetic !== true
    || plan.geometry_trust !== 'simplified-pbr-not-render-parity'
    || plan.verification_level !== 'L2'
    || plan.declared_target_count !== 180
    || !Number.isSafeInteger(plan.camera_count)
    || plan.camera_count < 0
    || plan.camera_count > 180
    || typeof plan.complete !== 'boolean'
    || plan.complete !== (plan.camera_count === 180)
    || !Array.isArray(plan.cameras)
    || plan.cameras.length !== plan.camera_count
    || !validRouteLoops(plan.route_loops)
  ) return false;

  const cameraIds = new Set();
  const positions = new Set();
  const cameraCounts = new Map();
  for (let index = 0; index < plan.cameras.length; index += 1) {
    const camera = plan.cameras[index];
    if (!validCamera(camera, index + 1)) return false;
    const positionKey = camera.position_m.join(',');
    if (cameraIds.has(camera.camera_id) || positions.has(positionKey)) return false;
    cameraIds.add(camera.camera_id);
    positions.add(positionKey);
    cameraCounts.set(camera.group_id, (cameraCounts.get(camera.group_id) ?? 0) + 1);
  }

  return (
    validUnplacedGroups(plan.unplaced_groups, cameraCounts)
    && (!plan.complete || plan.unplaced_groups.length === 0)
    && validGroupCoverage(plan.group_coverage, plan.cameras, cameraCounts)
    && validRequirements(plan.undelivered_requirements)
  );
}

function unknownModel(label = 'production camera plan not loaded') {
  return {
    status: 'unknown',
    color: PRODUCTION_PLAN_STATUS_COLORS.unknown,
    placed: null,
    target: 180,
    unplaced: null,
    summary: label,
    unplaced_label: 'unknown',
    requirements_label: 'unknown',
    provenance_label: 'unknown · fail-closed',
  };
}

export function productionCameraPlanViewModel(plan) {
  if (!isProductionCameraPlan(plan)) return unknownModel(
    plan == null ? undefined : 'unknown · invalid production camera plan',
  );

  const unplaced = plan.declared_target_count - plan.camera_count;
  const requirements = plan.undelivered_requirements;
  let status = 'planned';
  let summary = `${plan.camera_count}/${plan.declared_target_count} poses planned`;
  if (!plan.complete) {
    status = 'incomplete';
    summary = `${plan.camera_count}/${plan.declared_target_count} poses · not 360 evidence`;
  } else if (requirements.length > 0) {
    status = 'evidence-incomplete';
    summary = `${plan.camera_count}/${plan.declared_target_count} poses · evidence pending`;
  }

  const unplacedLabel = plan.unplaced_groups.length
    ? plan.unplaced_groups
      .map((row) => `${row.group_id}: ${row.camera_count}`)
      .join(' · ')
    : 'none declared';
  const requirementStatuses = [...new Set(requirements.map((row) => row.status))];
  const requirementsLabel = requirements.length
    ? `${requirements.length} undelivered · ${requirementStatuses.join(', ')}`
    : 'none declared';

  return {
    status,
    color: PRODUCTION_PLAN_STATUS_COLORS[status],
    placed: plan.camera_count,
    target: plan.declared_target_count,
    unplaced,
    summary,
    unplaced_label: unplacedLabel,
    requirements_label: requirementsLabel,
    provenance_label: (
      `synthetic · ${plan.verification_level} · ${plan.geometry_trust}`
    ),
  };
}
