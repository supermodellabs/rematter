# rematter

Obsidian metadata manager for perfectionists. `rematter` makes it easy to keep metadata perfectly aligned to a schema.

While it's not required, we suggest creating a unified but flexible vault-wide system with a shared set of properties and conventions. For instance, a `status` field would have a fixed set of values, and be used in any directory that required a status property — but the specific meaning of `status` could vary by directory (e.g. task status vs. project status vs. reading list). When you combine that discipline with rematter, Obsidian's lighter weight frontmatter UX starts to feel more solid. You use rematter to establish and conform bulk notes to the schema, and then you have a nice frontmatter GUI UX in Obsidian to fill out data on individual notes.

The downstream benefit of this is reliable syncing to other tools. Static site generators, task managers, calendars — because the metadata is in a consistent format, and you can chain `rematter` into your shell scripts, it's easy to build integrations.

For example, define a Content Collection in Astro, add the schema as a YAML file to the vault folder you want to export, then let rematter clean and validate your frontmatter and sync the content to your destination repo.
