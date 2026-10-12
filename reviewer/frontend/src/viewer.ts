import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export type BlockState = { name: string; properties: Record<string, string> };
export type ModelPart = { texture: string | null; translucent: boolean; tint: boolean; tint_color?: string; shade: boolean; positions: number[]; uv: number[]; opacity?: number; static_texture?: boolean };
export type Appearance = { faces: (string | null)[]; approximate: boolean; translucent?: boolean; tints?: boolean[]; parts?: ModelPart[]; models?: string[]; warnings?: string[]; invisible?: boolean; coverage?: string };
export type Region = { name: string; size: number[]; origin: number[]; signed_size: number[]; palette: BlockState[]; indices: number[] };
export type SceneData = { kind: string; regions: Region[]; limitations: string };
export type Marker = { index: number; position: number[]; kind: string };
export type CameraState = { position: number[]; target: number[]; quaternion: number[] };
export type Selection = { region: string; index: number; position: number[]; state: BlockState; side: string };
export interface ViewerAdapter {
  loadScene(region: Region | undefined, layer: number, markers: Marker[], highlight: boolean): void;
  setCamera(camera: CameraState): void;
  getCamera(): CameraState;
  setLayer(layer: number): void;
  highlightDifferences(markers: Marker[], enabled: boolean): void;
  setSelection(index: number | null): void;
  dispose(): void;
}

export function voxelPosition(region: Region, index: number): number[] {
  const [dx, , dz] = region.size;
  const y = Math.floor(index / (dx * dz)), remainder = index % (dx * dz);
  return [region.origin[0] + remainder % dx, region.origin[1] + y, region.origin[2] + Math.floor(remainder / dx)];
}
export function stateText(state?: BlockState): string {
  if (!state) return 'No counterpart';
  const props = Object.entries(state.properties).map(([k, v]) => `${k}=${v}`).join(', ');
  return state.name.replace('minecraft:', '') + (props ? ` [${props}]` : '');
}
const air = new Set(['minecraft:air', 'minecraft:cave_air', 'minecraft:void_air']);
const differenceColors: Record<string, string> = { missing: '#ef645d', added: '#4b9cff', changed: '#ffbb42' };
function blockColor(name: string): THREE.Color {
  let hash = 2166136261;
  for (const character of name) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  return new THREE.Color().setHSL((hash >>> 0) % 360 / 360, .25 + ((hash >>> 9) % 20) / 100, .40 + ((hash >>> 18) % 22) / 100);
}

