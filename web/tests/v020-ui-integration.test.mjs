import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { build } from 'esbuild';

const root = join(process.cwd(), 'src');
const read = (name) => readFileSync(join(root, name), 'utf8');

test('App owns one runtime choice and polls account/instance resources', () => {
  const source = read('App.tsx');
  assert.match(source, /runtimeChoice/);
  assert.match(source, /getGpuAccounts|getGpuInstances|apiFetch/);
  assert.match(source, /execution_policy/);
  assert.match(source, /retain_idle_minutes/);
  assert.doesNotMatch(source, /shutdown_after_job/);
  assert.doesNotMatch(source, /idle_grace_minutes/);
});

test('create page delegates lifecycle controls to RuntimeSelection', () => {
  const create = read('pages/CreatePage.tsx');
  const selection = read('components/RuntimeSelection.tsx');
  assert.match(create, /<RuntimeSelection\b/);
  assert.match(create, /value=\{runtimeChoice\}/);
  assert.match(create, /onChange=\{onRuntimeChoiceChange\}/);
  assert.match(selection, /RuntimeLifecycle = 'save' \| 'batch' \| 'smart' \| 'retain'/);
  assert.match(selection, /id="runtime-lifecycle"/);
  assert.match(selection, /id="retain-idle-minutes"/);
  assert.match(selection, /retainIdleMinutes/);
  assert.doesNotMatch(create, /id="batch-mode-select"/);
});

test('settings uses account directory and compute uses instance center', () => {
  const settings = read('pages/SettingsPage.tsx');
  const compute = read('pages/ComputePage.tsx');
  assert.match(settings, /AccountDirectory/);
  assert.match(compute, /InstanceCenter/);
  assert.doesNotMatch(compute, /GoogleOAuthSetup/);
});

