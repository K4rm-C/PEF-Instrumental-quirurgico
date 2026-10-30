-- Volumen Postgres ya inicializado: aplicar a mano (initdb solo corre 001).
-- Clip de ráfaga v1 = MP4 H.264. gif se conserva por legado.

ALTER TABLE media_asset DROP CONSTRAINT chk_media_asset_kind;

ALTER TABLE media_asset ADD CONSTRAINT chk_media_asset_kind CHECK (
  kind IN ('jpeg', 'gif', 'mp4', 'mkv', 'webm', 'weights', 'other')
);
