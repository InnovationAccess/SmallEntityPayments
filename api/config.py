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
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.patent_file_wrapper"

    @property
    def assignments_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.patent_assignments"

    @property
    def maintenance_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.maintenance_fee_events"

    @property
    def unification_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.name_unification"

    @property
    def entity_names_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.entity_names"


settings = Settings()
