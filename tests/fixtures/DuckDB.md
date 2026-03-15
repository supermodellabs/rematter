---
created: 2026-03-11 15:41
modified: 2026-03-11 15:41
synced:
publish: true
---
## Extensions

DuckDB being such a marvelous tool, it has also become something of a database lovers' database. Not in the sense of being niche, it's obviously widely beloved, but it attracts time, attention, and devotion from people who would find analyzing query planner optimizations in C an enjoyable afternoon. A lovely byproduct of this is a truly incredible extensions ecosystem. Off the dome, I can't think of another tool with as many plugins that are hardened and special engineering projects in their own right.

Here are some of the highlights:

- [Flock](https://dais-polymtl.github.io/flock/docs/what-is-flock) new as of time of writing, routable LLM functions in DuckDB, following the style you see in major cloud warehouses now (calling things like 'complete', 'categorize', 'rank', etc. as you would `min()` or `sum()`)
- [Airport](https://airport.query.farm/) Rusty Conover's work in the DuckDB ecosystem is so impressive, his mini-ecosystem around Arrow Flight is incredibly useful
