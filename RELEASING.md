# Releasing boop

## Once: let GitHub publish to PyPI (no tokens anywhere)

Trusted publishing: PyPI trusts this repo's `release.yml` workflow directly.

1. Create accounts on [pypi.org](https://pypi.org/account/register/) and [test.pypi.org](https://test.pypi.org/account/register/) (separate sites), with 2FA.
2. On each: **Your account → Publishing → Add a new pending publisher**
   | Field | Value |
   |---|---|
   | PyPI project name | `boop-cli` |
   | Owner | `thebunnyweb` |
   | Repository name | `boop` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` (on pypi.org) / `testpypi` (on test.pypi.org) |
3. On GitHub: **Settings → Environments → New environment**, create `pypi` and `testpypi`.
   On `pypi`, add yourself under *Required reviewers*, so a release waits for your click.

The pending publisher reserves the name `boop-cli` until the first upload.

## Every release

1. CI is green on `main` (Actions → ci).
2. Set the version in `boop/__init__.py` (`__version__ = "0.1.0"`), commit, push.
3. **Dry run on TestPyPI:** Actions → release → *Run workflow*. Then, in a fresh shell:
   ```bash
   pipx install --index-url https://test.pypi.org/simple/ --pip-args="--extra-index-url https://pypi.org/simple/" "boop-cli[local]"
   boop --version && boop --providers && boop --doctor
   ```
   (TestPyPI won't take the same version twice: bump to `0.1.0rc1`, `rc2`… for repeated dry runs.)
4. **Release:** `git tag v0.1.0 && git push origin v0.1.0`. The workflow runs the tests, checks the tag matches
   `__version__`, builds, and publishes after you approve the `pypi` environment.
5. Check: `pipx install "boop-cli[local]"` on a clean machine.

## Making our own model the default

1. Kaggle: data notebook → last cell (fast rebuild); training notebook with `MAX_STEPS = -1`. It pushes
   `boop-360m.Q8_0.gguf` and `boop-360m.Q4_K_M.gguf` to `bunnyweb/boop-360m-gguf`.
2. Try it: `boop --use boop …` (the `boop` preset), and score it: the training notebook's eval cell, or locally
   `BOOP_MODEL=bunnyweb/boop-360m-gguf BOOP_MODEL_FILE=boop-360m.Q4_K_M.gguf BOOP_FEWSHOT=0 python eval/run_eval.py`.
3. If Q4 scores **≥ 70%** (Qwen 1.5B: 79%, at 7× the size): set `USE_OWN_MODEL = True` in `boop/model.py`,
   update the size line in the README ("About 1.9 GB" → "About 270 MB"), and release.
