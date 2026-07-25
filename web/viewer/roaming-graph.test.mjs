import assert from 'node:assert/strict';
import test from 'node:test';

let graphModule;
try {
  graphModule = await import('./roaming-graph.mjs');
} catch (error) {
  graphModule = { __loadError: error };
}

function subject() {
  assert.equal(
    graphModule.__loadError,
    undefined,
    `roaming-graph.mjs must load: ${graphModule.__loadError?.message}`,
  );
  return graphModule;
}

const sha = (letter) => letter.repeat(64);

function room(roomId, label, kind, center, hashLetter) {
  return {
    room_id: roomId,
    label,
    kind,
    center_enu_m: center,
    collision_proxy_sha256: sha(hashLetter),
  };
}

function portal(portalId, roomA, roomB, endpointA, endpointB, hashLetter) {
  return {
    portal_id: portalId,
    room_ids: [roomA, roomB],
    endpoints_enu_m: [endpointA, endpointB],
    clear_width_m: 1.2,
    clear_height_m: 2.1,
    collision_proxy_sha256: sha(hashLetter),
    source_input_sha256: sha('f'),
  };
}

function edge(edgeId, portalId, fromRoomId, toRoomId) {
  return {
    edge_id: edgeId,
    portal_id: portalId,
    from_room_id: fromRoomId,
    to_room_id: toRoomId,
  };
}

function connectedGraph() {
  return {
    schema_version: 1,
    graph_schema: 'nantai.synthetic-village.roaming-graph.v1',
    graph_id: 'synthetic-village-interior-portal-v1',
    status: 'candidate',
    coordinate_frame: {
      name: 'synthetic-village-world-enu',
      units: 'meters',
      handedness: 'right',
      axes: { x: 'east', y: 'north', z: 'up' },
    },
    synthetic: true,
    trust: {
      geometry: 'modeled-unverified',
      connectivity: 'machine-checked-graph-only',
      coverage: 'not-verified',
      arbitrary_coordinate_reachability: 'not-claimed',
      trust_effect: 'none',
    },
    bindings: {
      scene_artifact_sha256: sha('a'),
      build_report_sha256: sha('b'),
      source_plan_sha256: sha('c'),
      collision_manifest_sha256: sha('d'),
    },
    entry_room_id: 'room-entry',
    rooms: [
      room('room-entry', 'Entry', 'exterior', [0, 0, 1.6], '1'),
      room('room-hall', 'Hall', 'interior', [4, 0, 1.6], '2'),
      room('room-gallery', 'Gallery', 'transition', [4, 4, 3.2], '3'),
    ],
    portals: [
      portal(
        'portal-entry-hall',
        'room-entry',
        'room-hall',
        [1, 0, 1.6],
        [2, 0, 1.6],
        '4',
      ),
      portal(
        'portal-hall-gallery',
        'room-hall',
        'room-gallery',
        [4, 1, 1.6],
        [4, 2, 2.4],
        '5',
      ),
      portal(
        'portal-gallery-entry',
        'room-gallery',
        'room-entry',
        [3, 4, 3.2],
        [0, 1, 1.6],
        '6',
      ),
    ],
    directed_edges: [
      edge('edge-entry-hall', 'portal-entry-hall', 'room-entry', 'room-hall'),
      edge('edge-hall-entry', 'portal-entry-hall', 'room-hall', 'room-entry'),
      edge('edge-hall-gallery', 'portal-hall-gallery', 'room-hall', 'room-gallery'),
      edge('edge-gallery-hall', 'portal-hall-gallery', 'room-gallery', 'room-hall'),
      edge('edge-gallery-entry', 'portal-gallery-entry', 'room-gallery', 'room-entry'),
      edge('edge-entry-gallery', 'portal-gallery-entry', 'room-entry', 'room-gallery'),
    ],
    route_loops: [{
      loop_id: 'loop-entry-hall-gallery',
      edge_ids: [
        'edge-entry-hall',
        'edge-hall-gallery',
        'edge-gallery-entry',
      ],
    }],
  };
}

function fragmentedGraph() {
  const graph = connectedGraph();
  graph.entry_room_id = 'room-entry';
  graph.rooms = [
    room('room-entry', 'Entry', 'exterior', [0, 0, 1.6], '1'),
    room('room-hall', 'Hall', 'interior', [4, 0, 1.6], '2'),
    room('room-cellar', 'Cellar', 'interior', [20, 0, -1], '7'),
    room('room-service', 'Service', 'exterior', [24, 0, 1.6], '8'),
  ];
  graph.portals = [
    portal(
      'portal-entry-hall',
      'room-entry',
      'room-hall',
      [1, 0, 1.6],
      [2, 0, 1.6],
      '4',
    ),
    portal(
      'portal-cellar-service',
      'room-cellar',
      'room-service',
      [21, 0, -1],
      [23, 0, 1.6],
      '9',
    ),
  ];
  graph.directed_edges = [
    edge('edge-entry-hall', 'portal-entry-hall', 'room-entry', 'room-hall'),
    edge('edge-hall-entry', 'portal-entry-hall', 'room-hall', 'room-entry'),
    edge(
      'edge-cellar-service',
      'portal-cellar-service',
      'room-cellar',
      'room-service',
    ),
    edge(
      'edge-service-cellar',
      'portal-cellar-service',
      'room-service',
      'room-cellar',
    ),
  ];
  graph.route_loops = [];
  return graph;
}

