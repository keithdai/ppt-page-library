CREATE TABLE selections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE selection_items (
    id TEXT PRIMARY KEY,
    selection_id TEXT NOT NULL REFERENCES selections(id) ON DELETE CASCADE,
    slide_id TEXT NOT NULL REFERENCES slides(id) ON DELETE RESTRICT,
    deck_version_id TEXT NOT NULL,
    source_page_number INTEGER NOT NULL,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(selection_id, sort_order)
);

CREATE INDEX idx_selection_items_selection ON selection_items(selection_id, sort_order);
