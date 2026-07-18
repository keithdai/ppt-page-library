CREATE TABLE slide_taxonomy (
    slide_id TEXT PRIMARY KEY REFERENCES slides(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    page_type TEXT NOT NULL,
    classified_at TEXT NOT NULL
);

CREATE INDEX idx_slide_taxonomy_topic ON slide_taxonomy(topic);
CREATE INDEX idx_slide_taxonomy_page_type ON slide_taxonomy(page_type);
