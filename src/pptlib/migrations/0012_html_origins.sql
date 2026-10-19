CREATE TABLE html_origins (
    deck_id TEXT PRIMARY KEY REFERENCES decks(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    resolved_url TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    capture_profile TEXT NOT NULL,
    resource_count INTEGER NOT NULL DEFAULT 0,
    download_bytes INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_html_origins_source_url ON html_origins(source_url);
