"""Stress test one Request with 50 concurrent independent Tasks."""

from __future__ import annotations

import pytest

from orchestrator.orchestrator import Orchestrator
from tasks.request import Request


@pytest.mark.asyncio
async def test_single_request_50_parallel_tasks() -> None:
    """
    Stress one Supervisor with 50 independent Tasks.

    max_concurrent_tasks=50 intentionally removes the Supervisor
    concurrency limit as a bottleneck so runtime behavior at high
    async fan-out can be inspected through observability traces.
    """

    orchestrator = Orchestrator(
        supervisor_max_iterations=10,
        max_concurrent_requests=1,
        max_concurrent_tasks=50,
        verbose=False,
        local=False,
    )

    request = Request(
        request_id="parallel-request-050",
        query="""
Complete all 50 independent research items below.

IMPORTANT:
- Delegate every numbered item as its own Task.
- Create exactly 50 Tasks.
- Do not combine multiple numbered items into one Task.
- All items are independent.
- Delegate all Tasks before waiting for their results.
- Use web_search for every Task.
- Keep each Task result concise.

1. Research the latest NVIDIA Blackwell developments.
2. Research the latest AMD Instinct developments.
3. Research the latest Google TPU developments.
4. Research the latest AWS Trainium developments.
5. Research the latest Intel Gaudi developments.
6. Research the latest Microsoft Maia developments.
7. Research the latest Meta MTIA developments.
8. Research the latest Cerebras hardware developments.
9. Research the latest Groq hardware developments.
10. Research the latest Tenstorrent hardware developments.

11. Research the latest SambaNova hardware developments.
12. Research the latest Qualcomm AI accelerator developments.
13. Research the latest Apple AI chip developments.
14. Research the latest Broadcom AI accelerator developments.
15. Research the latest Marvell AI accelerator developments.
16. Research the latest Huawei AI accelerator developments.
17. Research the latest Alibaba AI accelerator developments.
18. Research the latest Baidu AI accelerator developments.
19. Research the latest Tesla AI training hardware developments.
20. Research the latest Oracle AI infrastructure hardware developments.

21. Research the latest NVIDIA H200 developments.
22. Research the latest NVIDIA GB200 developments.
23. Research the latest NVIDIA GB300 developments.
24. Research the latest AMD MI300X developments.
25. Research the latest AMD MI325X developments.
26. Research the latest AMD MI350 developments.
27. Research the latest Google Trillium TPU developments.
28. Research the latest AWS Trainium2 developments.
29. Research the latest AWS Trainium3 developments.
30. Research the latest Intel Gaudi 3 developments.

31. Research the latest AI inference accelerator developments.
32. Research the latest AI training accelerator developments.
33. Research the latest datacenter GPU developments.
34. Research the latest AI inference server developments.
35. Research the latest liquid-cooled AI server developments.
36. Research the latest HBM memory developments for AI accelerators.
37. Research the latest chiplet developments for AI accelerators.
38. Research the latest silicon photonics developments for AI systems.
39. Research the latest CXL developments for AI systems.
40. Research the latest NVLink developments.

41. Research the latest UALink developments.
42. Research the latest Ultra Ethernet developments.
43. Research the latest AI datacenter networking developments.
44. Research the latest AI accelerator interconnect developments.
45. Research the latest AI server power-efficiency developments.
46. Research the latest AI datacenter cooling developments.
47. Research the latest edge AI accelerator developments.
48. Research the latest inference optimization hardware developments.
49. Research the latest custom AI ASIC developments.
50. Research the latest AI supercomputer hardware developments.
""".strip(),
    )

    result = await orchestrator.submit(
        request
    )

    assert result is not None