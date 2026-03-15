---
created: 2026-02-07 22:58
modified: 2026-02-07 21:58
synced:
publish: true
---
There are 3 things I find deeply satisfying that intersect heavily with my work:

- I love tidy, well-organized things
- I love beautifully crafted tools
- I love stories

So, as you might expect from that, I am delighted by a clean and curated linear git history.

These notes are both a sketchpad and reference, my best practices for maintaining clean, linear git history across solo and small team projects. I call this approach 'sapling', in that it's trunk-_ish_ , if it kept growing it would become a full-scale trunk-based development system. The goal is the same as trunk-based though: `main` is always deployable and the log tells the real story.

The reference section of distilled, tested ideas is first, all notes further down under [[#Sketchpad]] are looser thoughts.

---

## Core Concepts

### Two Timestamps in Every Commit

Every git commit carries two identities and two timestamps:

| Field              | What it represents                                    |
| ------------------ | ----------------------------------------------------- |
| **Author**         | The person who wrote the change                       |
| **Author date**    | When they originally wrote it                         |
| **Committer**      | The person who created this particular commit object   |
| **Committer date** | When this commit object was created                   |

In normal work these are identical. They diverge when a commit is **rewritten** — which is what `rebase`, `cherry-pick`, `commit --amend`, and interactive rebase (`squash`/`edit`/`reorder`) all do. These operations create new commit objects: the author date is preserved, but the committer date updates to _now_.

> [!tip]
> The `--committer-date-is-author-date` flag on rebase forces both to match. Cosmetically cleaner, but technically inaccurate about when the commit object was born.

### Why Linear History Matters

- `git log` reads like a changelog — every entry reflects a real functional change
- `git bisect` works perfectly — no merge commit noise to skip through
- Tags map cleanly to release points
- `git log v1.1.0..v1.2.0 --oneline` gives you a perfect changelog for free

---

## PRs are Pocket Universes

Shifting to a linear-obsessed, rebase-centric approach tends to freak people out for one reason: you 'rewrite history'. Not only that, but you 'rewrite history' then you _force push_! Dirty. Bad. Heretical. The thing to understand though is: your branch, your PR, ideally short-lived with somewhere between a single commit or a handful of changes — that _isn't history_. History is the shared, **curated** history of `main`. It doesn't matter that your rebase rewrote all the SHAs, and then you force pushed it on to your PR branch, because _none of that has happened yet_, not in the way that matters.

When you choose to emphasize a linear story about the _functional history_ of the codebase, the way the _code_ changed, not the way _you_ changed it — you are, by necessity, clearing out, curating, and overwriting physical realtime history. That stuff that you're taught to treat as sacred? That's the stuff that makes a `git log` useless. "oops fix typo", "one more time", "Merge feature into main into feature blah blah" — none of this meaningful. If you're working locally in a subset of changes that nobody else has interacted with, your goal is not to preserve a history of your actions, it's to get your changes organized into a clean legible state for sharing, whatever that entails.

When you go fetch and catch up on what's been unfolding on `main`, you're going to rewrite all of them as fresh commits on top of it anyway when you rebase. So it's important, from the start — whether you're coming from a Git Flow background, or just had senior devs say "don't rewrite history!" to you until you were scared and traumatized into believing force pushes were the devil — to fully let go of the idea that all history is sacred. Only our beautiful shared sweet baby project `main` is real history, the branches where you live and work are the Matrix, and in those little bubbles you are Neo. You have the power to shape reality for the benefit of your collaborators, and you should use it.

## Git Config Foundation

These belong in your global `~/.gitconfig`:

```ini
[pull]
    rebase = true        # git pull always rebases instead of merging
    ff = only            # refuse to pull if it can't fast-forward

[merge]
    ff = only            # refuse to merge if it can't fast-forward

[rebase]
    autoSquash = true    # honor fixup!/squash! prefixes automatically
    autoStash = true     # stash dirty working tree before rebase, apply after

[fetch]
    prune = true         # clean up deleted remote branches automatically

[rerere]
    enabled = true       # remember conflict resolutions, replay them automatically
```

> [!info] About `rerere`
> If you resolve a conflict during rebase and then need to redo the rebase later, git remembers your resolution and applies it automatically. Underrated and essential for a rebase-heavy workflow.

---

## Diagnostic Aliases

### Audit Log (Detect Rebases and Signature Issues)

```ini
[alias]
    audit = log --format=\"%h %G? %ad %cd %s\" --date=short
```

| Format code | Shows                                                              |
| ----------- | ------------------------------------------------------------------ |
| `%h`        | Short SHA                                                          |
| `%G?`       | Signature status (`G` good, `B` bad, `N` none, `U` untrusted)     |
| `%ad`       | Author date                                                        |
| `%cd`       | Committer date                                                     |
| `%s`        | Subject line                                                       |

When author date and committer date diverge, that's the rebase fingerprint.

### Lineage Check

```bash
git merge-base --is-ancestor origin/main HEAD && echo "clean" || echo "diverged"
```

Checks whether `origin/main`'s HEAD is actually an ancestor of your current branch — not just content-equivalent but object-identity-equivalent.

---

## Workflow: Solo Project

### Branch Model

```
main (protected, linear history enforced)
  ├── winnie/feature-a     ← short-lived, rebased before merge
  ├── winnie/fix-b          ← same
  └── contributor/their-fix ← outside PR, squash or rebase merged
```

### Daily Cycle

```bash
# Start work
git checkout main
git pull --ff-only
git checkout -b winnie/my-feature

# Work in small, atomic commits
git add -p
git commit

# Before merging — rebase onto latest main
git fetch origin
git rebase origin/main

# Push (force is fine on feature branches)
git push --force-with-lease
```

### Handling Outside Contributor PRs

- **Single clean commit** → rebase merge
- **Messy multi-commit PR** → squash merge with a well-written summary
- **Never** regular merge

### The `fixup!` Workflow

Work messily, clean up before merge:

```bash
# You notice a typo in a previous commit "feat: add widget parser"
git add -p
git commit --fixup=<sha-of-widget-parser-commit>

# Before pushing, interactive rebase auto-squashes it in
git rebase -i --autosquash origin/main
```

### Versioned Releases

```bash
git checkout main
git pull --ff-only
git tag -a v1.2.0 -m "Release 1.2.0: added widget support"
git push origin v1.2.0
```

`git log v1.1.0..v1.2.0 --oneline` is your changelog.

---

## Workflow: Small Team (2–5 Devs)

Same foundation as solo with these key additions:

- **One approval required** on PRs — lightweight code review matters now
- **Prefer squash merge for most PRs** — normalizes commit hygiene across contributors. Reserve rebase merge for PRs where individual commits genuinely tell a useful story.
- **Strict status checks** — "branch must be up to date with main" prevents two PRs that pass CI independently but break together
- **Agree on the rebase convention explicitly** — the person who merges second rebases and resolves conflicts. Say this out loud, don't assume.
- **Use CODEOWNERS** to auto-assign reviewers so PRs don't stall
- **Consider enabling merge queue** for repos with fast-moving main branches

### The One Rule

Nobody force-pushes `main`. Ever. Branch protection enforces this, but say it out loud anyway.

---

## GitHub Repository Settings

### Pull Request Settings

_Settings → General → Pull Requests_

- ✅ Allow squash merging (default message: "Pull request title")
- ✅ Allow rebase merging
- ❌ **Uncheck "Allow merge commits"**
- ✅ Always suggest updating pull request branches
- ✅ Automatically delete head branches

> [!important]
> Disabling merge commits at the repo level makes it structurally impossible to pollute the history. This is the single most impactful setting.

### Branch Ruleset for `main`

_Settings → Rules → Rulesets_

| Rule                        | Solo              | Small Team          |
| --------------------------- | ----------------- | ------------------- |
| Require pull request        | ✅ (0 approvals)  | ✅ (1 approval)     |
| Require linear history      | ✅                | ✅                  |
| Require status checks       | ✅ (strict)       | ✅ (strict)         |
| Restrict force pushes       | ✅                | ✅                  |
| Restrict deletions          | ✅                | ✅                  |
| Merge queue                 | ❌ (unnecessary)  | Optional            |

> [!note] Why require PRs on a solo project?
> Even with 0 required approvals, PRs give you CI runs, a changelog, and a place to write notes to yourself or future contributors. They're documentation, not bureaucracy.

---

## Common Mistakes & Fixes

### Accidentally Committed to `main`

```bash
git branch oops-feature
git reset --hard origin/main
git checkout oops-feature
# Open a PR from oops-feature
```

### Rebase Conflicts Got Overwhelming

```bash
# No shame in starting over
git rebase --abort

# Or resolve one at a time
# Fix the file, then:
git add <resolved-file>
git rebase --continue
```

### Accidentally Force-Pushed `main`

```bash
# Find the old HEAD
git reflog show origin/main
# Reset to it
git push --force-with-lease origin <old-sha>:main
# Go turn on branch protection immediately
```

### Contributor PR Has Merge Commits from Syncing

Either fix on their behalf:

```bash
git fetch origin
git checkout -b fix-their-branch origin/contributor/their-branch
git rebase origin/main
git push --force-with-lease origin fix-their-branch:contributor/their-branch
```

Or just squash merge the PR through the GitHub UI — that's what it's for.

### Branch Shows "Unable to Rebase" Despite Linear History

This happens when `main` was rewritten (rebased/force-pushed) after your branch was created. The commit SHAs your branch descends from no longer exist on `main`, even though the _content_ is identical.

```bash
git fetch origin
git rebase origin/main
git push --force-with-lease
```

Use the audit log alias to detect this: look for diverging author/committer dates and `verification: false` on commits you know were previously signed.

---

## Quick Reference: The Role of PRs

| Concern              | Solo                            | Small Team                        |
| -------------------- | ------------------------------- | --------------------------------- |
| **Primary purpose**  | CI + changelog + self-docs      | Code review + CI                  |
| **Review required**  | 0 approvals                     | 1 approval                        |
| **Preferred merge**  | Rebase merge (commits are clean)| Squash merge (normalizes quality) |
| **Branch lifetime**  | Hours to days                   | Hours to days                     |
| **Merge queue**      | Unnecessary                     | Optional                          |

> [!summary]
> PRs are never the place where history gets messy — they're the checkpoint where it gets cleaned up. The merge strategy you enforce at the repo level is what keeps `main` linear, not individual developer discipline (though that helps too).

----

## Sketchpad

Thinking out loud here.