test('connected candidate graph exposes only graph-level reachability', () => {
  const { isRoamingGraph, roamingGraphViewModel } = subject();
  const graph = connectedGraph();

  assert.equal(isRoamingGraph(graph), true);
  assert.deepEqual(roamingGraphViewModel(graph), {
    status: 'graph-connected',
    color: '#7fd1ff',
    room_count: 3,
    reachable_room_count: 3,
    component_count: 1,
    portal_count: 3,
    loop_count: 1,
    summary: '3/3 graph rooms reachable · not 360 evidence',
    reachability_label: '3/3 rooms · 1 component',
    portal_label: '3 portals · 1 loop',
    provenance_label: 'synthetic · modeled-unverified · graph only',
    navigation_nodes: [
      {
        room_id: 'room-entry',
        label: 'Entry',
        position: { east: 0, north: 0, up: 1.6 },
      },
      {
        room_id: 'room-hall',
        label: 'Hall',
        position: { east: 4, north: 0, up: 1.6 },
      },
      {
        room_id: 'room-gallery',
        label: 'Gallery',
        position: { east: 4, north: 4, up: 3.2 },
      },
    ],
  });
});

test('valid disconnected components remain fragmented instead of ready', () => {
  const { isRoamingGraph, roamingGraphViewModel } = subject();
  const graph = fragmentedGraph();

  assert.equal(isRoamingGraph(graph), true);
  const model = roamingGraphViewModel(graph);
  assert.equal(model.status, 'fragmented');
  assert.equal(model.component_count, 2);
  assert.equal(model.reachable_room_count, 2);
  assert.equal(model.navigation_nodes.length, 2);
  assert.match(model.summary, /2\/4.*fragmented.*not 360 evidence/i);
  assert.doesNotMatch(
    JSON.stringify(model),
    /360.?ready|coverage.?complete|arbitrary.?coordinate.?ready|metric.?aligned/i,
  );
});

test('missing and malformed artifacts stay fail-closed unknown', () => {
  const { isRoamingGraph, roamingGraphViewModel } = subject();

  assert.equal(isRoamingGraph(null), false);
  assert.equal(isRoamingGraph({ graph_schema: 'named-only' }), false);
  assert.deepEqual(roamingGraphViewModel(null), {
    status: 'unknown',
    color: '#9aa4ad',
    room_count: null,
    reachable_room_count: null,
    component_count: null,
    portal_count: null,
    loop_count: null,
    summary: 'roaming graph not loaded',
    reachability_label: 'unknown',
    portal_label: 'unknown',
    provenance_label: 'unknown · fail-closed',
    navigation_nodes: [],
  });
  assert.equal(
    roamingGraphViewModel({ graph_schema: 'named-only' }).summary,
    'unknown · invalid roaming graph',
  );
});

test('identity, reference, reciprocal edge and loop contradictions fail closed', () => {
  const { isRoamingGraph } = subject();
  const mutations = [
    (graph) => { graph.rooms[1].room_id = graph.rooms[0].room_id; },
    (graph) => { graph.portals[0].room_ids[1] = 'room-missing'; },
    (graph) => {
      graph.rooms.push(room('room-dangling', 'Dangling', 'interior', [8, 8, 1.6], '0'));
    },
    (graph) => { graph.directed_edges.splice(1, 1); },
    (graph) => {
      graph.directed_edges.push(
        edge('edge-entry-hall-extra', 'portal-entry-hall', 'room-entry', 'room-hall'),
      );
    },
    (graph) => { graph.directed_edges[0].to_room_id = 'room-gallery'; },
    (graph) => { graph.route_loops[0].edge_ids[1] = 'edge-gallery-hall'; },
    (graph) => { graph.route_loops[0].edge_ids.pop(); },
    (graph) => { graph.route_loops = [null]; },
    (graph) => { graph.entry_room_id = 'room-missing'; },
    (graph) => { graph.portals[0].room_ids = ['room-entry', 'room-entry']; },
  ];

  for (const mutate of mutations) {
    const graph = connectedGraph();
    mutate(graph);
    assert.equal(isRoamingGraph(graph), false);
  }
});

test('numeric, frame, binding and trust mutations fail closed', () => {
  const { isRoamingGraph } = subject();
  const mutations = [
    (graph) => { graph.rooms[0].center_enu_m[0] = Number.NaN; },
    (graph) => { graph.portals[0].endpoints_enu_m[0][2] = Number.POSITIVE_INFINITY; },
    (graph) => { graph.portals[0].clear_width_m = 0; },
    (graph) => { graph.portals[0].clear_height_m = -1; },
    (graph) => { graph.bindings.scene_artifact_sha256 = sha('A'); },
    (graph) => { graph.bindings.build_report_sha256 = 'named-report'; },
    (graph) => { graph.coordinate_frame.units = 'centimeters'; },
    (graph) => { graph.coordinate_frame.axes.z = 'down'; },
    (graph) => { graph.synthetic = false; },
    (graph) => { graph.status = 'accepted'; },
    (graph) => { graph.trust.geometry = 'metric-aligned'; },
    (graph) => { graph.trust.coverage = 'verified'; },
    (graph) => {
      graph.trust.arbitrary_coordinate_reachability = 'complete';
    },
  ];

  for (const mutate of mutations) {
    const graph = connectedGraph();
    mutate(graph);
    assert.equal(isRoamingGraph(graph), false);
  }
});
