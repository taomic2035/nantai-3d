export const SH_C0 = 0.28209479177387814;

const BYTE_COLOR_INDEX = Object.freeze({ r: 0, g: 1, b: 2 });
const SH_COLOR_INDEX = Object.freeze({ f_dc_0: 0, f_dc_1: 1, f_dc_2: 2 });

function boundedChannel(value) {
  return Math.min(1, Math.max(0, value));
}

export function pointPreviewColorComponent(name, value) {
  if (!Number.isFinite(value)) return null;
  if (Object.hasOwn(BYTE_COLOR_INDEX, name)) {
    return {
      index: BYTE_COLOR_INDEX[name],
      value: boundedChannel(value / 255),
    };
  }
  if (Object.hasOwn(SH_COLOR_INDEX, name)) {
    return {
      index: SH_COLOR_INDEX[name],
      value: boundedChannel(value * SH_C0 + 0.5),
    };
  }
  return null;
}
