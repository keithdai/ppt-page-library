CREATE TABLE source_roots (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE decks (
    id TEXT PRIMARY KEY,
    canonical_path TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    current_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE deck_versions (
    id TEXT PRIMARY KEY,
    deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    parser_version TEXT NOT NULL,
    slide_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('discovered', 'parsed', 'failed')),
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(deck_id, sha256)
);

CREATE INDEX idx_deck_versions_deck ON deck_versions(deck_id, created_at DESC);

CREATE TABLE slides (
    id TEXT PRIMARY KEY,
    deck_version_id TEXT NOT NULL REFERENCES deck_versions(id) ON DELETE CASCADE,
    slide_number INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    notes_text TEXT NOT NULL DEFAULT '',
    content_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(deck_version_id, slide_number)
);

CREATE INDEX idx_slides_version ON slides(deck_version_id, slide_number);

CREATE VIRTUAL TABLE slide_fts USING fts5(
    slide_id UNINDEXED,
    title,
    body_text,
    notes_text,
    tokenize = 'unicode61 remove_diacritics 2'
);
