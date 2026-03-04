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
    def entity_table(self) -> str:
        return f"{self.GCP_PROJECT_ID}.{self.BIGQUERY_DATASET}.normalized_entities"


settings = Settings()
