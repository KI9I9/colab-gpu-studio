import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Eye, Image as ImageIcon, Maximize2, Upload, X } from 'lucide-react';
import { assetPreviewUrl, assetUrl, type GalleryAsset } from '../lib/gpu-api';
import styles from '../gpu.module.css';

export type AssetSlotPickerProps = {
  id: string;
  assets: GalleryAsset[];
  value: string;
  onChange: (assetId: string) => void;
  onUpload?: (file: File) => Promise<void> | void;
  uploading?: boolean;
  uploadProgress?: Record<string, number>;
  accept?: string;
  placeholder?: string;
  required?: boolean;
  compact?: boolean;
  emptyText?: string;
};

function readPositiveNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

function formatDimensions(asset: GalleryAsset): string {
  const meta = asset.metadata && typeof asset.metadata === 'object' ? asset.metadata : undefined;
  const width = readPositiveNumber(asset.width) ?? readPositiveNumber(meta?.width);
  const height = readPositiveNumber(asset.height) ?? readPositiveNumber(meta?.height);
  return width && height ? `${width}x${height}` : '';
}

function formatShortTime(asset: GalleryAsset): string {
  const raw = asset.created_at;
  if (typeof raw !== 'string' && typeof raw !== 'number') return '';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatSourceTag(asset: GalleryAsset): string {
  const rawJob = asset.source_job_id ?? asset.gpu_job_id;
  if (typeof rawJob === 'string' && rawJob.trim()) {
    return `任务${rawJob.trim().slice(0, 4)}`;
  }
  return '上传';
}

function isImageAsset(asset: GalleryAsset): boolean {
  const mime = String(asset.mime || '').toLowerCase();
  if (mime.startsWith('image/')) return true;
  if (mime.startsWith('video/') || mime.startsWith('audio/')) return false;
  const name = String(asset.filename || '').toLowerCase();
  return /\.(png|jpe?g|webp|gif|bmp)$/.test(name);
}

function isVideoAsset(asset: GalleryAsset): boolean {
  return String(asset.mime || '').toLowerCase().startsWith('video/');
}

function formatAssetOptionLabel(asset: GalleryAsset, index: number): string {
  const name = asset.filename || asset.id.slice(0, 8);
  const parts = [formatSourceTag(asset)];
  const created = formatShortTime(asset);
  if (created) parts.push(created);
  const dims = formatDimensions(asset);
  if (dims) parts.push(dims);
  return `#${index + 1} ${name} · ${parts.join(' · ')} [${asset.id.slice(0, 4)}]`;
}

function formatAssetMeta(asset: GalleryAsset): string {
  const parts = [formatSourceTag(asset)];
  const dims = formatDimensions(asset);
  if (dims) parts.push(dims);
  const created = formatShortTime(asset);
  if (created) parts.push(created);
  parts.push(`ID:${asset.id.slice(0, 6)}`);
  return parts.join(' · ');
}

/** Visual picker with disambiguated selector, selected preview card, expandable thumbnail grid, and high-res modal. */
export function AssetSlotPicker({
  id,
  assets,
  value,
  onChange,
  onUpload,
  uploading = false,
  uploadProgress,
  accept,
  placeholder = '请选择或上传素材',
  required = false,
  compact = false,
  emptyText = '当前项目还没有可选图片，请先上传一张。',
}: AssetSlotPickerProps) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const userToggledRef = useRef(false);
  const [gridOpen, setGridOpen] = useState(() => Boolean(required && !value && assets.length > 0));
  const [previewAsset, setPreviewAsset] = useState<GalleryAsset | null>(null);
  const [fallbackIds, setFallbackIds] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!userToggledRef.current && required && !value && assets.length > 0) {
      setGridOpen(true);
    }
  }, [assets.length, required, value]);

  useEffect(() => {
    if (!previewAsset) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPreviewAsset(null);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [previewAsset]);

  const selectedIndex = useMemo(
    () => (value ? assets.findIndex((asset) => asset.id === value) : -1),
    [assets, value],
  );
  const selectedAsset = selectedIndex >= 0 ? assets[selectedIndex] : null;
  const previewIndex = useMemo(
    () => (previewAsset ? assets.findIndex((asset) => asset.id === previewAsset.id) : -1),
    [assets, previewAsset],
  );

  function thumbSrc(asset: GalleryAsset): string {
    return isImageAsset(asset) && !fallbackIds[asset.id] ? assetUrl(asset, true) : assetPreviewUrl(asset);
  }

  function markFallback(assetId: string) {
    setFallbackIds((current) => (current[assetId] ? current : { ...current, [assetId]: true }));
  }

  function toggleGrid() {
    userToggledRef.current = true;
    setGridOpen((open) => !open);
  }

  return (
    <div className={styles.assetSlotWrap}>
      <div className={styles.fileControl}>
        <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">{placeholder}</option>
          {assets.map((asset, index) => (
            <option key={asset.id} value={asset.id}>
              {formatAssetOptionLabel(asset, index)}
            </option>
          ))}
        </select>

        {assets.length > 0 ? (
          <button
            type="button"
            className={`button secondary ${compact ? 'small' : ''}`.trim()}
            onClick={toggleGrid}
            aria-expanded={gridOpen}
          >
            <ImageIcon size={14} />
            {gridOpen ? '收起图库' : `图库 (${assets.length})`}
          </button>
        ) : null}

        {onUpload ? (
          <>
            <button
              type="button"
              className={`button secondary ${compact ? 'small' : ''}`.trim()}
              disabled={uploading}
              onClick={() => fileRef.current?.click()}
            >
              <Upload size={14} />
              {uploading ? '上传中…' : '上传'}
            </button>
            <input
              ref={fileRef}
              hidden
              type="file"
              accept={accept}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void onUpload(file);
                event.target.value = '';
              }}
            />
          </>
        ) : null}
      </div>

      {uploadProgress && Object.entries(uploadProgress).length > 0 ? (
        <div className={styles.stack}>
          {Object.entries(uploadProgress).map(([name, progress]) => (
            <small className={styles.hint} key={name}>正在上传 {name}：{progress}%</small>
          ))}
        </div>
      ) : null}

      {selectedAsset ? (
        <div className={styles.assetSelectedCard}>
          <button
            type="button"
            className={styles.assetSelectedThumbBtn}
            onClick={() => setPreviewAsset(selectedAsset)}
            title="点击查看大图预览"
          >
            {isImageAsset(selectedAsset) ? (
              <img
                className={styles.assetSelectedThumb}
                src={thumbSrc(selectedAsset)}
                alt={selectedAsset.filename || selectedAsset.id}
                loading="lazy"
                onError={() => markFallback(selectedAsset.id)}
              />
            ) : (
              <div className={styles.assetSelectedThumbPlaceholder}>
                <ImageIcon size={20} />
              </div>
            )}
          </button>
          <div className={styles.assetSelectedMeta}>
            <strong>#{selectedIndex + 1} · {selectedAsset.filename || selectedAsset.id}</strong>
            <small className="muted">{formatAssetMeta(selectedAsset)}</small>
          </div>
          <div className={styles.assetSelectedActions}>
            <button
              type="button"
              className="button secondary small"
              onClick={() => setPreviewAsset(selectedAsset)}
            >
              <Maximize2 size={14} />
              大图
            </button>
            <button
              type="button"
              className="button ghost small"
              onClick={() => onChange('')}
              aria-label="清除当前选择的素材"
            >
              <X size={14} />
              清除
            </button>
          </div>
        </div>
      ) : null}

      {gridOpen ? (
        assets.length > 0 ? (
          <div className={styles.assetPickerGrid} role="list" aria-label="预览参考素材">
            {assets.map((asset, index) => {
              const selected = asset.id === value;
              const dims = formatDimensions(asset);
              const created = formatShortTime(asset);
              const tileMeta = [formatSourceTag(asset), dims || created || asset.id.slice(0, 4)]
                .filter(Boolean)
                .join(' · ');
              return (
                <div
                  key={asset.id}
                  role="listitem"
                  className={styles.assetPickerTile}
                  data-selected={selected ? 'true' : undefined}
                >
                  <button
                    type="button"
                    className={styles.assetPickerSelectBtn}
                    onClick={() => onChange(asset.id)}
                    aria-pressed={selected}
                    title={formatAssetOptionLabel(asset, index)}
                  >
                    <div className={styles.assetPickerMedia}>
                      {isImageAsset(asset) ? (
                        <img
                          src={thumbSrc(asset)}
                          alt={asset.filename || asset.id}
                          loading="lazy"
                          onError={() => markFallback(asset.id)}
                        />
                      ) : (
                        <div className={styles.assetSelectedThumbPlaceholder}>
                          <ImageIcon size={24} />
                        </div>
                      )}
                      <span className={styles.assetIndexBadge}>#{index + 1}</span>
                      {selected ? (
                        <span className={styles.assetSelectedBadge} aria-label="已选中">
                          <Check size={12} />
                        </span>
                      ) : null}
                    </div>
                    <span className={styles.assetTileTitle}>{asset.filename || asset.id.slice(0, 8)}</span>
                    <span className={styles.assetTileSub}>{tileMeta}</span>
                  </button>
                  <button
                    type="button"
                    className={styles.assetZoomBtn}
                    onClick={() => setPreviewAsset(asset)}
                    aria-label={`预览 ${asset.filename || asset.id}`}
                    title="查看大图"
                  >
                    <Eye size={13} />
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <small className="muted">{emptyText}</small>
        )
      ) : null}

      {previewAsset ? (
        <div
          className={styles.dialogBackdrop}
          role="dialog"
          aria-modal="true"
          aria-label="参考素材大图预览"
          onClick={(event) => {
            if (event.target === event.currentTarget) setPreviewAsset(null);
          }}
        >
          <div className={styles.dialog}>
            <div className={styles.dialogHead}>
              <span className="truncate">
                {previewIndex >= 0 ? `#${previewIndex + 1} · ` : ''}
                {previewAsset.filename || previewAsset.id}
              </span>
              <button
                type="button"
                className={styles.iconGhost}
                onClick={() => setPreviewAsset(null)}
                aria-label="关闭预览"
              >
                <X size={18} />
              </button>
            </div>
            {isVideoAsset(previewAsset) ? (
              <video className={styles.viewerVideo} src={assetPreviewUrl(previewAsset)} controls playsInline />
            ) : (
              <img
                className={styles.viewerImage}
                src={assetPreviewUrl(previewAsset)}
                alt={previewAsset.filename || previewAsset.id}
              />
            )}
            <div className={styles.assetDialogFoot}>
              <small className="muted">{formatAssetMeta(previewAsset)}</small>
              <div className={styles.assetSelectedActions}>
                {previewAsset.id !== value ? (
                  <button
                    type="button"
                    className="button primary small"
                    onClick={() => {
                      onChange(previewAsset.id);
                      setPreviewAsset(null);
                    }}
                  >
                    <Check size={14} />
                    选用这张图
                  </button>
                ) : (
                  <span className="pill" data-state="active">当前已选中</span>
                )}
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => setPreviewAsset(null)}
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

