## Context Switching

KernelAI uses OS-inspired scheduling, not OS-scale context switching.

- Quanta are semantic work units and may last seconds.
- A quantum boundary is only a preemption opportunity, not a forced switch.
- Preemption should be infrequent and only occur when its scheduling benefit exceeds context-switch overhead.
- Evaluation must measure this tradeoff rather than assume switching is free.
