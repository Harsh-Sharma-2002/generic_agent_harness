"""Temporary checkpoints for batched offline evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from learning.evaluation.execution import ExecutionRecord
from learning.evaluation.metrics import (
    MetricName,
    MetricResult,
    MetricSource,
)


_CHECKPOINT_ROOT = Path(
    "learning/data/tmp"
)


class EvaluationCheckpoint:
    """
    Temporary semantic-batch checkpoint.

    Completed semantic batches are persisted so a later retry can
    resume without repeating successful LLM judge calls.

    This object does not decide when a checkpoint should be deleted.
    The tool layer owns that lifecycle.
    """

    def __init__(
        self,
        *,
        checkpoint_id: str,
        batch_size: int,
        request_ids: list[str],
        path: Path,
        completed_batches: dict[str, Any],
    ) -> None:
        self.checkpoint_id = checkpoint_id
        self.batch_size = batch_size
        self.request_ids = request_ids
        self.path = path
        self.completed_batches = (
            completed_batches
        )

    @classmethod
    def open(
        cls,
        *,
        checkpoint_id: str,
        executions: list[ExecutionRecord],
        batch_size: int,
    ) -> EvaluationCheckpoint:
        """
        Load an existing checkpoint or create a new one.

        An existing checkpoint is accepted only when its workload
        and batch size exactly match the current evaluation.
        """

        path = checkpoint_path(
            checkpoint_id
        )

        request_ids = [
            execution.request_id
            for execution in executions
        ]

        if not path.exists():
            checkpoint = cls(
                checkpoint_id=checkpoint_id,
                batch_size=batch_size,
                request_ids=request_ids,
                path=path,
                completed_batches={},
            )

            checkpoint._write()

            return checkpoint

        try:
            raw = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

        except (
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "Evaluation checkpoint could "
                "not be loaded."
            ) from exc

        if not isinstance(
            raw,
            dict,
        ):
            raise ValueError(
                "Evaluation checkpoint must "
                "contain a JSON object."
            )

        if (
            raw.get("checkpoint_id")
            != checkpoint_id
        ):
            raise ValueError(
                "Checkpoint ID mismatch."
            )

        if (
            raw.get("batch_size")
            != batch_size
        ):
            raise ValueError(
                "Checkpoint batch size does not "
                "match the current evaluation."
            )

        if (
            raw.get("request_ids")
            != request_ids
        ):
            raise ValueError(
                "Checkpoint workload does not "
                "match the current evaluation."
            )

        completed_batches = raw.get(
            "completed_batches"
        )

        if not isinstance(
            completed_batches,
            dict,
        ):
            raise ValueError(
                "Checkpoint completed_batches "
                "must be an object."
            )

        return cls(
            checkpoint_id=checkpoint_id,
            batch_size=batch_size,
            request_ids=request_ids,
            path=path,
            completed_batches=completed_batches,
        )

    def has_batch(
        self,
        batch_number: int,
    ) -> bool:
        """Return whether a semantic batch is already complete."""

        return (
            str(batch_number)
            in self.completed_batches
        )

    def load_batch(
        self,
        *,
        batch_number: int,
        executions: list[ExecutionRecord],
    ) -> dict[
        str,
        tuple[MetricResult, ...],
    ]:
        """
        Restore one completed semantic batch.

        The request IDs must exactly match the current batch.
        """

        batch_key = str(
            batch_number
        )

        raw_batch = (
            self.completed_batches.get(
                batch_key
            )
        )

        if not isinstance(
            raw_batch,
            dict,
        ):
            raise ValueError(
                f"Checkpoint batch "
                f"{batch_number} is missing."
            )

        expected_request_ids = [
            execution.request_id
            for execution in executions
        ]

        if (
            raw_batch.get(
                "request_ids"
            )
            != expected_request_ids
        ):
            raise ValueError(
                "Checkpoint batch does not "
                "match the current evaluation "
                f"for batch {batch_number}."
            )

        return _deserialize_results(
            raw_batch.get(
                "semantic_results"
            )
        )

    def save_batch(
        self,
        *,
        batch_number: int,
        executions: list[ExecutionRecord],
        semantic_results: dict[
            str,
            tuple[MetricResult, ...],
        ],
    ) -> None:
        """
        Persist one successfully completed semantic batch.
        """

        request_ids = [
            execution.request_id
            for execution in executions
        ]

        result_ids = set(
            semantic_results
        )

        if result_ids != set(
            request_ids
        ):
            raise ValueError(
                "Semantic results do not match "
                "the checkpoint batch workload."
            )

        self.completed_batches[
            str(batch_number)
        ] = {
            "request_ids": request_ids,
            "semantic_results": (
                _serialize_results(
                    semantic_results
                )
            ),
        }

        self._write()

    @property
    def completed_count(
        self,
    ) -> int:
        """Number of semantic batches already checkpointed."""

        return len(
            self.completed_batches
        )

    def _write(
        self,
    ) -> None:
        """
        Atomically persist checkpoint state.
        """

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        data = {
            "checkpoint_id": (
                self.checkpoint_id
            ),
            "batch_size": (
                self.batch_size
            ),
            "request_ids": (
                self.request_ids
            ),
            "completed_batches": (
                self.completed_batches
            ),
        }

        temporary_path = (
            self.path.with_suffix(
                ".tmp"
            )
        )

        temporary_path.write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )

        temporary_path.replace(
            self.path
        )


def checkpoint_path(
    checkpoint_id: str,
) -> Path:
    """
    Resolve the temporary path for one checkpoint.
    """

    if not isinstance(
        checkpoint_id,
        str,
    ):
        raise ValueError(
            "checkpoint_id must be a string."
        )

    checkpoint_id = (
        checkpoint_id.strip()
    )

    if not checkpoint_id:
        raise ValueError(
            "checkpoint_id must not be empty."
        )

    if not all(
        character.isalnum()
        or character in {
            "-",
            "_",
        }
        for character in checkpoint_id
    ):
        raise ValueError(
            "checkpoint_id contains "
            "invalid characters."
        )

    return (
        _CHECKPOINT_ROOT
        / checkpoint_id
        / "semantic_batches.json"
    )


def delete_checkpoint(
    checkpoint_id: str,
) -> None:
    """
    Delete one temporary evaluation checkpoint.

    The tool layer should call this only after the permanent
    evaluation artifact has been successfully persisted.
    """

    path = checkpoint_path(
        checkpoint_id
    )

    if path.exists():
        path.unlink()

    directory = path.parent

    if (
        directory.exists()
        and not any(
            directory.iterdir()
        )
    ):
        directory.rmdir()


def _serialize_results(
    results: dict[
        str,
        tuple[MetricResult, ...],
    ],
) -> dict[
    str,
    list[dict[str, Any]],
]:
    """Serialize semantic MetricResults for temporary storage."""

    return {
        request_id: [
            {
                "name": metric.name.value,
                "source": metric.source.value,
                "value": metric.value,
                "metadata": metric.metadata,
            }
            for metric in metrics
        ]
        for request_id, metrics
        in results.items()
    }


def _deserialize_results(
    data: Any,
) -> dict[
    str,
    tuple[MetricResult, ...],
]:
    """Restore semantic MetricResults from temporary storage."""

    if not isinstance(
        data,
        dict,
    ):
        raise ValueError(
            "Checkpoint semantic_results "
            "must be an object."
        )

    restored: dict[
        str,
        tuple[MetricResult, ...],
    ] = {}

    for request_id, raw_metrics in data.items():
        if not isinstance(
            request_id,
            str,
        ):
            raise ValueError(
                "Checkpoint request ID "
                "must be a string."
            )

        if not isinstance(
            raw_metrics,
            list,
        ):
            raise ValueError(
                "Checkpoint metrics "
                "must be a list."
            )

        metrics: list[
            MetricResult
        ] = []

        for raw_metric in raw_metrics:
            if not isinstance(
                raw_metric,
                dict,
            ):
                raise ValueError(
                    "Checkpoint metric "
                    "must be an object."
                )

            try:
                name = MetricName(
                    raw_metric["name"]
                )

                source = MetricSource(
                    raw_metric["source"]
                )

                value = raw_metric["value"]

            except (
                KeyError,
                ValueError,
            ) as exc:
                raise ValueError(
                    "Checkpoint contains "
                    "an invalid metric."
                ) from exc

            metadata = raw_metric.get(
                "metadata",
                {},
            )

            if metadata is None:
                metadata = {}

            if not isinstance(
                metadata,
                dict,
            ):
                raise ValueError(
                    "Checkpoint metric metadata "
                    "must be an object."
                )

            metrics.append(
                MetricResult(
                    name=name,
                    source=source,
                    value=value,
                    metadata=metadata,
                )
            )

        restored[
            request_id
        ] = tuple(
            metrics
        )

    return restored
