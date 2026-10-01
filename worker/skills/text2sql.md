
One thing I like about doing it this way is that our first test becomes a **real multi-quantum workload**:

```text
LLM call #1
    ↓
sql_executor(schema discovery)
    ↓
──────── work boundary

LLM call #2
    ↓
sql_executor(actual query)
    ↓
──────── work boundary

LLM call #3
    ↓
final answer
