const COLOR_MAX = 0xffffff;

export const SKY_VERTEX_SHADER = `
  varying vec3 vSkyDirection;

  void main() {
    vec3 worldPosition = (modelMatrix * vec4(position, 1.0)).xyz;
    vSkyDirection = normalize(worldPosition - cameraPosition);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

export const SKY_FRAGMENT_SHADER = `
  uniform vec3 uZenith;
  uniform vec3 uHorizon;
  uniform vec3 uLower;
  uniform vec3 uSunColor;
  uniform vec3 uSunDirection;
  uniform float uSunSharpness;
  uniform float uCloudCoverage;
  uniform float uCloudOpacity;
  uniform float uHaze;
  uniform float uStars;
  uniform float uTime;
  varying vec3 vSkyDirection;

  float skyHash(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.yzx + 33.33);
    return fract((p.x + p.y) * p.z);
  }

  float skyNoise(vec3 p) {
    vec3 cell = floor(p);
    vec3 local = fract(p);
    local = local * local * (3.0 - 2.0 * local);
    return mix(
      mix(
        mix(skyHash(cell), skyHash(cell + vec3(1.0, 0.0, 0.0)), local.x),
        mix(skyHash(cell + vec3(0.0, 1.0, 0.0)), skyHash(cell + vec3(1.0, 1.0, 0.0)), local.x),
        local.y
      ),
      mix(
        mix(skyHash(cell + vec3(0.0, 0.0, 1.0)), skyHash(cell + vec3(1.0, 0.0, 1.0)), local.x),
        mix(skyHash(cell + vec3(0.0, 1.0, 1.0)), skyHash(cell + vec3(1.0, 1.0, 1.0)), local.x),
        local.y
      ),
      local.z
    );
  }

  float skyFbm(vec3 p) {
    float value = 0.0;
    float weight = 0.55;
    for (int octave = 0; octave < 4; octave += 1) {
      value += skyNoise(p) * weight;
      p = p * 2.03 + vec3(7.1, 3.7, 5.9);
      weight *= 0.48;
    }
    return value;
  }

  void main() {
    vec3 direction = normalize(vSkyDirection);
    float height = direction.y;
    float upperBlend = smoothstep(0.02, 0.86, height);
    float lowerBlend = smoothstep(-0.28, 0.12, height);
    vec3 color = mix(uLower, uHorizon, lowerBlend);
    color = mix(color, uZenith, upperBlend);

    float horizonBand = exp(-abs(height) * 7.0) * uHaze;
    color = mix(color, uHorizon, clamp(horizonBand, 0.0, 0.92));

    vec3 cloudCoordinate = direction * 4.2
      + vec3(uTime * 0.003, 0.0, uTime * -0.0017);
    float cloudNoise = skyFbm(cloudCoordinate);
    float cloudEdge = min(0.98, uCloudCoverage + 0.16);
    float cloud = smoothstep(uCloudCoverage, cloudEdge, cloudNoise);
    cloud *= smoothstep(-0.08, 0.24, height) * uCloudOpacity;
    vec3 cloudColor = mix(uHorizon, vec3(1.0), 0.28);
    color = mix(color, cloudColor, clamp(cloud, 0.0, 0.94));

    float sunAlignment = max(dot(direction, normalize(uSunDirection)), 0.0);
    float sunDisc = pow(sunAlignment, uSunSharpness);
    float sunHalo = pow(sunAlignment, max(2.0, uSunSharpness * 0.025));
    color += uSunColor * (sunDisc + sunHalo * 0.12) * (1.0 - cloud * 0.7);

    vec3 starCell = floor(direction * 950.0);
    float star = step(0.9975, skyHash(starCell));
    star *= smoothstep(0.02, 0.55, height) * uStars * (1.0 - cloud);
    color += vec3(star);

    gl_FragColor = vec4(max(color, vec3(0.0)), 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

function finiteUnit(value, label) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error(`Invalid procedural sky preset ${label}`);
  }
  return value;
}

function finiteColor(value, label) {
  if (!Number.isInteger(value) || value < 0 || value > COLOR_MAX) {
    throw new Error(`Invalid procedural sky preset ${label}`);
  }
  return value;
}

