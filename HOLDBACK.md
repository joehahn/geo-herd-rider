# HOLDBACK.md: what this repo publishes, and what it doesn't

This project is published to be read, run and learned from. It is not published to be cloned into a
competing product. Those two goals conflict in exactly one place, the tuned configuration and the data behind it, so the line is drawn there and nowhere else.

## The rule

**Publish the architecture and the epistemics. Hold back the settings, the corpus, and the research
ledger.**

Anyone reading the published material should be able to understand precisely how this works, judge
whether the numbers are honest, and build the same shape of thing for their own domain. What they
should not get for free is the tuning, which is the part that took the longest and is the easiest
to copy.

## Three tiers

**Published.** `README.md`, `CLAUDE.md`, `STYLE.md`, `agent_design.md`, the write-ups, the
dashboards under `docs/`, and all of `src/` and `scripts/` under the code licence. The design, the
guardrails, and the method for telling a real improvement from a lucky one are all in there in full.

**Template only.** The investor profiles. `examples/investor_profile.md` ships every knob name with
a one-line description of what it governs and a neutral placeholder value. The real
`investor_profile.backtest.md` and `investor_profile.forward.md` are gitignored and stay local.

**Local only.** The news corpus (`data/`), the curation journals, everything under
`data/forward/`, and `TODO.md`, the running record of what has been measured, tested and rejected.
That ledger is worth more than the code it describes, because it is a map of which plausible ideas
turn out to be wrong.

## When adding to the repo

Before committing a file or writing a doc, ask which tier it belongs to:

- Does it state a **tuned value**, or the reasoning that produced one? Local only.
- Does it hand someone a **runnable recipe** for gathering a corpus, curating it, or sweeping the
  grid? Keep it out of the published docs.
- Does it explain **how the thing works** or **how it is kept honest**? Publish it. That material is
  the point of the repo, it is what makes the project worth anyone's attention, and none of it
  requires naming a single tuned number.

The dashboards need their own pass under this rule: their settings panels currently render the live
profile, which is exactly what the template tier exists to withhold.

## What this does not do

Everything already pushed is already public and stays retrievable from git history. This policy
governs what goes out from here on, which is the part that matters: the value is in the tuning
still to come, not in the settings of any run published so far.
