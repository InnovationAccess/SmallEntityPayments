-- USPTO Data Platform – BigQuery DDL
-- Dataset: uspto_data
-- Run this script using the BigQuery console or `bq` CLI:
--   bq query --use_legacy_sql=false < database/setup.sql

-- Drop tables if they exist so this script is idempotent
DROP TABLE IF EXISTS `uspto_data.patent_file_wrapper`;
DROP TABLE IF EXISTS `uspto_data.patent_assignments`;
DROP TABLE IF EXISTS `uspto_data.maintenance_fee_events`;
DROP TABLE IF EXISTS `uspto_data.name_unification`;

-- Core patent file wrapper table (bibliographic data)
CREATE TABLE `uspto_data.patent_file_wrapper` (
  patent_number   STRING    NOT NULL OPTIONS(description = 'USPTO patent number (e.g. US10000001B2)'),
  invention_title STRING             OPTIONS(description = 'Title of the patented invention'),
  grant_date      DATE               OPTIONS(description = 'Date the patent was granted'),
  applicants      ARRAY<STRUCT<
    name            STRING           OPTIONS(description = 'Applicant entity name as filed'),
    street_address  STRING           OPTIONS(description = 'Street address of the applicant'),
    city            STRING           OPTIONS(description = 'City of the applicant'),
    state           STRING           OPTIONS(description = 'State or province of the applicant'),
    country         STRING           OPTIONS(description = 'Two-letter ISO country code of the applicant'),
    entity_type     STRING           OPTIONS(description = 'LARGE, SMALL, or MICRO')
  >>                                 OPTIONS(description = 'List of applicants associated with this filing')
)
PARTITION BY grant_date
CLUSTER BY patent_number
OPTIONS (
  description = 'Stores raw patent file wrapper data ingested from USPTO bulk data feeds'
);

-- Patent assignment/transfer records
CREATE TABLE `uspto_data.patent_assignments` (
  patent_number   STRING    NOT NULL OPTIONS(description = 'USPTO patent number'),
  recorded_date   DATE               OPTIONS(description = 'Date the assignment was recorded'),
  assignees       ARRAY<STRUCT<
    name            STRING           OPTIONS(description = 'Assignee entity name'),
    street_address  STRING           OPTIONS(description = 'Street address of the assignee'),
    city            STRING           OPTIONS(description = 'City of the assignee'),
    state           STRING           OPTIONS(description = 'State or province of the assignee'),
    country         STRING           OPTIONS(description = 'Two-letter ISO country code of the assignee')
  >>                                 OPTIONS(description = 'List of assignees in this assignment')
)
OPTIONS (
  description = 'Stores patent assignment records from USPTO assignment data'
);

-- Maintenance fee payment history
CREATE TABLE `uspto_data.maintenance_fee_events` (
  patent_number   STRING    NOT NULL OPTIONS(description = 'USPTO patent number'),
  event_code      STRING             OPTIONS(description = 'Fee event code'),
  event_date      DATE               OPTIONS(description = 'Date of the fee event'),
  fee_code        STRING             OPTIONS(description = 'Fee type code'),
  entity_status   STRING             OPTIONS(description = 'Entity size status: SMALL, MICRO, or LARGE')
)
OPTIONS (
  description = 'Stores maintenance fee payment events from USPTO fee data'
);

-- Name unification table for manual MDM normalization
CREATE TABLE `uspto_data.name_unification` (
  representative_name STRING NOT NULL OPTIONS(description = 'The canonical representative entity name'),
  associated_name     STRING NOT NULL OPTIONS(description = 'A variant or typo name linked to the representative')
)
OPTIONS (
  description = 'Manual name normalization associations linking variant entity names to representative names'
);
