"""BigQuery service – data access layer for all USPTO queries."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
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

    def run_query(
        self,
        sql: str,
        params: Optional[List[bigquery.ScalarQueryParameter]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute *sql* and return results as a list of plain dicts."""
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        rows = self.client.query(sql, job_config=job_config).result()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # MDM – Entity name search
    # ------------------------------------------------------------------

    def search_entity_names(
        self,
        and_terms: List[str],
        not_terms: List[str],
    ) -> List[Dict[str, Any]]:
        """Search entity names across both tables with boolean logic.

        Returns unique names with frequency counts and representative
        associations from name_unification.
        """
        like_clauses: List[str] = []
        params: List[bigquery.ScalarQueryParameter] = []

        for i, term in enumerate(and_terms):
            pname = f"and_{i}"
            like_clauses.append(f"UPPER(entity_name) LIKE UPPER(@{pname})")
            params.append(bigquery.ScalarQueryParameter(pname, "STRING", term))

        not_clauses: List[str] = []
        for i, term in enumerate(not_terms):
            pname = f"not_{i}"
            not_clauses.append(f"UPPER(entity_name) NOT LIKE UPPER(@{pname})")
            params.append(bigquery.ScalarQueryParameter(pname, "STRING", term))

        where_parts = like_clauses + not_clauses
        where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        sql = f"""
        WITH all_names AS (
          SELECT app.name AS entity_name
          FROM `{settings.patent_table}`, UNNEST(applicants) AS app
          WHERE app.name IS NOT NULL
          UNION ALL
          SELECT asgn.name AS entity_name
          FROM `{settings.assignments_table}`, UNNEST(assignees) AS asgn
          WHERE asgn.name IS NOT NULL
        ),
        counted AS (
          SELECT entity_name, COUNT(*) AS frequency
          FROM all_names
          {where_sql}
          GROUP BY entity_name
        )
        SELECT
          c.entity_name AS raw_name,
          c.frequency,
          nu.representative_name
        FROM counted c
        LEFT JOIN `{settings.unification_table}` nu
          ON nu.associated_name = c.entity_name
        ORDER BY c.frequency DESC
        LIMIT 1000
        """
        return self.run_query(sql, params)

    # ------------------------------------------------------------------
    # MDM – Name association (normalization)
    # ------------------------------------------------------------------

    def associate_names(
        self,
        representative_name: str,
        associated_names: List[str],
    ) -> int:
        """Associate names with a representative. Handles cascading.

        If any of the associated_names is itself a representative (has names
        under it), those names cascade to the new representative. Returns the
        number of associations created/updated.
        """
        count = 0
        for name in associated_names:
            # Step 1: Cascade — if this name was a representative, re-point
            # all names under it to the new representative.
            cascade_sql = f"""
            UPDATE `{settings.unification_table}`
            SET representative_name = @new_rep
            WHERE representative_name = @old_rep
            """
            cascade_params = [
                bigquery.ScalarQueryParameter("new_rep", "STRING", representative_name),
                bigquery.ScalarQueryParameter("old_rep", "STRING", name),
            ]
            self.run_query(cascade_sql, cascade_params)

            # Step 2: Upsert the association itself.
            merge_sql = f"""
            MERGE `{settings.unification_table}` AS T
            USING (SELECT @rep AS representative_name, @name AS associated_name) AS S
            ON T.associated_name = S.associated_name
            WHEN MATCHED THEN
              UPDATE SET representative_name = S.representative_name
            WHEN NOT MATCHED THEN
              INSERT (representative_name, associated_name)
              VALUES (S.representative_name, S.associated_name)
            """
            merge_params = [
                bigquery.ScalarQueryParameter("rep", "STRING", representative_name),
                bigquery.ScalarQueryParameter("name", "STRING", name),
            ]
            self.run_query(merge_sql, merge_params)
            count += 1

        # Ensure the representative itself has a self-association row.
        self_sql = f"""
        MERGE `{settings.unification_table}` AS T
        USING (SELECT @rep AS representative_name, @rep AS associated_name) AS S
        ON T.associated_name = S.associated_name
        WHEN MATCHED THEN
          UPDATE SET representative_name = S.representative_name
        WHEN NOT MATCHED THEN
          INSERT (representative_name, associated_name)
          VALUES (S.representative_name, S.associated_name)
        """
        self_params = [
            bigquery.ScalarQueryParameter("rep", "STRING", representative_name),
        ]
        self.run_query(self_sql, self_params)

        return count

    def delete_association(self, associated_name: str) -> Dict[str, Any]:
        """Remove an association. If the name is a representative, un-associate
        all names under it. Returns info about what was deleted."""
        # Check if this name is a representative.
        check_sql = f"""
        SELECT COUNT(*) AS cnt
        FROM `{settings.unification_table}`
        WHERE representative_name = @name
        """
        check_params = [
            bigquery.ScalarQueryParameter("name", "STRING", associated_name),
        ]
        result = self.run_query(check_sql, check_params)
        is_representative = result[0]["cnt"] > 0 if result else False

        if is_representative:
            # Delete all rows where this name is the representative.
            del_sql = f"""
            DELETE FROM `{settings.unification_table}`
            WHERE representative_name = @name
            """
        else:
            # Delete only the single association row.
            del_sql = f"""
            DELETE FROM `{settings.unification_table}`
            WHERE associated_name = @name
            """

        del_params = [
            bigquery.ScalarQueryParameter("name", "STRING", associated_name),
        ]
        self.run_query(del_sql, del_params)

        return {
            "deleted": associated_name,
            "was_representative": is_representative,
        }

    # ------------------------------------------------------------------
    # MDM – Address lookup
    # ------------------------------------------------------------------

    def get_addresses(self, name: str) -> List[Dict[str, Any]]:
        """Return unique addresses for an entity name. If the name is a
        representative, aggregates addresses from all associated names."""
        # First get all names to search for (representative + associated).
        names = self._get_all_names_for(name)

        params: List[bigquery.ScalarQueryParameter] = []
        in_clause, params = self._build_in_clause(names, "addr_name", params)

        sql = f"""
        WITH addresses AS (
          SELECT app.street_address, app.city
          FROM `{settings.patent_table}`, UNNEST(applicants) AS app
          WHERE app.name IN ({in_clause})
          UNION ALL
          SELECT asgn.street_address, asgn.city
          FROM `{settings.assignments_table}`, UNNEST(assignees) AS asgn
          WHERE asgn.name IN ({in_clause})
        )
        SELECT DISTINCT street_address, city
        FROM addresses
        WHERE street_address IS NOT NULL OR city IS NOT NULL
        ORDER BY city, street_address
        LIMIT 500
        """
        return self.run_query(sql, params)

    def search_by_address(
        self,
        addresses: List[Dict[str, Optional[str]]],
    ) -> List[Dict[str, Any]]:
        """Find entity names at given addresses across both tables."""
        if not addresses:
            return []

        addr_conditions: List[str] = []
        params: List[bigquery.ScalarQueryParameter] = []

        for i, addr in enumerate(addresses):
            parts: List[str] = []
            if addr.get("street_address"):
                pname = f"street_{i}"
                parts.append(f"UPPER(street_address) = UPPER(@{pname})")
                params.append(
                    bigquery.ScalarQueryParameter(pname, "STRING", addr["street_address"])
                )
            if addr.get("city"):
                pname = f"city_{i}"
                parts.append(f"UPPER(city) = UPPER(@{pname})")
                params.append(
                    bigquery.ScalarQueryParameter(pname, "STRING", addr["city"])
                )
            if parts:
                addr_conditions.append("(" + " AND ".join(parts) + ")")

        if not addr_conditions:
            return []

        addr_where = " OR ".join(addr_conditions)

        sql = f"""
        WITH all_names AS (
          SELECT app.name AS entity_name
          FROM `{settings.patent_table}`, UNNEST(applicants) AS app
          WHERE ({addr_where})
          UNION ALL
          SELECT asgn.name AS entity_name
          FROM `{settings.assignments_table}`, UNNEST(assignees) AS asgn
          WHERE ({addr_where})
        ),
        counted AS (
          SELECT entity_name, COUNT(*) AS frequency
          FROM all_names
          WHERE entity_name IS NOT NULL
          GROUP BY entity_name
        )
        SELECT
          c.entity_name AS raw_name,
          c.frequency,
          nu.representative_name
        FROM counted c
        LEFT JOIN `{settings.unification_table}` nu
          ON nu.associated_name = c.entity_name
        ORDER BY c.frequency DESC
        LIMIT 500
        """
        return self.run_query(sql, params)

    # ------------------------------------------------------------------
    # Name expansion for query integration (Tabs 2 & 3)
    # ------------------------------------------------------------------

    def expand_name_for_query(self, name: str) -> List[str]:
        """Given an entity name, return all names that should be searched.

        Checks name_unification: if the name has a representative, returns
        all names associated with that representative. If the name IS a
        representative, returns all its associated names. Otherwise returns
        just the original name.
        """
        sql = f"""
        WITH rep AS (
          SELECT representative_name
          FROM `{settings.unification_table}`
          WHERE associated_name = @name
          LIMIT 1
        )
        SELECT DISTINCT associated_name
        FROM `{settings.unification_table}`
        WHERE representative_name = (SELECT representative_name FROM rep)
        """
        params = [
            bigquery.ScalarQueryParameter("name", "STRING", name),
        ]
        result = self.run_query(sql, params)
        if result:
            return [row["associated_name"] for row in result]
        return [name]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_all_names_for(self, name: str) -> List[str]:
        """Get all names associated with a name (including itself).

        If the name is a representative, returns all associated names.
        If the name is associated with a representative, returns all names
        under that representative. Otherwise returns just the name.
        """
        sql = f"""
        WITH rep_names AS (
          SELECT representative_name AS rep_name
          FROM `{settings.unification_table}`
          WHERE associated_name = @name
          UNION DISTINCT
          SELECT @name AS rep_name
        )
        SELECT DISTINCT associated_name
        FROM `{settings.unification_table}`
        WHERE representative_name IN (SELECT rep_name FROM rep_names)
        """
        params = [
            bigquery.ScalarQueryParameter("name", "STRING", name),
        ]
        result = self.run_query(sql, params)
        names = [row["associated_name"] for row in result]
        return names if names else [name]

    def _build_in_clause(
        self,
        values: List[str],
        prefix: str,
        params: List[bigquery.ScalarQueryParameter],
    ) -> Tuple[str, List[bigquery.ScalarQueryParameter]]:
        """Build a parameterized IN clause from a list of values."""
        placeholders: List[str] = []
        for i, val in enumerate(values):
            pname = f"{prefix}_{i}"
            placeholders.append(f"@{pname}")
            params.append(bigquery.ScalarQueryParameter(pname, "STRING", val))
        return ", ".join(placeholders), params


bq_service = BigQueryService()
