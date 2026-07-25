export const ROAMING_GRAPH_SCHEMA =
  'nantai.synthetic-village.roaming-graph.v1';

export const ROAMING_GRAPH_STATUS_COLORS = Object.freeze({
  unknown: '#9aa4ad',
  fragmented: '#ffbf47',
  'graph-connected': '#7fd1ff',
});

const SHA256 = /^[0-9a-f]{64}$/;
const STABLE_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
const ROOM_KINDS = new Set(['exterior', 'interior', 'transition']);
const MAX_ROOMS = 4096;
const MAX_PORTALS = 8192;
const MAX_EDGES = MAX_PORTALS * 2;
const MAX_LOOPS = 4096;
const MAX_ABS_COORDINATE_M = 10_000_000;
const MAX_CLEARANCE_M = 100;

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function stableId(value) {
  return typeof value === 'string' && STABLE_ID.test(value);
}

function displayLabel(value) {
  return (
    typeof value === 'string'
    && value.trim().length > 0
    && value.length <= 120
  );
}

function boundedArray(value, minimum, maximum) {
  return Array.isArray(value) && value.length >= minimum && value.length <= maximum;
}

function finiteCoordinate(value) {
  return (
    Number.isFinite(value)
    && Math.abs(value) <= MAX_ABS_COORDINATE_M
  );
}

function finiteVector3(value) {
  return (
    Array.isArray(value)
    && value.length === 3
    && value.every(finiteCoordinate)
  );
}

function positiveClearance(value) {
  return (
    Number.isFinite(value)
    && value > 0
    && value <= MAX_CLEARANCE_M
  );
}

function uniqueMap(rows, key) {
  const result = new Map();
  for (const row of rows) {
    if (!isRecord(row)) return null;
    const id = row[key];
    if (!stableId(id) || result.has(id)) return null;
    result.set(id, row);
  }
  return result;
}

function validHeader(graph) {
  const frame = graph.coordinate_frame;
  const trust = graph.trust;
  const bindings = graph.bindings;
  return (
    graph.schema_version === 1
    && graph.graph_schema === ROAMING_GRAPH_SCHEMA
    && stableId(graph.graph_id)
    && graph.status === 'candidate'
    && isRecord(frame)
    && frame.name === 'synthetic-village-world-enu'
    && frame.units === 'meters'
    && frame.handedness === 'right'
    && isRecord(frame.axes)
    && frame.axes.x === 'east'
    && frame.axes.y === 'north'
    && frame.axes.z === 'up'
    && graph.synthetic === true
    && isRecord(trust)
    && trust.geometry === 'modeled-unverified'
    && trust.connectivity === 'machine-checked-graph-only'
    && trust.coverage === 'not-verified'
    && trust.arbitrary_coordinate_reachability === 'not-claimed'
    && trust.trust_effect === 'none'
    && isRecord(bindings)
    && SHA256.test(bindings.scene_artifact_sha256)
    && SHA256.test(bindings.build_report_sha256)
    && SHA256.test(bindings.source_plan_sha256)
    && SHA256.test(bindings.collision_manifest_sha256)
    && stableId(graph.entry_room_id)
  );
}

function validRooms(rooms) {
  return rooms.every((room) => (
    isRecord(room)
    && stableId(room.room_id)
    && displayLabel(room.label)
    && ROOM_KINDS.has(room.kind)
    && finiteVector3(room.center_enu_m)
    && SHA256.test(room.collision_proxy_sha256)
  ));
}

function validPortals(portals) {
  return portals.every((portal) => (
    isRecord(portal)
    && stableId(portal.portal_id)
    && Array.isArray(portal.room_ids)
    && portal.room_ids.length === 2
    && stableId(portal.room_ids[0])
    && stableId(portal.room_ids[1])
    && portal.room_ids[0] !== portal.room_ids[1]
    && Array.isArray(portal.endpoints_enu_m)
    && portal.endpoints_enu_m.length === 2
    && portal.endpoints_enu_m.every(finiteVector3)
    && positiveClearance(portal.clear_width_m)
    && positiveClearance(portal.clear_height_m)
    && SHA256.test(portal.collision_proxy_sha256)
    && SHA256.test(portal.source_input_sha256)
  ));
}

function validEdges(edges) {
  return edges.every((edge) => (
    isRecord(edge)
    && stableId(edge.edge_id)
    && stableId(edge.portal_id)
    && stableId(edge.from_room_id)
    && stableId(edge.to_room_id)
    && edge.from_room_id !== edge.to_room_id
  ));
}

function validPortalEdges(portals, portalMap, roomMap, edges) {
  const incidentRooms = new Set();
  const edgesByPortal = new Map();
  for (const edge of edges) {
    const portal = portalMap.get(edge.portal_id);
    if (!portal || !roomMap.has(edge.from_room_id) || !roomMap.has(edge.to_room_id)) {
      return false;
    }
    const [roomA, roomB] = portal.room_ids;
    const matchesPortal = (
      (edge.from_room_id === roomA && edge.to_room_id === roomB)
      || (edge.from_room_id === roomB && edge.to_room_id === roomA)
    );
    if (!matchesPortal) return false;
    if (!edgesByPortal.has(edge.portal_id)) edgesByPortal.set(edge.portal_id, []);
    edgesByPortal.get(edge.portal_id).push(edge);
  }

  for (const portal of portals) {
    const [roomA, roomB] = portal.room_ids;
    if (!roomMap.has(roomA) || !roomMap.has(roomB)) return false;
    incidentRooms.add(roomA);
    incidentRooms.add(roomB);
    const portalEdges = edgesByPortal.get(portal.portal_id) ?? [];
    if (portalEdges.length !== 2) return false;
    const directions = new Set(
      portalEdges.map((edge) => `${edge.from_room_id}\u0000${edge.to_room_id}`),
    );
    if (
      !directions.has(`${roomA}\u0000${roomB}`)
      || !directions.has(`${roomB}\u0000${roomA}`)
    ) return false;
  }
  return [...roomMap.keys()].every((roomId) => incidentRooms.has(roomId));
}