export class VoxelRenderer implements ViewerAdapter {
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(45, 1, .1, 10000);
  private renderer: THREE.WebGLRenderer;
  private controls: OrbitControls;
  private group = new THREE.Group();
  private instanceIndices = new Map<THREE.Object3D, number[]>();
  private appearances: Appearance[] = [];
  private texturesEnabled = false;
  private textures = new Map<string, THREE.Texture>();
  private region?: Region;
  private layer = Infinity;
  private markers: Marker[] = [];
  private highlight = true;
  private selection = new THREE.Box3Helper(new THREE.Box3(), new THREE.Color('#154e3b'));
  private frame = 0;
  private observer: ResizeObserver;
  private keys = new Set<string>();
  private applyingCamera = false;
  private fly = false;
  private fallbackFly = false;
  private requestingLock = false;
  private draggingLook = false;
  private pointerDown = [0, 0];
  private previousTime = performance.now();
  private regionIdentity = '';
  constructor(private host: HTMLElement, private side: string, private changed: (camera: CameraState) => void,
      private selected: (selection: Selection) => void, private flying: (active: boolean) => void) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    this.renderer.setClearColor('#edf1ed');
    this.renderer.domElement.setAttribute('aria-label', `${side} diagnostic 3D viewer`);
    this.renderer.domElement.tabIndex = 0;
    host.appendChild(this.renderer.domElement);
    this.scene.add(this.group, this.selection);
    this.selection.visible = false;
    this.scene.add(new THREE.HemisphereLight('#ffffff', '#718077', 2.5));
    const sun = new THREE.DirectionalLight('#ffffff', 2.2); sun.position.set(40, 70, 20); this.scene.add(sun);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.addEventListener('change', this.onCamera);
    this.observer = new ResizeObserver(this.resize); this.observer.observe(host); this.resize();
    this.renderer.domElement.addEventListener('pointerdown', this.down);
    this.renderer.domElement.addEventListener('pointerup', this.click);
    document.addEventListener('pointerlockchange', this.lockChanged);
    document.addEventListener('pointerlockerror', this.enableFallback);
    document.addEventListener('mousemove', this.look);
    document.addEventListener('keydown', this.keyDown);
    document.addEventListener('keyup', this.keyUp);
    window.addEventListener('blur', this.clearKeys);
    this.renderer.domElement.addEventListener('blur', this.canvasBlur);
    this.animate();
  }
  private resize = () => {
    const width = this.host.clientWidth, height = this.host.clientHeight;
    if (!width || !height) return;
    this.renderer.setSize(width, height); this.camera.aspect = width / height; this.camera.updateProjectionMatrix();
  };
  private publishCamera() { this.renderer.domElement.dataset.camera = JSON.stringify(this.getCamera()); }
  private onCamera = () => { this.publishCamera(); if (!this.applyingCamera) this.changed(this.getCamera()); };
  getCamera(): CameraState { return { position: this.camera.position.toArray(), target: this.controls.target.toArray(), quaternion: this.camera.quaternion.toArray() }; }
  setCamera(value: CameraState) {
    this.applyingCamera = true;
    this.camera.position.fromArray(value.position); this.controls.target.fromArray(value.target);
    this.controls.update(); this.camera.quaternion.fromArray(value.quaternion);
    this.publishCamera();
    this.applyingCamera = false;
  }
  resetCamera() {
    if (!this.region) return;
    const center = this.region.origin.map((v, i) => v + this.region!.size[i] / 2);
    const extent = Math.max(...this.region.size), distance = extent * 1.6 + 4;
    this.controls.target.fromArray(center);
    this.camera.position.set(center[0] + distance, center[1] + distance * .7, center[2] + distance);
    this.controls.update(); this.onCamera();
  }
  setAppearance(appearances: Appearance[], enabled: boolean) {
    this.appearances = appearances; this.texturesEnabled = enabled;
    this.loadScene(this.region, this.layer, this.markers, this.highlight);
  }
  private texture(url: string, staticTexture = false): THREE.Texture {
    const key = `${url}:${staticTexture}`;
    const existing = this.textures.get(key);
    if (existing) return existing;
    const texture = new THREE.TextureLoader().load(url, loaded => {
      const image = loaded.image as HTMLImageElement;
      // Animated strips use a static first frame, not a stretched whole strip.
      if (!staticTexture && image.height > image.width) {
        loaded.repeat.y = image.width / image.height; loaded.offset.y = 1 - loaded.repeat.y;
      }
      this.renderer.domElement.dataset.textureLoaded = String(Number(this.renderer.domElement.dataset.textureLoaded || 0) + 1);
    }, undefined, () => {
      this.renderer.domElement.dataset.textureErrors = String(Number(this.renderer.domElement.dataset.textureErrors || 0) + 1);
      this.host.dispatchEvent(new CustomEvent('texture-error', { bubbles: true }));
    });
    texture.magFilter = THREE.NearestFilter; texture.minFilter = THREE.NearestFilter;
    texture.colorSpace = THREE.SRGBColorSpace;
    this.textures.set(key, texture);
    return texture;
  }
  loadScene(region: Region | undefined, layer: number, markers: Marker[], highlight: boolean) {
    const identity = region ? `${region.name}:${region.origin}:${region.size}` : '';
    const reset = identity !== this.regionIdentity;
    this.regionIdentity = identity; this.region = region; this.layer = layer; this.markers = markers; this.highlight = highlight;
    this.group.children.forEach(object => {
      const mesh = object as THREE.Mesh;
      mesh.geometry?.dispose();
      if (Array.isArray(mesh.material)) mesh.material.forEach(m => m.dispose()); else mesh.material?.dispose();
    });
    this.group.clear(); this.instanceIndices.clear(); this.selection.visible = false;
    if (!region) return;
    const diff = new Map(markers.map(marker => [marker.index, marker.kind]));
    const shown: number[] = [];
    for (let i = 0; i < region.indices.length; i++) {
      const state = region.palette[region.indices[i]], point = voxelPosition(region, i);
      if (this.appearances[region.indices[i]]?.invisible && !(highlight && diff.has(i))) continue;
      if (point[1] <= layer && (!air.has(state.name) || (highlight && diff.has(i)))) shown.push(i);
    }
    const batches = new Map<string, { indices: number[]; parts: ModelPart[] }>();
    // Hash a palette appearance once, not once for every voxel in a large build.
    const signatures = region.palette.map((_, i) => JSON.stringify(this.appearances[i]?.parts || []));
    for (const index of shown) {
      const paletteIndex = region.indices[index], state = region.palette[paletteIndex];
      const parts = !air.has(state.name) ? this.appearances[paletteIndex]?.parts || [] : [];
      const key = air.has(state.name) ? 'ghost-cube' : signatures[paletteIndex];
      if (!batches.has(key)) batches.set(key, { indices: [], parts });
      batches.get(key)!.indices.push(index);
    }
    let triangles = 0;
    for (const { indices, parts } of batches.values()) {
      for (const part of parts.length ? parts : [null]) {
      const textured = this.texturesEnabled && !!part?.texture;
      const parameters = {
        map: textured ? this.texture(part!.texture!, part?.static_texture) : null,
        alphaTest: part?.translucent ? .01 : .5, transparent: !!part?.translucent && textured,
        depthWrite: !part?.translucent || !textured, side: THREE.DoubleSide,
        opacity: textured ? part?.opacity ?? 1 : 1,
        color: textured ? part?.tint_color || '#ffffff' : '#ffffff',
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
      };
      const material = part?.shade === false ? new THREE.MeshBasicMaterial(parameters) : new THREE.MeshLambertMaterial(parameters);
      const geometry = part ? new THREE.BufferGeometry() : new THREE.BoxGeometry(1, 1, 1).translate(.5, .5, .5);
      if (part) {
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(part.positions, 3));
        geometry.setAttribute('uv', new THREE.Float32BufferAttribute(part.uv, 2));
        geometry.computeVertexNormals();
      }
      triangles += (part ? part.positions.length / 9 : 12) * indices.length;
      const mesh = new THREE.InstancedMesh(geometry, material, indices.length);
      const transform = new THREE.Matrix4();
      indices.forEach((index, instance) => {
        const point = voxelPosition(region, index), state = region.palette[region.indices[index]];
        transform.makeTranslation(point[0], point[1], point[2]);
        mesh.setMatrixAt(instance, transform);
        mesh.setColorAt(instance, highlight && diff.has(index) ? new THREE.Color(differenceColors[diff.get(index)!]) : textured ? new THREE.Color('#ffffff') : blockColor(state.name));
      });
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      this.instanceIndices.set(mesh, indices); this.group.add(mesh);
      }
    }
    this.renderer.domElement.dataset.triangles = String(triangles);
    this.renderer.domElement.dataset.modeledVoxels = String(shown.filter(i => !air.has(region.palette[region.indices[i]].name) && this.appearances[region.indices[i]]?.parts?.length).length);
    this.renderer.domElement.dataset.texturedVoxels = String(shown.filter(i => this.texturesEnabled && !air.has(region.palette[region.indices[i]].name) && this.appearances[region.indices[i]]?.parts?.some(p => !!p.texture)).length);
    this.renderer.domElement.dataset.voxelCount = String(shown.length);
    if (reset) this.resetCamera();
  }
  setLayer(layer: number) { this.loadScene(this.region, layer, this.markers, this.highlight); }
  highlightDifferences(markers: Marker[], enabled: boolean) { this.loadScene(this.region, this.layer, markers, enabled); }
  setSelection(index: number | null) {
    if (!this.region || index === null || index >= this.region.indices.length) { this.selection.visible = false; return; }
    const p = voxelPosition(this.region, index);
    this.selection.box.set(new THREE.Vector3(...p as [number, number, number]), new THREE.Vector3(p[0] + 1, p[1] + 1, p[2] + 1));
    this.selection.visible = true;
  }
  private down = (event: PointerEvent) => { this.pointerDown = [event.clientX, event.clientY]; if (this.fallbackFly) this.draggingLook = true; };
  private click = (event: PointerEvent) => {
    this.draggingLook = false;
    if (!this.group.children.length || !this.region || Math.hypot(event.clientX - this.pointerDown[0], event.clientY - this.pointerDown[1]) > 5) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const pointer = this.fly ? new THREE.Vector2(0, 0) : new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    const ray = new THREE.Raycaster(); ray.setFromCamera(pointer, this.camera);
    const hit = ray.intersectObjects(this.group.children)[0];
    if (hit?.instanceId === undefined) return;
    const index = this.instanceIndices.get(hit.object)![hit.instanceId]; this.setSelection(index);
    this.selected({ region: this.region.name, index, position: voxelPosition(this.region, index), state: this.region.palette[this.region.indices[index]], side: this.side });
  };
  startFly() {
    if (this.fly) { this.stopFly(); return; }
    this.requestingLock = true;
    try { const request = this.renderer.domElement.requestPointerLock(); request?.catch(this.enableFallback); }
    catch { this.enableFallback(); }
  }
  private enableFallback = () => {
    if (!this.requestingLock) return;
    this.requestingLock = false;
    if (document.pointerLockElement === this.renderer.domElement) return;
    this.fallbackFly = true; this.fly = true; this.controls.enabled = false;
    this.renderer.domElement.dataset.flying = 'true'; this.renderer.domElement.focus(); this.flying(true);
  };
  private stopFly = () => {
    this.requestingLock = false;
    if (document.pointerLockElement === this.renderer.domElement) document.exitPointerLock();
    this.fly = false; this.fallbackFly = false; this.draggingLook = false; this.keys.clear();
    this.renderer.domElement.dataset.flying = 'false'; this.controls.enabled = true; this.flying(false);
    this.updateFlyTarget(); this.controls.update();
  };
  private canvasBlur = () => { if (this.fallbackFly) this.stopFly(); };
  private lockChanged = () => {
    const next = document.pointerLockElement === this.renderer.domElement;
    if (next === this.fly) return;
    if (next) this.requestingLock = false;
    this.fly = next; this.fallbackFly = false; this.controls.enabled = !next; this.keys.clear(); this.flying(next);
    this.renderer.domElement.dataset.flying = String(next);
    if (!next) {
      const direction = new THREE.Vector3(); this.camera.getWorldDirection(direction);
      this.controls.target.copy(this.camera.position).addScaledVector(direction, 10); this.controls.update();
    }
  };
  private look = (event: MouseEvent) => {
    if (!this.fly || (this.fallbackFly && !this.draggingLook)) return;
    const rotation = new THREE.Euler().setFromQuaternion(this.camera.quaternion, 'YXZ');
    rotation.y -= event.movementX * .002; rotation.x = THREE.MathUtils.clamp(rotation.x - event.movementY * .002, -1.55, 1.55);
    this.camera.quaternion.setFromEuler(rotation); this.updateFlyTarget(); this.onCamera();
  };
  private updateFlyTarget() { const direction = new THREE.Vector3(); this.camera.getWorldDirection(direction); this.controls.target.copy(this.camera.position).addScaledVector(direction, 10); }
  private keyDown = (event: KeyboardEvent) => {
    if (!this.fly) return;
    if (event.code === 'Escape') { this.stopFly(); return; }
    this.keys.add(event.code);
    if (['Space', 'ShiftLeft', 'KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyE'].includes(event.code)) event.preventDefault();
  };
  private keyUp = (event: KeyboardEvent) => { this.keys.delete(event.code); };
  private clearKeys = () => { this.keys.clear(); };
  private animate = () => {
    this.frame = requestAnimationFrame(this.animate);
    const time = performance.now(), dt = Math.min(.05, (time - this.previousTime) / 1000); this.previousTime = time;
    if (this.fly && this.keys.size) {
      const direction = new THREE.Vector3(), right = new THREE.Vector3(); this.camera.getWorldDirection(direction);
      right.crossVectors(direction, this.camera.up).normalize();
      const movement = new THREE.Vector3();
      if (this.keys.has('KeyW')) movement.add(direction); if (this.keys.has('KeyS')) movement.sub(direction);
      if (this.keys.has('KeyD')) movement.add(right); if (this.keys.has('KeyA')) movement.sub(right);
      if (this.keys.has('Space') || this.keys.has('KeyE')) movement.y += 1;
      if (this.keys.has('ShiftLeft') || this.keys.has('KeyQ')) movement.y -= 1;
      this.camera.position.addScaledVector(movement.normalize(), dt * 12); this.updateFlyTarget(); this.onCamera();
    }
    this.renderer.render(this.scene, this.camera);
  };
  dispose() {
    if (document.pointerLockElement === this.renderer.domElement) document.exitPointerLock();
    cancelAnimationFrame(this.frame); this.observer.disconnect(); this.controls.dispose();
    this.renderer.domElement.removeEventListener('pointerdown', this.down); this.renderer.domElement.removeEventListener('pointerup', this.click);
    document.removeEventListener('pointerlockchange', this.lockChanged); document.removeEventListener('mousemove', this.look);
    document.removeEventListener('pointerlockerror', this.enableFallback); this.renderer.domElement.removeEventListener('blur', this.canvasBlur);
    document.removeEventListener('keydown', this.keyDown); document.removeEventListener('keyup', this.keyUp); window.removeEventListener('blur', this.clearKeys);
    this.group.children.forEach(object => { const mesh = object as THREE.Mesh; mesh.geometry?.dispose(); if (Array.isArray(mesh.material)) mesh.material.forEach(m => m.dispose()); else mesh.material?.dispose(); });
    this.selection.geometry.dispose(); (this.selection.material as THREE.Material).dispose();
    this.textures.forEach(texture => texture.dispose()); this.textures.clear();
    this.renderer.dispose(); this.renderer.domElement.remove();
  }
}
