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


settings = Settings()
