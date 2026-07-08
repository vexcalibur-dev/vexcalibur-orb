# Contributing

Vexcalibur CircleCI Orb changes should keep the orb source, README, and reference
docs aligned.

## Development

Prerequisites:

- Python with `pip`
- ShellCheck
- detect-secrets
- CircleCI CLI for `scripts/validate-circleci.sh`

Run local checks from the repository root:

```bash
python -m pip install -r requirements-dev.txt
bash -n scripts/*.sh
bash -n src/scripts/*.sh
shellcheck scripts/*.sh
shellcheck src/scripts/*.sh
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline --
python -m unittest discover -s tests
scripts/validate-circleci.sh
```

Pull requests should include:

- A short description of the CircleCI workflow behavior being changed.
- Unit tests for `src/scripts/run-vexcalibur.sh` when input handling changes.
- Updates to `docs/reference/orb.md` when command, job, or parameter contracts change.
- Notes about public network access, token handling, or publishing behavior when relevant.
