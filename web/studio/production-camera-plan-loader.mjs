export const PRODUCTION_CAMERA_PLAN_URL =
  '/web/data/production-camera-plan.json';

export async function loadOptionalProductionCameraPlan({
  bridge,
  fetchImpl = globalThis.fetch,
  url = PRODUCTION_CAMERA_PLAN_URL,
}) {
  if (!bridge.supportsArtifactKind('production-camera-plan')) {
    return { status: 'unsupported' };
  }
  const response = await fetchImpl(url, { method: 'HEAD', cache: 'no-store' });
  if (response.status === 404) return { status: 'absent' };
  if (!response.ok) {
    throw new Error(`production plan probe failed (${response.status})`);
  }
  const loaded = await bridge.loadArtifact('production-camera-plan', { url });
  return {
    status: 'loaded',
    production_plan: loaded.production_plan,
  };
}
