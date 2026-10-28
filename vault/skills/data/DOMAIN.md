# Data

Answering questions from structured data held in a connected relational database.

## When to Use

Use this domain when the Task's answer must come from data stored in the database — counts, records, aggregates, relationships, or any fact that lives in the tables rather than in reasoning or external sources.

## When Not to Use

- The answer does not require the database.
- The Task asks about external or current public information. Use `research`.
- The Task is a software change or a learning interaction rather than a question about data.

## Skills

### data.text2sql

**Use when:** The Task asks a question whose answer must be derived from the connected PostgreSQL database.

Discovers the schema, answers with read-only queries, and reports what the database actually returned rather than stating contents from assumption.

**Avoid when:** The question can be answered from context already provided, or the answer would have to be invented because no usable data exists.

## Assignment Principle

Assign when the answer genuinely lives in the data. This domain carries a single methodology; do not attach other domains' methodologies speculatively, and keep the Task scoped to what was actually asked.
