---
description: Drive the CodeRabbit review loop on a PR to convergence (fix, reply, push, retrigger, repeat)
argument-hint: "[PR number] [unattended]"
---

Invoke the `coderabbit-loop` skill via the Skill tool and follow it.

Arguments: $ARGUMENTS

Interpret them as:
- A PR number or URL. If absent, resolve the PR for the current branch.
- The words "unattended", "leaving", "for hours", or "while I'm away" enable the
  skill's **Unattended mode** section: no round cap, convergence requires two
  consecutive clean rounds, and every round must end with a backgrounded wait job
  armed so the session resumes itself.

If no open PR exists for the current branch, stop and ask rather than creating one.