export function skyDomeParameters(preset) {
  const sky = preset?.sky;
  if (!sky || typeof sky !== 'object') {
    throw new Error('Invalid procedural sky preset');
  }
  if (
    !Array.isArray(sky.sunDirection)
    || sky.sunDirection.length !== 3
    || !sky.sunDirection.every(Number.isFinite)
  ) {
    throw new Error('Invalid procedural sky sun direction');
  }
  const length = Math.hypot(...sky.sunDirection);
  if (!(length > 1e-9)) {
    throw new Error('Invalid procedural sky sun direction');
  }
  if (
    typeof sky.sunSharpness !== 'number'
    || !Number.isFinite(sky.sunSharpness)
    || sky.sunSharpness < 1
    || sky.sunSharpness > 2048
  ) {
    throw new Error('Invalid procedural sky preset sun sharpness');
  }
  return {
    effectKind: 'atmospheric-overlay',
    relighting: false,
    zenith: finiteColor(sky.zenith, 'zenith'),
    horizon: finiteColor(sky.horizon, 'horizon'),
    lower: finiteColor(sky.lower, 'lower'),
    sunColor: finiteColor(sky.sunColor, 'sun color'),
    sunDirection: sky.sunDirection.map((value) => value / length),
    sunSharpness: sky.sunSharpness,
    cloudCoverage: finiteUnit(sky.cloudCoverage, 'cloud coverage'),
    cloudOpacity: finiteUnit(sky.cloudOpacity, 'cloud opacity'),
    haze: finiteUnit(sky.haze, 'haze'),
    stars: finiteUnit(sky.stars, 'stars'),
  };
}

export function createSkyDome({ THREE, scene }) {
  if (!THREE || !scene?.add) throw new Error('Sky dome requires THREE and a scene');
  const geometry = new THREE.SphereGeometry(1, 32, 18);
  const material = new THREE.ShaderMaterial({
    name: 'viewer_runtime_procedural_sky_material',
    uniforms: {
      uZenith: { value: new THREE.Color(0) },
      uHorizon: { value: new THREE.Color(0) },
      uLower: { value: new THREE.Color(0) },
      uSunColor: { value: new THREE.Color(0) },
      uSunDirection: { value: new THREE.Vector3(0, 1, 0) },
      uSunSharpness: { value: 1 },
      uCloudCoverage: { value: 1 },
      uCloudOpacity: { value: 0 },
      uHaze: { value: 0 },
      uStars: { value: 0 },
      uTime: { value: 0 },
    },
    vertexShader: SKY_VERTEX_SHADER,
    fragmentShader: SKY_FRAGMENT_SHADER,
    side: THREE.BackSide,
    depthWrite: false,
    depthTest: false,
    fog: false,
    toneMapped: true,
  });
  const dome = new THREE.Mesh(geometry, material);
  dome.name = 'viewer_runtime_procedural_sky';
  dome.frustumCulled = false;
  dome.renderOrder = -1000;
  dome.userData.nvEffectKind = 'atmospheric-overlay';
  dome.userData.nvRelighting = false;
  scene.add(dome);
  return dome;
}

export function applySkyDomePreset(dome, preset) {
  if (!dome?.material?.uniforms) throw new Error('Sky dome is not initialized');
  const parameters = skyDomeParameters(preset);
  const uniforms = dome.material.uniforms;
  uniforms.uZenith.value.setHex(parameters.zenith);
  uniforms.uHorizon.value.setHex(parameters.horizon);
  uniforms.uLower.value.setHex(parameters.lower);
  uniforms.uSunColor.value.setHex(parameters.sunColor);
  uniforms.uSunDirection.value.fromArray(parameters.sunDirection);
  uniforms.uSunSharpness.value = parameters.sunSharpness;
  uniforms.uCloudCoverage.value = parameters.cloudCoverage;
  uniforms.uCloudOpacity.value = parameters.cloudOpacity;
  uniforms.uHaze.value = parameters.haze;
  uniforms.uStars.value = parameters.stars;
  return parameters;
}

export function updateSkyDome(dome, camera, elapsedSeconds) {
  if (!dome || !camera || !Number.isFinite(elapsedSeconds)) return;
  dome.position.copy(camera.position);
  const radius = Math.max(100, camera.far * 0.9);
  dome.scale.setScalar(radius);
  dome.material.uniforms.uTime.value = elapsedSeconds;
}
