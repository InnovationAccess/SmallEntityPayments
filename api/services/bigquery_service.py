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
        """Search pre-computed entity_names table with boolean logic.

        Returns unique names with frequency counts and representative
        associations from name_unification.
        """
        like_clauses: List[str] = []
        params: List[bigquery.ScalarQueryParameter] = []

        for i, term in enumerate(and_terms):
            pname = f"and_{i}"
            like_clauses.append(f"UPPER(en.entity_name) LIKE UPPER(@{pname})")
            params.append(bigquery.ScalarQueryParameter(pname, "STRING", term))

        not_clauses: List[str] = []
        for i, term in enumerate(not_terms):
            pname = f"not_{i}"
            not_clauses.append(f"UPPER(en.entity_name) NOT LIKE UPPER(@{pname})")
            params.append(bigquery.ScalarQueryParameter(pname, "STRING", term))

        where_parts = like_clauses + not_clauses
        where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        sql = f"""
        SELECT
          COALESCE(nu.representative_name, en.entity_name) AS raw_name,
          SUM(en.frequency) AS frequency,
          MAX(nu.representative_name) AS representative_name
        FROM `{settings.entity_names_table}` en
        LEFT JOIN `{settings.unification_table}` nu
          ON UPPER(nu.associated_name) = UPPER(en.entity_name)
        {where_sql}
        GROUP BY COALESCE(nu.representative_name, en.entity_name)
        ORDER BY frequency DESC
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
        """Associate names with a representative using batched DML.

        Three operations regardless of batch size:
        1. Cascade UPDATE: re-point names under any associated_name
        2. Bulk MERGE: upsert all associations
        3. Self MERGE: ensure representative has self-association
        """
        # Build parameterized name list.
        name_params = [
            bigquery.ScalarQueryParameter(f"name_{i}", "STRING", name)
            for i, name in enumerate(associated_names)
        ]
        name_list = ", ".join(f"@name_{i}" for i in range(len(associated_names)))
        rep_param = bigquery.ScalarQueryParameter("rep", "STRING", representative_name)

        # DML 1: Cascade — re-point any names under the associated names.
        cascade_sql = f"""
        UPDATE `{settings.unification_table}`
        SET representative_name = @rep
        WHERE representative_name IN ({name_list})
        """
        self.run_query(cascade_sql, [rep_param] + name_params)

        # DML 2: Bulk MERGE — upsert all associations at once.
        union_parts = [
            f"SELECT @rep AS representative_name, @name_{i} AS associated_name"
            for i in range(len(associated_names))
        ]
        merge_sql = f"""
        MERGE `{settings.unification_table}` AS T
        USING ({' UNION ALL '.join(union_parts)}) AS S
        ON T.associated_name = S.associated_name
        WHEN MATCHED THEN
          UPDATE SET representative_name = S.representative_name
        WHEN NOT MATCHED THEN
          INSERT (representative_name, associated_name)
          VALUES (S.representative_name, S.associated_name)
        """
        self.run_query(merge_sql, [rep_param] + name_params)

        # DML 3: Self-association for the representative.
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
        self.run_query(self_sql, [rep_param])

        return len(associated_names)

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
        """Return unique addresses for an entity name from assignment records.

        Uses the normalized pat_assign_assignees table.
        """
        names = self._get_all_names_for(name)

        params: List[bigquery.ScalarQueryParameter] = []
        in_clause, params = self._build_in_clause(names, "addr_name", params)

        sql = f"""
        SELECT DISTINCT assignee_city AS city, assignee_state AS state,
               assignee_country AS country
        FROM `{settings.assign_assignees_table}`
        WHERE assignee_name IN ({in_clause})
          AND (assignee_city IS NOT NULL OR assignee_state IS NOT NULL)
        ORDER BY assignee_city, assignee_state
        LIMIT 500
        """
        return self.run_query(sql, params)

    def search_by_address(
        self,
        addresses: List[Dict[str, Optional[str]]],
    ) -> List[Dict[str, Any]]:
        """Find entity names at given addresses from assignment records.

        Uses the normalized pat_assign_assignees table.
        """
        if not addresses:
            return []

        addr_conditions: List[str] = []
        params: List[bigquery.ScalarQueryParameter] = []

        for i, addr in enumerate(addresses):
            parts: List[str] = []
            if addr.get("city"):
                pname = f"city_{i}"
                parts.append(f"UPPER(assignee_city) = UPPER(@{pname})")
                params.append(
                    bigquery.ScalarQueryParameter(pname, "STRING", addr["city"])
                )
            if addr.get("state"):
                pname = f"state_{i}"
                parts.append(f"UPPER(assignee_state) = UPPER(@{pname})")
                params.append(
                    bigquery.ScalarQueryParameter(pname, "STRING", addr["state"])
                )
            if addr.get("country"):
                pname = f"country_{i}"
                parts.append(f"UPPER(assignee_country) = UPPER(@{pname})")
                params.append(
                    bigquery.ScalarQueryParameter(pname, "STRING", addr["country"])
                )
            if parts:
                addr_conditions.append("(" + " AND ".join(parts) + ")")

        if not addr_conditions:
            return []

        addr_where = " OR ".join(addr_conditions)

        sql = f"""
        WITH addr_names AS (
          SELECT DISTINCT assignee_name AS entity_name
          FROM `{settings.assign_assignees_table}`
          WHERE ({addr_where}) AND assignee_name IS NOT NULL
        )
        SELECT
          an.entity_name AS raw_name,
          COALESCE(en.frequency, 0) AS frequency,
          nu.representative_name
        FROM addr_names an
        LEFT JOIN `{settings.entity_names_table}` en
          ON en.entity_name = an.entity_name
        LEFT JOIN `{settings.unification_table}` nu
          ON nu.associated_name = an.entity_name
        ORDER BY frequency DESC
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
          WHERE LOWER(associated_name) = LOWER(@name)
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
          WHERE LOWER(associated_name) = LOWER(@name)
          UNION DISTINCT
          SELECT @name AS rep_name
        )
        SELECT DISTINCT associated_name
        FROM `{settings.unification_table}`
        WHERE representative_name IN (SELECT rep_name FROM rep_names)
           OR LOWER(representative_name) IN (SELECT LOWER(rep_name) FROM rep_names)
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
