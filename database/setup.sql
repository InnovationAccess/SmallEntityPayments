-- USPTO Data Platform – BigQuery DDL
-- Dataset: uspto_data
-- Run this script using the BigQuery console or `bq` CLI:
--   bq query --use_legacy_sql=false < database/setup.sql

-- Drop tables if they exist so this script is idempotent
DROP TABLE IF EXISTS `uspto_data.patent_file_wrapper`;
DROP TABLE IF EXISTS `uspto_data.normalized_entities`;

-- Core patent file wrapper table
CREATE TABLE `uspto_data.patent_file_wrapper` (
  patent_number   STRING    NOT NULL OPTIONS(description = 'USPTO patent number (e.g. US10000001B2)'),
  invention_title STRING             OPTIONS(description = 'Title of the patented invention'),
  grant_date      DATE               OPTIONS(description = 'Date the patent was granted'),
  applicants      ARRAY<STRUCT<
    name          STRING             OPTIONS(description = 'Applicant entity name as filed'),
    city          STRING             OPTIONS(description = 'City of the applicant'),
    state         STRING             OPTIONS(description = 'State or province of the applicant'),
    country       STRING             OPTIONS(description = 'Two-letter ISO country code of the applicant'),
    entity_type   STRING             OPTIONS(description = 'LARGE, SMALL, or MICRO')
  >>                                 OPTIONS(description = 'List of applicants associated with this filing')
)
PARTITION BY grant_date
CLUSTER BY patent_number
OPTIONS (
  description = 'Stores raw patent file wrapper data ingested from USPTO bulk data feeds'
);

-- MDM canonical entity table used by the UI-driven normalisation workflow
CREATE TABLE `uspto_data.normalized_entities` (
  canonical_name  STRING    NOT NULL OPTIONS(description = 'The authoritative, normalised entity name'),
  aliases         ARRAY<STRING>      OPTIONS(description = 'Alternative or raw names that map to this canonical name'),
  city            STRING             OPTIONS(description = 'Primary city associated with this entity'),
  state           STRING             OPTIONS(description = 'Primary state associated with this entity'),
  country         STRING             OPTIONS(description = 'Two-letter ISO country code'),
  entity_type     STRING             OPTIONS(description = 'LARGE, SMALL, or MICRO'),
  created_at      TIMESTAMP          OPTIONS(description = 'Row creation timestamp'),
  updated_at      TIMESTAMP          OPTIONS(description = 'Row last-updated timestamp')
)
OPTIONS (
  description = 'Master Data Management table holding canonical entity names and their aliases'
);
