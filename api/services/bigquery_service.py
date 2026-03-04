"""BigQuery service – thin wrapper around the BigQuery client."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from google.cloud import bigquery

from api.config import settings


class BigQueryService:
    def __init__(self) -> None:
        self._client: Optional[bigquery.Client] = None

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            self._client = bigquery.Client(project=settings.GCP_PROJECT_ID)
        return self._client

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def run_query(self, sql: str, params: Optional[List[bigquery.ScalarQueryParameter]] = None) -> List[Dict[str, Any]]:
        """Execute *sql* and return results as a list of plain dicts."""
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        rows = self.client.query(sql, job_config=job_config).result()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Patent file wrapper
    # ------------------------------------------------------------------

    def search_patents(self, sql: str, params: Optional[List[bigquery.ScalarQueryParameter]] = None) -> List[Dict[str, Any]]:
        return self.run_query(sql, params)

    # ------------------------------------------------------------------
    # MDM – normalized entities
    # ------------------------------------------------------------------

    def search_entities(
        self,
        name: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        country: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search the normalized_entities table using exact / geographic filters."""
        conditions: List[str] = []
        params: List[bigquery.ScalarQueryParameter] = []

        if name:
            # Exact name match against canonical_name OR any alias
            conditions.append(
                "(canonical_name = @name OR EXISTS "
                "(SELECT 1 FROM UNNEST(aliases) AS a WHERE a = @name))"
            )
            params.append(bigquery.ScalarQueryParameter("name", "STRING", name))

        if city:
            conditions.append("LOWER(city) = LOWER(@city)")
            params.append(bigquery.ScalarQueryParameter("city", "STRING", city))

        if state:
            conditions.append("LOWER(state) = LOWER(@state)")
            params.append(bigquery.ScalarQueryParameter("state", "STRING", state))

        if country:
            conditions.append("UPPER(country) = UPPER(@country)")
            params.append(bigquery.ScalarQueryParameter("country", "STRING", country))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM `{settings.entity_table}` {where} LIMIT 200"
        return self.run_query(sql, params)

    def upsert_entity(self, canonical_name: str, aliases: List[str], city: Optional[str], state: Optional[str], country: Optional[str], entity_type: Optional[str]) -> None:
        """Insert or update a canonical entity record via a MERGE statement."""
        sql = f"""
        MERGE `{settings.entity_table}` AS T
        USING (SELECT @canonical_name AS canonical_name) AS S
        ON T.canonical_name = S.canonical_name
        WHEN MATCHED THEN
          UPDATE SET
            aliases      = @aliases,
            city         = @city,
            state        = @state,
            country      = @country,
            entity_type  = @entity_type,
            updated_at   = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
          INSERT (canonical_name, aliases, city, state, country, entity_type, created_at, updated_at)
          VALUES (@canonical_name, @aliases, @city, @state, @country, @entity_type, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
        """
        params = [
            bigquery.ScalarQueryParameter("canonical_name", "STRING", canonical_name),
            bigquery.ArrayQueryParameter("aliases", "STRING", aliases),
            bigquery.ScalarQueryParameter("city", "STRING", city),
            bigquery.ScalarQueryParameter("state", "STRING", state),
            bigquery.ScalarQueryParameter("country", "STRING", country),
            bigquery.ScalarQueryParameter("entity_type", "STRING", entity_type),
        ]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        self.client.query(sql, job_config=job_config).result()


bq_service = BigQueryService()
