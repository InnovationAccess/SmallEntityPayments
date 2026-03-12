-- USPTO Data Platform v4 – Normalized Patent Assignments Schema
-- Dataset: uspto_data
--
-- Normalizes the flat patent_assignments_v3 table into 4 tables
-- linked by reel_frame, matching the natural structure of USPTO
-- assignment XML records.
--
-- Cross-table joins (e.g. to patent_file_wrapper_v2) use
-- application_number as the universal key — every asset has one,
-- while patent_number is only available for granted patents.

-- 1. Assignment records: one row per assignment transaction
CREATE TABLE IF NOT EXISTS `uspto_data.pat_assign_records` (
  reel_frame               STRING NOT NULL,
  reel_no                  INT64,
  frame_no                 INT64,
  recorded_date            DATE NOT NULL,
  last_update_date         DATE,
  page_count               INT64,
  conveyance_text          STRING,
  conveyance_type          STRING,
  employer_assignment      BOOLEAN,
  correspondent_name       STRING,
  correspondent_detail     STRING,
  correspondent_address_1  STRING,
  correspondent_address_2  STRING,
  correspondent_address_3  STRING,
  source_file              STRING,
  ingestion_timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
PARTITION BY DATE_TRUNC(recorded_date, MONTH)
CLUSTER BY reel_frame;

-- 2. Assignors: one row per assignor per assignment
CREATE TABLE IF NOT EXISTS `uspto_data.pat_assign_assignors` (
  reel_frame               STRING NOT NULL,
  assignor_name            STRING,
  assignor_execution_date  DATE,
  ingestion_timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY reel_frame;

-- 3. Assignees: one row per assignee per assignment
CREATE TABLE IF NOT EXISTS `uspto_data.pat_assign_assignees` (
  reel_frame               STRING NOT NULL,
  assignee_name            STRING,
  assignee_address_1       STRING,
  assignee_address_2       STRING,
  assignee_city            STRING,
  assignee_state           STRING,
  assignee_postcode        STRING,
  assignee_country         STRING,
  ingestion_timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY reel_frame, assignee_name;

-- 4. Documents: one row per patent property per assignment
CREATE TABLE IF NOT EXISTS `uspto_data.pat_assign_documents` (
  reel_frame               STRING NOT NULL,
  application_number       STRING,
  filing_date              DATE,
  publication_number       STRING,
  publication_date         DATE,
  patent_number            STRING,
  grant_date               DATE,
  invention_title          STRING,
  ingestion_timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
CLUSTER BY reel_frame, application_number;
