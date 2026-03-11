-- USPTO Data Platform v2 – BigQuery DDL
-- Dataset: uspto_data
-- This creates the rebuilt tables alongside existing ones.
-- Old tables will be dropped after new data is verified.

-- New tables (create only if not exists)

-- Patent File Wrapper (rebuilt — richer schema from ODP PFW)
CREATE TABLE IF NOT EXISTS `uspto_data.patent_file_wrapper_v2` (
  application_number STRING NOT NULL,
  patent_number STRING,
  invention_title STRING,
  filing_date DATE,
  effective_filing_date DATE,
  grant_date DATE,
  entity_status STRING,
  small_entity_indicator BOOL,
  application_type STRING,
  application_type_category STRING,
  application_status_code INT64,
  application_status STRING,
  first_inventor_name STRING,
  first_applicant_name STRING,
  examiner_name STRING,
  group_art_unit STRING,
  cpc_codes ARRAY<STRING>,
  uspc_class STRING,
  uspc_subclass STRING,
  customer_number INT64,
  earliest_publication_number STRING,
  earliest_publication_date DATE,
  national_stage_indicator BOOL,
  first_inventor_to_file BOOL,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number, patent_number;

-- PFW Transaction History (new)
CREATE TABLE IF NOT EXISTS `uspto_data.pfw_transactions` (
  application_number STRING NOT NULL,
  event_date DATE,
  event_code STRING,
  event_description STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
PARTITION BY event_date
CLUSTER BY application_number;

-- PFW Continuity (new)
CREATE TABLE IF NOT EXISTS `uspto_data.pfw_continuity` (
  application_number STRING NOT NULL,
  claim_parentage_type_code STRING,
  claim_parentage_description STRING,
  parent_application_number STRING,
  parent_filing_date DATE,
  child_application_number STRING,
  parent_patent_number STRING,
  parent_status_code INT64,
  parent_status_description STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;

-- Patent Assignments (rebuilt — denormalized, richer schema)
CREATE TABLE IF NOT EXISTS `uspto_data.patent_assignments_v2` (
  reel_no INT64,
  frame_no INT64,
  reel_frame STRING NOT NULL,
  recorded_date DATE,
  last_update_date DATE,
  purge_indicator STRING,
  page_count INT64,
  correspondent_name STRING,
  conveyance_text STRING,
  assignor_name STRING,
  assignor_execution_date DATE,
  assignee_name STRING,
  assignee_city STRING,
  assignee_state STRING,
  assignee_country STRING,
  assignee_postcode STRING,
  doc_country STRING,
  doc_number STRING NOT NULL,
  doc_kind STRING,
  invention_title STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
PARTITION BY recorded_date
CLUSTER BY doc_number;

-- Maintenance Fee Events (rebuilt — from fixed-width format)
CREATE TABLE IF NOT EXISTS `uspto_data.maintenance_fee_events_v2` (
  patent_number STRING NOT NULL,
  application_number STRING,
  entity_status STRING,
  filing_date DATE,
  grant_date DATE,
  event_date DATE,
  event_code STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
PARTITION BY event_date
CLUSTER BY patent_number;

-- Forward Citations (new)
CREATE TABLE IF NOT EXISTS `uspto_data.forward_citations` (
  cited_patent_number STRING NOT NULL,
  citing_patent_number STRING NOT NULL,
  citing_grant_date DATE NOT NULL,
  citing_application_number STRING,
  citing_filing_date DATE,
  citation_category STRING NOT NULL,
  citing_kind_code STRING,
  cited_kind_code STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
PARTITION BY citing_grant_date
CLUSTER BY cited_patent_number;

-- Ingestion Log (new — tracks ETL state for all sources)
CREATE TABLE IF NOT EXISTS `uspto_data.ingestion_log` (
  source STRING NOT NULL,
  file_name STRING NOT NULL,
  file_date DATE,
  records_processed INT64,
  records_skipped INT64,
  ingestion_start TIMESTAMP,
  ingestion_end TIMESTAMP,
  status STRING
);
