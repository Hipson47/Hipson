# Releasing Hipson

Releases go to PyPI from GitHub Actions through
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/): PyPI trusts one workflow in this
repository, so no API token exists anywhere. The workflow is `.github/workflows/release.yml`.

## One-time setup (maintainer)

1. **PyPI account.** Register at <https://pypi.org/account/register/> and enable two-factor
   authentication (PyPI requires it for publishing).
2. **Pending trusted publisher.** At <https://pypi.org/manage/account/publishing/>, under
   "Add a new pending publisher" → GitHub, enter exactly:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `hipson` |
   | Owner | `Hipson47` |
   | Repository name | `Hipson` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   The project is created on the first successful upload; a pending publisher does not reserve
   the name, so do the first release soon after.
3. **GitHub environment.** Repository → Settings → Environments → New environment → `pypi`.
   Recommended: add yourself under "Required reviewers", so every upload waits for your click.

## Each release

1. Bump `version` in `pyproject.toml` and `__version__` in `src/hipson/__init__.py`, run
   `uv lock`, move the `Unreleased` entries in `CHANGELOG.md` under the new version and write
   `docs/release-X.Y.md` if the release has notable changes.
2. Merge to `main` with CI green on Ubuntu, macOS and Windows.
3. Tag and push: `git tag -a vX.Y.Z -m "Hipson X.Y.Z" && git push origin vX.Y.Z`.
4. The workflow checks that the tag matches both version strings, builds, runs
   `twine check --strict`, smoke-tests the wheel, then waits for approval in the `pypi`
   environment and uploads. Approve it under Actions → the run → Review deployments.
5. Create the GitHub Release for the tag (`gh release create vX.Y.Z --notes-file docs/release-X.Y.md`).

A tag that already exists can be published with Actions → "Release to PyPI" → Run workflow,
entering the tag. PyPI never accepts the same version twice; fix forward with a new patch version.