function validLoops(loops, edgeMap) {
  const loopMap = uniqueMap(loops, 'loop_id');
  if (!loopMap) return false;
  for (const loop of loops) {
    if (
      !isRecord(loop)
      || !boundedArray(loop.edge_ids, 3, MAX_EDGES)
      || new Set(loop.edge_ids).size !== loop.edge_ids.length
    ) return false;
    const orderedEdges = loop.edge_ids.map((edgeId) => edgeMap.get(edgeId));
    if (orderedEdges.some((edge) => edge === undefined)) return false;
    for (let index = 0; index < orderedEdges.length; index += 1) {
      const current = orderedEdges[index];
      const next = orderedEdges[(index + 1) % orderedEdges.length];
      if (current.to_room_id !== next.from_room_id) return false;
    }
  }
  return true;
}

export function isRoamingGraph(graph) {
  if (!isRecord(graph) || !validHeader(graph)) return false;
  if (
    !boundedArray(graph.rooms, 2, MAX_ROOMS)
    || !boundedArray(graph.portals, 1, MAX_PORTALS)
    || !boundedArray(graph.directed_edges, 2, MAX_EDGES)
    || !boundedArray(graph.route_loops, 0, MAX_LOOPS)
    || !validRooms(graph.rooms)
    || !validPortals(graph.portals)
    || !validEdges(graph.directed_edges)
  ) return false;

  const roomMap = uniqueMap(graph.rooms, 'room_id');
  const portalMap = uniqueMap(graph.portals, 'portal_id');
  const edgeMap = uniqueMap(graph.directed_edges, 'edge_id');
  if (
    !roomMap
    || !portalMap
    || !edgeMap
    || !roomMap.has(graph.entry_room_id)
    || graph.directed_edges.length !== graph.portals.length * 2
  ) return false;

  return (
    validPortalEdges(graph.portals, portalMap, roomMap, graph.directed_edges)
    && validLoops(graph.route_loops, edgeMap)
  );
}

function adjacencyFor(graph) {
  const adjacency = new Map(graph.rooms.map((room) => [room.room_id, new Set()]));
  for (const edge of graph.directed_edges) {
    adjacency.get(edge.from_room_id).add(edge.to_room_id);
  }
  return adjacency;
}

function reachableFrom(start, adjacency) {
  const visited = new Set([start]);
  const queue = [start];
  for (let index = 0; index < queue.length; index += 1) {
    for (const neighbor of adjacency.get(queue[index]) ?? []) {
      if (visited.has(neighbor)) continue;
      visited.add(neighbor);
      queue.push(neighbor);
    }
  }
  return visited;
}

function componentCount(graph, adjacency) {
  const remaining = new Set(graph.rooms.map((room) => room.room_id));
  let count = 0;
  while (remaining.size > 0) {
    const [start] = remaining;
    for (const roomId of reachableFrom(start, adjacency)) remaining.delete(roomId);
    count += 1;
  }
  return count;
}

function plural(value, singular, pluralForm = `${singular}s`) {
  return `${value} ${value === 1 ? singular : pluralForm}`;
}

function unknownModel(label = 'roaming graph not loaded') {
  return {
    status: 'unknown',
    color: ROAMING_GRAPH_STATUS_COLORS.unknown,
    room_count: null,
    reachable_room_count: null,
    component_count: null,
    portal_count: null,
    loop_count: null,
    summary: label,
    reachability_label: 'unknown',
    portal_label: 'unknown',
    provenance_label: 'unknown · fail-closed',
    navigation_nodes: [],
  };
}

export function roamingGraphViewModel(graph) {
  if (!isRoamingGraph(graph)) {
    return unknownModel(graph == null ? undefined : 'unknown · invalid roaming graph');
  }

  const adjacency = adjacencyFor(graph);
  const reachable = reachableFrom(graph.entry_room_id, adjacency);
  const components = componentCount(graph, adjacency);
  const connected = reachable.size === graph.rooms.length && components === 1;
  const status = connected ? 'graph-connected' : 'fragmented';
  const fragmented = connected ? '' : ' · fragmented';

  return {
    status,
    color: ROAMING_GRAPH_STATUS_COLORS[status],
    room_count: graph.rooms.length,
    reachable_room_count: reachable.size,
    component_count: components,
    portal_count: graph.portals.length,
    loop_count: graph.route_loops.length,
    summary: (
      `${reachable.size}/${graph.rooms.length} graph rooms reachable`
      + `${fragmented} · not 360 evidence`
    ),
    reachability_label: (
      `${reachable.size}/${graph.rooms.length} rooms · `
      + plural(components, 'component')
    ),
    portal_label: (
      `${plural(graph.portals.length, 'portal')} · `
      + plural(graph.route_loops.length, 'loop')
    ),
    provenance_label: 'synthetic · modeled-unverified · graph only',
    navigation_nodes: graph.rooms
      .filter((room) => reachable.has(room.room_id))
      .map((room) => ({
        room_id: room.room_id,
        label: room.label,
        position: {
          east: room.center_enu_m[0],
          north: room.center_enu_m[1],
          up: room.center_enu_m[2],
        },
      })),
  };
}
