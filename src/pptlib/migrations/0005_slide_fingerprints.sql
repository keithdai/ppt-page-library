CREATE TABLE slide_fingerprints (
    slide_id TEXT PRIMARY KEY REFERENCES slides(id) ON DELETE CASCADE,
    pixel_sha256 TEXT NOT NULL DEFAULT '',
    perceptual_hash TEXT NOT NULL DEFAULT '',
    visual_vector BLOB,
    text_hash TEXT NOT NULL,
    structure_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    image_width INTEGER NOT NULL DEFAULT 0,
    image_height INTEGER NOT NULL DEFAULT 0,
    fingerprint_version TEXT NOT NULL,
    computed_at TEXT NOT NULL
);

CREATE INDEX idx_slide_fingerprints_pixel
ON slide_fingerprints(pixel_sha256);

CREATE INDEX idx_slide_fingerprints_content
ON slide_fingerprints(content_hash);

CREATE INDEX idx_slide_fingerprints_text
ON slide_fingerprints(text_hash);
