export const COVERAGE_AUDIT_URL = '/web/data/coverage-audit.json';

/**
 * Load the canonical optional runtime audit without turning absence into error.
 *
 * The static server already confines this URL below the approved `web` root.
 * The Viewer remains the authority that validates the report body.
 */
export async function loadOptionalCoverageAudit({
  bridge,
  fetchImpl = globalThis.fetch,
  url = COVERAGE_AUDIT_URL,
}) {
  if (!bridge.supportsArtifactKind('coverage-audit')) {
    return { status: 'unsupported' };
  }
  const response = await fetchImpl(url, { method: 'HEAD', cache: 'no-store' });
  if (response.status === 404) return { status: 'absent' };
  if (!response.ok) {
    throw new Error(`coverage audit probe failed (${response.status})`);
  }
  const loaded = await bridge.loadArtifact('coverage-audit', { url });
  return {
    status: 'loaded',
    coverage: loaded.coverage,
  };
}
