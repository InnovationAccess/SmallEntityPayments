"""Application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    GCP_PROJECT_ID: str = os.getenv("GCP_PROJECT_ID", "")
    BIGQUERY_DATASET: str = os.getenv("BIGQUERY_DATASET", "uspto_data")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    @property
    def patent_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.patent_file_wrapper_v2"

    @property
    def assignments_table(self) -> str:
        """Kept for rollback — points to the old flat v3 table."""
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.patent_assignments_v3"

    @property
    def assign_records_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pat_assign_records"

    @property
    def assign_assignors_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pat_assign_assignors"

    @property
    def assign_assignees_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pat_assign_assignees"

    @property
    def assign_documents_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pat_assign_documents"

    @property
    def maintenance_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.maintenance_fee_events_v2"

    @property
    def unification_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.name_unification"

    @property
    def entity_names_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.entity_names"

    @property
    def forward_citations_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.forward_citations"

    @property
    def pfw_transactions_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_transactions"

    @property
    def pfw_continuity_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_continuity"

    @property
    def pfw_applicants_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_applicants"

    @property
    def pfw_inventors_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_inventors"

    @property
    def pfw_child_continuity_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_child_continuity"

    @property
    def pfw_foreign_priority_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_foreign_priority"

    @property
    def pfw_publications_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_publications"

    @property
    def pfw_pta_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_patent_term_adjustment"

    @property
    def pfw_pta_history_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_pta_history"

    @property
    def pfw_correspondence_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_correspondence_address"

    @property
    def pfw_attorneys_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_attorneys"

    @property
    def pfw_document_metadata_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_document_metadata"

    @property
    def pfw_embedded_assignments_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.pfw_embedded_assignments"

    @property
    def sec_leads_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.sec_leads_results"

    @property
    def patent_litigation_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.patent_litigation"

    @property
    def patent_litigation_cache_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.patent_litigation_cache"

    @property
    def prosecution_payment_cache_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.prosecution_payment_cache"

    @property
    def entity_prosecution_cache_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.entity_prosecution_cache"


settings = Settings()
