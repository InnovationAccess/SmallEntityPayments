-- SEC Leads: patent importance analysis results from 10-K filings
-- One row per company per analysis date

CREATE TABLE IF NOT EXISTS `uspto-data-app.uspto_data.sec_leads_results` (
  analysis_date         DATE,
  company_name          STRING,
  ticker                STRING,
  cik                   STRING,
  filing_date           DATE,
  filing_url            STRING,
  score                 INT64,
  rationale             STRING,
  key_excerpts_json     STRING,
  stats_json            STRING,
  gist                  STRING,
  secretary_name        STRING,
  secretary_title       STRING,
  general_counsel_name  STRING,
  general_counsel_title STRING,
  board_chair_name      STRING,
  board_chair_title     STRING,
  ceo_name              STRING,
  cfo_name              STRING,
  board_members_json    STRING,
  memo_text             STRING,
  letter_text           STRING,
  apollo_enriched       BOOL DEFAULT FALSE,
  report_gcs_path       STRING,
  created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
PARTITION BY analysis_date
CLUSTER BY ticker, score;
