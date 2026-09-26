---
name: no-heavy-data-in-repo
enabled: true
event: bash
action: block
pattern: (cp|mv|rsync|Copy-Item|Move-Item)\s+[^|;&]*\.(parquet|npy|npz|pt|pth|ckpt|safetensors|faiss|h5|pkl|joblib|csv\.gz|tsv\.gz)\b[^|;&]*\s["']?(\.[/\x5c]?|(\.[/\x5c])?(src|experiments|docs|facts|tools|ops)([/\x5c][^\s"';|&]*)?|(\.[/\x5c])?[\w.-]+\.(parquet|npy|npz|pt|pth|ckpt|safetensors|faiss|h5|pkl|joblib|csv\.gz|tsv\.gz))["']?(\s|$|[;|&])
---

🛑 **Heavy data does not belong in the repo tree.**

CLAUDE.md: raw dumps, embeddings, indexes, model weights live in `data_local/` (git-ignored) or the
external root named by `DATA_ROOT`.

**Do this instead:**
- Read it in place from `data_local/...` or `$DATA_ROOT/...`.
- If a script needs it, pass the path as an argument.
- If you genuinely need a small artifact in the repo (a plot, a summary table under ~1 MB), write the
  *derived* file, not the source data, and say why in the commit.

This rule pattern-matches commands, so it is a first line only; the pre-commit hook blocks any staged file
over 5 MB whatever command put it there.

Every copied dataset makes clones and greps slower for every future session, and GitHub rejects files
over 100 MB outright.
