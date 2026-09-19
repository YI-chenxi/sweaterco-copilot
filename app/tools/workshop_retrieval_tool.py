"""Deterministic tool for retrieving factory workshop profiles."""

import csv
from pathlib import Path

from app.agent.contracts import Evidence, ToolResult
from app.tools.data_parsing import parse_workshop_row


class WorkshopRetrievalTool:
    """Retrieve workshop profiles using objective filters."""

    name = "retrieve_factory_workshops"
    description = (
        "Retrieve workshop profiles by workshop ID, status, or supported "
        "product category."
    )

    def __init__(self, workshops_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.workshops_path = (
            workshops_path or project_root / "data" / "workshops.csv"
        )

    @staticmethod
    def _supported_categories(makes: str) -> set[str]:
        """Parse a workshop's supported categories."""
        return {
            category.strip().upper()
            for category in makes.split("+")
            if category.strip()
        }

    def execute(
        self,
        *,
        workshop_id: str | None = None,
        status: str | None = None,
        category: str | None = None,
    ) -> ToolResult:
        """Return workshop profiles matching all supplied filters."""

        normalized_workshop_id = (
            workshop_id.strip().upper()
            if workshop_id and workshop_id.strip()
            else None
        )
        normalized_status = (
            status.strip().upper()
            if status and status.strip()
            else None
        )
        normalized_category = (
            category.strip().upper()
            if category and category.strip()
            else None
        )

        if (
            normalized_workshop_id is None
            and normalized_status is None
            and normalized_category is None
        ):
            return ToolResult(
                data={
                    "workshops": [],
                    "count": 0,
                },
                warnings=[
                    "At least one workshop search filter must be provided."
                ],
            )

        matched_workshops: list[dict[str, str]] = []

        with self.workshops_path.open(
            newline="",
            encoding="utf-8",
        ) as workshops_file:
            for row in csv.DictReader(workshops_file):
                if (
                    normalized_workshop_id is not None
                    and row["workshop_id"].strip().upper()
                    != normalized_workshop_id
                ):
                    continue

                if (
                    normalized_status is not None
                    and row["status"].strip().upper()
                    != normalized_status
                ):
                    continue

                if normalized_category is not None:
                    supported_categories = self._supported_categories(
                        row["makes"]
                    )
                    if normalized_category not in supported_categories:
                        continue

                matched_workshops.append(parse_workshop_row(row))

        evidence = [
            Evidence(
                source="workshops.csv",
                reference=row["workshop_id"],
                detail="Workshop row matched the supplied retrieval filters.",
            )
            for row in matched_workshops
        ]

        warnings: list[str] = []

        if not matched_workshops:
            warnings.append(
                "No workshops matched the supplied filters."
            )

        return ToolResult(
            data={
                "workshops": matched_workshops,
                "count": len(matched_workshops),
            },
            evidence=evidence,
            warnings=warnings,
        )