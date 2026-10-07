ALTER TABLE deck_versions ADD COLUMN source_format TEXT NOT NULL DEFAULT 'pptx';
ALTER TABLE deck_versions ADD COLUMN canonical_format TEXT NOT NULL DEFAULT 'pptx_package';
ALTER TABLE deck_versions ADD COLUMN dependencies_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE deck_versions ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE deck_versions ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE deck_versions ADD COLUMN renderer_version TEXT NOT NULL DEFAULT '';

ALTER TABLE slides ADD COLUMN page_key TEXT NOT NULL DEFAULT '';
ALTER TABLE slides ADD COLUMN page_kind TEXT NOT NULL DEFAULT 'ooxml';
ALTER TABLE slides ADD COLUMN composition_ref_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE slides ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX idx_deck_versions_source_format ON deck_versions(source_format);
