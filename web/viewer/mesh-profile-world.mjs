import {
  H2_PROFILE_ID,
  H3_PROFILE_ID,
} from './material-profile.mjs';

const EVENT_CODES = new Set([
  'h3-load-start',
  'h3-load-complete',
  'h3-failure',
  'h3-chunks-disposed',
  'h3-templates-disposed',
  'h3-textures-disposed',
  'h2-reload-start',
  'h2-reload-complete',
]);

function validateLane(lane) {
  if (
    lane === null
    || typeof lane !== 'object'
    || typeof lane.build !== 'function'
    || typeof lane.disposeTemplates !== 'function'
    || typeof lane.disposeTextures !== 'function'
  ) {
    throw new TypeError('mesh profile resource lane is invalid');
  }
  return lane;
}

export function createAtomicMeshProfileWorld({
  profileController,
  renderer,
  resolveSelectedProfile,
  createLane,
  replaceVisible,
  disposeRecord,
  onEvent = () => {},
} = {}) {
  if (
    typeof profileController?.select !== 'function'
    || typeof profileController?.rollbackToFallback !== 'function'
    || typeof profileController?.snapshot !== 'function'
    || typeof profileController?.getRuntimeLoader !== 'function'
    || renderer === null
    || typeof renderer !== 'object'
    || typeof resolveSelectedProfile !== 'function'
    || typeof createLane !== 'function'
    || typeof replaceVisible !== 'function'
    || typeof disposeRecord !== 'function'
    || typeof onEvent !== 'function'
  ) {
    throw new TypeError('atomic mesh profile world dependencies are invalid');
  }

  let lane = null;
  let laneProfileId = null;
  let activeRecords = [];
  let loadingPromise = null;

  const emit = (code) => {
    if (!EVENT_CODES.has(code)) {
      throw new TypeError('mesh profile world event is invalid');
    }
    try {
      onEvent(code);
    } catch {
      // Bounded diagnostics must never replace the fail-closed transition.
    }
  };

  const selectedRuntime = (runtime, profileId) => (
    resolveSelectedProfile(runtime, profileId)
  );

  const ensureLane = (profileId) => {
    if (lane !== null) {
      if (laneProfileId !== profileId) {
        throw new Error('mesh profile lane cannot mix frozen profiles');
      }
      return lane;
    }
    lane = validateLane(createLane(
      profileId,
      profileController.getRuntimeLoader(),
    ));
    laneProfileId = profileId;
    return lane;
  };

  const disposeRecords = async (records) => {
    await Promise.allSettled(
      records.map((record) => Promise.resolve(disposeRecord(record))),
    );
  };

  const buildBatch = async (runtimes, profileId) => {
    let currentLane;
    try {
      currentLane = ensureLane(profileId);
    } catch (error) {
      return {
        records: [],
        failure: { status: 'rejected', reason: error },
      };
    }
    const results = await Promise.allSettled(
      runtimes.map(async (runtime) => {
        const record = await currentLane.build(
          selectedRuntime(runtime, profileId),
          runtime,
        );
        if (
          record === null
          || typeof record !== 'object'
          || record.profileId !== profileId
        ) {
          throw new TypeError(
            'mesh profile record identity is invalid',
          );
        }
        return record;
      }),
    );
    const records = results
      .filter((result) => result.status === 'fulfilled')
      .map((result) => result.value);
    const failure = results.find(
      (result) => result.status === 'rejected',
    );
    return { records, failure };
  };

  const publish = async (records, profileId) => {
    await replaceVisible({
      previous: activeRecords,
      next: records,
      profileId,
    });
    activeRecords = records;
  };

  const selectOnce = async (runtimes) => {
    const h3 = selectedRuntime(runtimes[0], H3_PROFILE_ID);
    const canary = h3.textures?.find(
      (descriptor) => descriptor.media_type === 'image/ktx2',
    );
    return profileController.select({
      renderer,
      canary,
      predictedCompressedBytes:
        h3.predicted_compressed_texture_bytes,
    });
  };

  const disposePrimaryLane = async () => {
    const primary = lane;
    lane = null;
    laneProfileId = null;
    let firstFailure = null;
    try {
      await primary?.disposeTemplates();
    } catch (error) {
      firstFailure = error;
    } finally {
      emit('h3-templates-disposed');
    }
    try {
      await primary?.disposeTextures();
    } catch (error) {
      firstFailure ??= error;
    } finally {
      emit('h3-textures-disposed');
    }
    if (firstFailure) throw firstFailure;
  };

  const loadOnce = async (runtimes) => {
    if (!Array.isArray(runtimes) || runtimes.length === 0) {
      throw new TypeError('visible mesh runtime batch is absent');
    }
    const selection = await selectOnce(runtimes);
    let profileId = selection.profileId;
    emit(profileId === H3_PROFILE_ID
      ? 'h3-load-start'
      : 'h2-reload-start');
    let batch = await buildBatch(runtimes, profileId);
    if (!batch.failure) {
      await publish(batch.records, profileId);
      emit(profileId === H3_PROFILE_ID
        ? 'h3-load-complete'
        : 'h2-reload-complete');
      return [...activeRecords];
    }

    await disposeRecords(batch.records);
    if (profileId !== H3_PROFILE_ID) {
      throw batch.failure.reason;
    }
    emit('h3-failure');
    await replaceVisible({
      previous: activeRecords,
      next: [],
      profileId: null,
    });
    activeRecords = [];
    emit('h3-chunks-disposed');
    const fallback = await profileController.rollbackToFallback({
      disposePrimary: disposePrimaryLane,
    });
    profileId = fallback.profileId;
    if (profileId !== H2_PROFILE_ID) {
      throw new Error('mesh profile rollback did not freeze H2');
    }

    emit('h2-reload-start');
    batch = await buildBatch(runtimes, profileId);
    if (batch.failure) {
      await disposeRecords(batch.records);
      throw batch.failure.reason;
    }
    await publish(batch.records, profileId);
    emit('h2-reload-complete');
    return [...activeRecords];
  };

  const loadVisible = (runtimes) => {
    if (loadingPromise) return loadingPromise;
    loadingPromise = loadOnce(runtimes).finally(() => {
      loadingPromise = null;
    });
    return loadingPromise;
  };

  const snapshot = () => {
    const profile = profileController.snapshot();
    return Object.freeze({
      state: profile.state,
      profileId: profile.profileId,
      fallbackCode: profile.fallbackReason?.code ?? null,
      activeRecords: activeRecords.length,
      mixedProfiles: new Set(
        activeRecords.map((record) => record.profileId),
      ).size > 1,
      loading: loadingPromise !== null,
    });
  };

  return Object.freeze({
    loadVisible,
    snapshot,
  });
}
