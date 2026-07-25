export const ROAMING_GRAPH_URL = '/web/data/roaming-graph.json';

/**
 * Ask Viewer to validate an optional room/portal graph.
 *
 * Studio consumes only the derived view model returned by Viewer. A missing
 * canonical file is an honest absence; every other probe/load failure remains
 * visible to the operator.
 */
export async function loadOptionalRoamingGraph({
  bridge,
  fetchImpl = globalThis.fetch,
  url = ROAMING_GRAPH_URL,
}) {
  if (!bridge.supportsArtifactKind('roaming-graph')) {
    return { status: 'unsupported' };
  }
  const response = await fetchImpl(url, { method: 'HEAD', cache: 'no-store' });
  if (response.status === 404) return { status: 'absent' };
  if (!response.ok) {
    throw new Error(`roaming graph probe failed (${response.status})`);
  }
  const loaded = await bridge.loadArtifact('roaming-graph', { url });
  return {
    status: 'loaded',
    roaming_graph: loaded.roaming_graph,
  };
}