test('App wires retention success back into the instance state without duplicates', () => {
  const source = read('App.tsx');
  assert.match(source, /const updatedInstance = await apiFetch<GpuInstance>\(/);
  assert.match(source, /method: 'PATCH'/);
  assert.match(source, /\/retention/);
  assert.match(source, /setInstances\(\(current\) =>/);
  assert.match(source, /findIndex\(\(item\) => item\.instance_id === updatedInstance\.instance_id\)/);
});

test('jobs render machine history and only expose retention action through callback', () => {
  const source = read('pages/JobsPage.tsx');
  assert.match(source, /instance_id/);
  assert.match(source, /account_alias_snapshot/);
  assert.match(source, /onRetain\?:/);
  assert.match(source, /onInstance\?:/);
  assert.match(source, /key=\{jobKey\}/);
});

test('settings and compute render read-only real retention verification badge', async () => {
  const apiTypes = read('lib/gpu-api.ts');
  assert.match(apiTypes, /real_retention_verified\?: boolean/);

  const { outputFiles } = await build({
    stdin: {
      contents: `
        import { createElement } from 'react';
        import { renderToStaticMarkup } from 'react-dom/server';
        import { SettingsPanel } from './src/pages/SettingsPage';
        import { ComputePage } from './src/pages/ComputePage';

        export function renderSettings(settings) {
          return renderToStaticMarkup(createElement(SettingsPanel, {
            settings,
            usage: null,
            specs: [],
            workflows: [],
            loading: '',
            error: '',
            onConnect() {},
            onDisconnect() {},
            onCheck() {},
            onStop() {},
            onImport() {},
            onValidate() {},
            onPublish() {},
            onRollback() {},
            onLogout() {},
          }));
        }

        export function renderCompute(settings) {
          return renderToStaticMarkup(createElement(ComputePage, {
            settings,
            usage: null,
          }));
        }
      `,
      resolveDir: process.cwd(),
      loader: 'tsx',
    },
    bundle: true,
    platform: 'node',
    format: 'cjs',
    define: {
      'import.meta.env.VITE_API_BASE_URL': '""',
      'import.meta.env.VITE_GIT_SHA': '""',
    },
    write: false,
    outfile: 'retention-badge-check.cjs',
    external: ['react', 'react-dom/server'],
    loader: { '.css': 'empty' },
  });

  const mod = { exports: {} };
  new Function('require', 'module', 'exports', outputFiles[0].text)(
    createRequire(import.meta.url),
    mod,
    mod.exports,
  );
  const { renderSettings, renderCompute } = mod.exports;
  const prevStorage = globalThis.localStorage;
  const prevWindow = globalThis.window;
  globalThis.localStorage = {
    getItem() { return 'dark'; },
    setItem() {},
    removeItem() {},
  };
  globalThis.window = {
    location: { origin: 'https://studio.example.test' },
  };

  try {
    const unlockedReal = { real_enabled: true, real_retention_verified: true };
    const unlockedSettingsHtml = renderSettings(unlockedReal);
    const unlockedComputeHtml = renderCompute(unlockedReal);
    assert.match(unlockedSettingsHtml, /保留复用：已解锁/);
    assert.match(unlockedSettingsHtml, /class="pill solid"/);
    assert.match(unlockedComputeHtml, /保留复用：已解锁/);
    assert.match(unlockedComputeHtml, /真实 GPU/);

    const lockedReal = { real_enabled: true, real_retention_verified: false };
    assert.match(renderSettings(lockedReal), /保留复用：未解锁/);
    assert.doesNotMatch(renderSettings(lockedReal), /class="pill solid"/);
    assert.match(renderCompute(lockedReal), /保留复用：未解锁/);

    const mockSettings = { real_enabled: false, real_retention_verified: false };
    const mockSettingsHtml = renderSettings(mockSettings);
    const mockComputeHtml = renderCompute(mockSettings);
    assert.match(mockSettingsHtml, /保留复用：未解锁/);
    assert.doesNotMatch(mockSettingsHtml, /class="pill solid"/);
    assert.match(mockComputeHtml, /模拟模式/);
    assert.doesNotMatch(mockComputeHtml, /保留复用/);
  } finally {
    if (prevStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = prevStorage;
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
  }
});

test('App refreshes reference images across tab switch and completed jobs without clearing selectedAssets', () => {
  const source = read('App.tsx');
  assert.match(source, /const completedJobsKey = useMemo\(/);
  assert.match(
    source,
    /useEffect\(\(\) => \{\s*setReferenceImages\(\[\]\);\s*setSelectedAssets\(\[\]\);\s*\}, \[projectId, loggedIn\]\);/
  );
  assert.match(
    source,
    /getReferenceAssets\(projectId\)[\s\S]*\[projectId, loggedIn, tab, completedJobsKey\]/
  );
});

test('Gallery preview and download URLs are separated to avoid browser cache lock', async () => {
  const { outputFiles } = await build({
    stdin: {
      contents: `
        export { assetUrl, assetPreviewUrl } from './src/lib/gpu-api';
      `,
      resolveDir: process.cwd(),
      loader: 'ts',
    },
    bundle: true,
    platform: 'node',
    format: 'cjs',
    define: {
      'import.meta.env.VITE_API_BASE_URL': '""',
    },
    write: false,
    outfile: 'asset-url-check.cjs',
  });
  const mod = { exports: {} };
  new Function('require', 'module', 'exports', outputFiles[0].text)(
    createRequire(import.meta.url),
    mod,
    mod.exports,
  );
  const { assetUrl, assetPreviewUrl } = mod.exports;
  const sample = { id: 'a123', project_id: 'p1', mime: 'video/mp4' };
  assert.equal(assetPreviewUrl(sample), '/api/assets/a123/preview');
  assert.equal(assetUrl(sample, false), '/api/assets/a123/download');
  assert.equal(assetUrl(sample, true), '/api/assets/a123/thumbnail');

  const appSource = read('App.tsx');
  assert.match(appSource, /<video\b[^>]*src=\{assetPreviewUrl\(viewer\)\}/);
  assert.match(appSource, /<img\b[^>]*src=\{assetPreviewUrl\(viewer\)\}/);
  assert.match(appSource, /<a\b[^>]*href=\{assetUrl\(viewer\)\}\s+download=\{viewer\.filename \|\| viewer\.id\}>/);
  assert.doesNotMatch(appSource, /href=\{assetUrl\(viewer\)\}[^>]*target="_blank"/);

  const gallerySource = read('pages/GalleryPage.tsx');
  assert.match(gallerySource, /<video\b[^>]*src=\{assetPreviewUrl\(asset\)\}/);
  assert.match(gallerySource, /<a\b[^>]*href=\{assetUrl\(selected\)\}\s+download=\{selected\.filename \|\| selected\.id\}>/);

  const pickerSource = read('components/AssetSlotPicker.tsx');
  assert.match(pickerSource, /<video\b[^>]*src=\{assetPreviewUrl\(previewAsset\)\}/);
  assert.match(pickerSource, /src=\{assetPreviewUrl\(previewAsset\)\}/);
});
