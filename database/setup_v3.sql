-- USPTO Data Platform v3 – Patent Assignments Schema Fix
-- Dataset: uspto_data
--
-- Fixes the doc_number ambiguity from v2 by splitting into three separate fields:
--   application_number, publication_number, patent_number
-- Also adds: all assignees, assignee addresses, correspondent addresses,
--   conveyance_type classification, filing/publication/grant dates,
--   employer_assignment placeholder (NULL for now, to be populated from UPAD later).

CREATE TABLE IF NOT EXISTS `uspto_data.patent_assignments_v3` (
  -- Assignment record identifiers
  reel_frame               STRING NOT NULL,
  reel_no                  INT64,
  frame_no                 INT64,

  -- Assignment record metadata
  recorded_date            DATE NOT NULL,
  last_update_date         DATE,
  page_count               INT64,
  conveyance_text          STRING,
  conveyance_type          STRING,
  employer_assignment      BOOLEAN,

  -- Correspondent (law firm that recorded the assignment)
  correspondent_name       STRING,
  correspondent_detail     STRING,
  correspondent_address_1  STRING,
  correspondent_address_2  STRING,
  correspondent_address_3  STRING,

  -- Assignor (entity transferring rights)
  assignor_name            STRING,
  assignor_execution_date  DATE,

  -- Assignee (entity receiving rights)
  assignee_name            STRING,
  assignee_address_1       STRING,
  assignee_address_2       STRING,
  assignee_city            STRING,
  assignee_state           STRING,
  assignee_postcode        STRING,
  assignee_country         STRING,

  -- Document identifiers (split from v2's ambiguous doc_number)
  application_number       STRING,
  filing_date              DATE,
  publication_number       STRING,
  publication_date         DATE,
  patent_number            STRING,
  grant_date               DATE,

  -- Other
  invention_title          STRING,

  -- ETL metadata
  source_file              STRING,
  ingestion_timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
PARTITION BY recorded_date
CLUSTER BY patent_number, application_number;
