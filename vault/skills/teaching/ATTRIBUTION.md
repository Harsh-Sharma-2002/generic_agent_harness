# Attribution — Teaching skill domain

## Upstream source

The 13 skill bundles in this directory are imported from:

| | |
|---|---|
| Repository | `https://github.com/GarethManning/education-agent-skills` |
| Directory | `skills/student-learning/` |
| Commit | `135d6275e7db0657d70b147f46d3191c5430677d` (2026-10-22) |
| Author | Gareth Manning |
| Copyright | © 2026 Gareth Manning |
| Licence | Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0) |
| Licence text | [LICENSE](LICENSE) in this directory; canonical legal code at <https://creativecommons.org/licenses/by-sa/4.0/legalcode> |
| Imported | 2026-10-27 |

Each skill is imported as a single-file bundle. Upstream provides no
`references/`, `templates/`, `scripts/`, `assets/` or `examples/` directories
for any of these 13 skills, so none were copied.

## Changes made to the imported material

CC BY-SA 4.0 requires that changes be indicated. The following changes were
made, and **only** the following:

1. **Frontmatter — added KernelAI canonical fields.** Every `SKILL.md` gained an
   `id:` key (`teaching.<skill-name>`) and a `related:` list. The `related`
   entries mirror that file's existing upstream `chains_well_with` list exactly,
   in the same order and direction, with a `description` explaining why each
   transition is useful. `related` is an adaptation, not upstream content.
2. **Frontmatter — re-domained.** The upstream `domain: "student-learning"` value
   was changed to `domain: "teaching"` so the corpus sits in KernelAI's teaching
   domain. The upstream identifier is still visible per file in
   `skill_id: "student-learning/<skill-name>"`.
3. **Formatting.** A blank line and a `# KERNELAI CANONICAL FIELDS` comment were
   inserted to separate added fields from upstream fields.

Everything else is unmodified:

- the skill bodies (methodology, system prompts, worked examples, caveats,
  evidence discussions) are byte-identical to upstream;
- all other upstream frontmatter is preserved verbatim — `name`, `description`,
  `skill_id`, `skill_name`, `domain_number`, `version`, `audience`,
  `evidence_strength`, `evidence_sources`, `input_schema`, `evidence_captured`,
  `chains_well_with`, `tags`, `effort`, `user-invocable`,
  `disable-model-invocation`.

## ShareAlike obligation

These skills are licensed **CC BY-SA 4.0**, whose ShareAlike term requires that
adaptations be distributed under the same licence. The adapted `SKILL.md` files
in this directory therefore remain CC BY-SA 4.0. Any redistribution of this
directory, or of further modifications to it, must carry the same licence and
the attribution above. This is a different licence from the MIT terms that
cover other material in this repository; keep the two separate.

## Per-skill licence differences

None. All 13 skills are covered by the repository-level CC BY-SA 4.0 licence.
No individual skill carries its own separate licence or third-party notice.

The `evidence_sources` fields cite published research (e.g. Roediger &
Karpicke, 2006; Kapur, 2014; Bastani et al., 2025). Those citations are
bibliographic references to third-party work and do not place that work under
CC BY-SA 4.0.
