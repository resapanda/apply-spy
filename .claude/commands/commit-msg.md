---
description: Summarize pending changes and draft a commit message (does not commit)
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git branch:*)
---

## Current state

- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Staged changes: !`git diff --cached --stat`
- Unstaged changes: !`git diff --stat`
- Recent commits (for style): !`git log --oneline -5`

## Task

Look at the changes above (read the full diff with `git diff HEAD` and read any new untracked files if needed), then:

1. Give a short plain-language summary of what changed and why, grouped by area.
2. Write a commit message in this format:
   - First line: imperative summary, 50 characters or fewer, no trailing period (for example "Add profile creation").
   - Blank line, then a short body explaining what and why, wrapped at about 72 characters.
   - If the branch name starts with an issue number (for example `1-1-create-profile`), add a line `Refs #1` unless the user says this commit completes the issue, in which case use `Closes #<number>`.
   - End with the Co-Authored-By attribution line from your instructions.
3. Put the message in a code block the user can copy, and show the exact commands to commit it.
4. If staged and unstaged changes are mixed, point that out and say which files the message covers.

Do not run `git add`, `git commit` or `git push`. Wait for the user to ask. Extra instructions from the user: $ARGUMENTS
