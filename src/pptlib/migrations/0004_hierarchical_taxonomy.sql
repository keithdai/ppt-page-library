ALTER TABLE slide_taxonomy ADD COLUMN subtopic TEXT NOT NULL DEFAULT '';
ALTER TABLE slide_taxonomy ADD COLUMN confidence TEXT NOT NULL DEFAULT 'low';
ALTER TABLE slide_taxonomy ADD COLUMN classification_source TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE slide_taxonomy ADD COLUMN classifier_version TEXT NOT NULL DEFAULT '';

CREATE INDEX idx_slide_taxonomy_subtopic ON slide_taxonomy(subtopic);
CREATE INDEX idx_slide_taxonomy_topic_subtopic ON slide_taxonomy(topic, subtopic);
