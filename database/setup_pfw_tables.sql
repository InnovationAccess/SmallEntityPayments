-- ============================================================
-- PTFWPRE (Patent File Wrapper) — Complete Table Schemas
-- Dataset: uspto_data (location: us-west1)
-- Run once: bq query --location=us-west1 --project_id=uspto-data-app --use_legacy_sql=false < database/setup_pfw_tables.sql
-- ============================================================

-- 1. Modified: patent_file_wrapper_v2 (add 10 new scalar columns)
-- NOTE: This table is TRUNCATED + reloaded on each PTFWPRE update.
-- Use CREATE OR REPLACE to pick up new columns on next reload.
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.patent_file_wrapper_v2` (
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
  -- NEW columns --
  docket_number STRING,
  application_confirmation_number INT64,
  application_status_date DATE,
  application_type_label STRING,
  pct_publication_number STRING,
  pct_publication_date DATE,
  intl_registration_number STRING,
  intl_registration_pub_date DATE,
  uspc_symbol STRING,
  last_ingestion_datetime STRING,
  -- Metadata --
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number, patent_number;


-- 2. NEW: pfw_applicants — from applicantBag[]
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_applicants` (
  application_number STRING NOT NULL,
  applicant_name STRING,
  first_name STRING,
  middle_name STRING,
  last_name STRING,
  name_prefix STRING,
  name_suffix STRING,
  preferred_name STRING,
  country_code STRING,
  address_name_line_1 STRING,
  address_name_line_2 STRING,
  address_city STRING,
  address_region STRING,
  address_region_code STRING,
  address_country_code STRING,
  address_country_name STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number, applicant_name;


-- 3. NEW: pfw_inventors — from inventorBag[]
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_inventors` (
  application_number STRING NOT NULL,
  inventor_name STRING,
  first_name STRING,
  middle_name STRING,
  last_name STRING,
  name_prefix STRING,
  name_suffix STRING,
  preferred_name STRING,
  country_code STRING,
  address_name_line_1 STRING,
  address_name_line_2 STRING,
  address_city STRING,
  address_region STRING,
  address_region_code STRING,
  address_country_code STRING,
  address_country_name STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;


-- 4. NEW: pfw_child_continuity — from childContinuityBag[]
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_child_continuity` (
  application_number STRING NOT NULL,
  child_application_number STRING,
  parent_application_number STRING,
  child_filing_date DATE,
  child_patent_number STRING,
  child_status_code INT64,
  child_status_description STRING,
  claim_parentage_type_code STRING,
  claim_parentage_description STRING,
  first_inventor_to_file BOOL,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;


-- 5. NEW: pfw_foreign_priority — from foreignPriorityBag[]
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_foreign_priority` (
  application_number STRING NOT NULL,
  priority_country STRING,
  priority_filing_date DATE,
  priority_application_number STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;


-- 6. NEW: pfw_publications — from publicationDateBag/publicationSequenceNumberBag/publicationCategoryBag
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_publications` (
  application_number STRING NOT NULL,
  publication_date STRING,
  publication_sequence_number STRING,
  publication_category STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;


-- 7. NEW: pfw_patent_term_adjustment — from patentTermAdjustmentData (1:1 per app)
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_patent_term_adjustment` (
  application_number STRING NOT NULL,
  a_delay_days INT64,
  b_delay_days INT64,
  c_delay_days INT64,
  overlap_days INT64,
  non_overlap_days INT64,
  applicant_delay_days INT64,
  adjustment_total_days INT64,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;


-- 8. NEW: pfw_pta_history — from patentTermAdjustmentData.patentTermAdjustmentHistoryDataBag[]
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_pta_history` (
  application_number STRING NOT NULL,
  event_sequence_number INT64,
  event_date DATE,
  event_description STRING,
  pta_pte_code STRING,
  ip_office_delay_days INT64,
  applicant_delay_days INT64,
  originating_event_sequence INT64,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;


-- 9. NEW: pfw_correspondence_address — from top-level correspondenceAddressBag[]
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_correspondence_address` (
  application_number STRING NOT NULL,
  name_line_1 STRING,
  name_line_2 STRING,
  address_line_1 STRING,
  address_line_2 STRING,
  city STRING,
  region STRING,
  region_code STRING,
  postal_code STRING,
  country_code STRING,
  country_name STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;


-- 10. NEW: pfw_attorneys — from recordAttorney (POA + attorney + customer correspondence merged)
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_attorneys` (
  application_number STRING NOT NULL,
  role STRING,
  first_name STRING,
  middle_name STRING,
  last_name STRING,
  name_prefix STRING,
  name_suffix STRING,
  preferred_name STRING,
  registration_number STRING,
  active_indicator STRING,
  practitioner_category STRING,
  country_code STRING,
  patron_identifier INT64,
  organization_name STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;


-- 11. NEW: pfw_document_metadata — from pgpubDocumentMetaData + grantDocumentMetaData
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_document_metadata` (
  application_number STRING NOT NULL,
  document_type STRING,
  zip_file_name STRING,
  product_identifier STRING,
  file_location_uri STRING,
  file_create_datetime STRING,
  xml_file_name STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number;


-- 12. NEW: pfw_embedded_assignments — from assignmentBag[]
CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.pfw_embedded_assignments` (
  application_number STRING NOT NULL,
  reel_frame STRING,
  reel_number INT64,
  frame_number INT64,
  page_count INT64,
  document_uri STRING,
  received_date DATE,
  recorded_date DATE,
  mailed_date DATE,
  conveyance_text STRING,
  assignor_names STRING,
  assignee_names STRING,
  correspondent_name STRING,
  source_file STRING,
  ingestion_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY application_number, reel_frame;
