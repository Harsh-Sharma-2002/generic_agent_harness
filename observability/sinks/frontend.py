"""Live trace sink for frontend observability consumers."""

from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock

from observability.event import RuntimeEvent


class FrontendSink:
    """
    Maintain a bounded, queryable live RuntimeEvent store.

    The sink preserves structured runtime events for frontend
    consumers such as:
    - the KernelAI user interface,
    - the observability dashboard.

    Presentation and timing calculations are performed by consumers,
    not while events are emitted.
    """

    def __init__(
        self,
        max_events: int = 5000,
    ) -> None:
        if max_events < 1:
            raise ValueError(
                "max_events must be at least 1."
            )

        self.max_events = max_events

        self._events: deque[RuntimeEvent] = deque(
            maxlen=max_events
        )

        self._request_events: dict[
            str,
            deque[RuntimeEvent],
        ] = defaultdict(
            lambda: deque(
                maxlen=max_events
            )
        )

        self._task_events: dict[
            str,
            deque[RuntimeEvent],
        ] = defaultdict(
            lambda: deque(
                maxlen=max_events
            )
        )

        self._lock = Lock()

    def emit(
        self,
        event: RuntimeEvent,
    ) -> None:
        """
        Store one RuntimeEvent and index it by Request and Task.
        """

        with self._lock:
            self._events.append(
                event
            )

            if event.request_id is not None:
                self._request_events[
                    event.request_id
                ].append(
                    event
                )

            if event.task_id is not None:
                self._task_events[
                    event.task_id
                ].append(
                    event
                )

    def get_events(
        self,
    ) -> list[RuntimeEvent]:
        """
        Return the currently retained global event history.
        """

        with self._lock:
            return list(
                self._events
            )

    def get_request_events(
        self,
        request_id: str,
    ) -> list[RuntimeEvent]:
        """
        Return retained events belonging to one Request.
        """

        with self._lock:
            return list(
                self._request_events.get(
                    request_id,
                    (),
                )
            )

    def get_task_events(
        self,
        task_id: str,
    ) -> list[RuntimeEvent]:
        """
        Return retained events belonging to one Task.
        """

        with self._lock:
            return list(
                self._task_events.get(
                    task_id,
                    (),
                )
            )
