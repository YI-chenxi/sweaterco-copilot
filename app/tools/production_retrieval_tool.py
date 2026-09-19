"""Deterministic tool for retrieving factory production records."""

import csv
from datetime import date
from pathlib import Path

from app.agent.contracts import Evidence, ToolResult
from app.tools.data_parsing import parse_production_row


class ProductionRetrievalTool:
    """Retrieve production records and deterministic summary statistics."""

    name = "retrieve_factory_production"
    description = (
        "Retrieve factory production records by stage, exact date, "
        "or date range, with basic deterministic summary statistics."
    )

    def __init__(self, production_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.production_path = (
            production_path or project_root / "data" / "production_log.csv"
        )

    def execute(
        self,
        *,
        stage: str | None = None,
        production_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> ToolResult:
        """Return production records matching all supplied filters."""

        if (
            stage is None
            and production_date is None
            and start_date is None
            and end_date is None
        ):
            return ToolResult(
                data={
                    "records": [],
                    "count": 0,
                    "summary": {},
                },
                warnings=[
                    "At least one production search filter must be provided."
                ],
            )

        if production_date is not None and (
            start_date is not None or end_date is not None
        ):
            raise ValueError(
                "production_date cannot be combined with start_date or end_date."
            )

        if (
            start_date is not None
            and end_date is not None
            and start_date > end_date
        ):
            raise ValueError(
                "start_date must not be later than end_date."
            )

        normalized_stage = stage.strip().upper() if stage else None
        matched_records: list[dict[str, str]] = []

        with self.production_path.open(
            newline="",
            encoding="utf-8",
        ) as production_file:
            for row in csv.DictReader(production_file):
                row_date = date.fromisoformat(row["date"])

                if normalized_stage is not None:
                    if row["stage"].strip().upper() != normalized_stage:
                        continue

                if production_date is not None and row_date != production_date:
                    continue

                if start_date is not None and row_date < start_date:
                    continue

                if end_date is not None and row_date > end_date:
                    continue

                matched_records.append(parse_production_row(row))

        evidence = [
            Evidence(
                source="production_log.csv",
                reference=f"{row['date']}:{row['stage']}",
                detail="Production row matched the supplied retrieval filters.",
            )
            for row in matched_records
        ]

        warnings: list[str] = []

        if not matched_records:
            warnings.append(
                "No production records matched the supplied filters."
            )

        pieces = [
             row["pieces_completed"]
             for row in matched_records
         ]

        summary = {}

        if pieces:
            summary = {
                "total_pieces": sum(pieces),
                "average_pieces": sum(pieces) / len(pieces),
                "min_pieces": min(pieces),
                "max_pieces": max(pieces),
            }

        return ToolResult(
            data={
                "records": matched_records,
                "count": len(matched_records),
                "summary": summary,
            },
            evidence=evidence,
            warnings=warnings,
        )