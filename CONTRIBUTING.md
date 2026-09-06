# Contributing

BuildBrake is in private beta. Small, evidence-backed changes are preferred.

## Development setup

```bash
git clone https://github.com/karangandhidev/buildbrake.git
cd buildbrake
./install.sh --editable
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Dashboard HTML changes appear after a browser refresh. Restart `bb serve` after
changing Python backend code.

## Pull requests

- Explain the user-visible problem and the observable result.
- Keep unrelated formatting and refactors out of the change.
- Add a regression test for behavior changes.
- Run the complete test suite before opening the pull request.
- Never commit `.buildbrake/` receipts; they can contain project and prompt
  details.

Bug reports should include a minimal reproduction and a redacted receipt ID or
receipt excerpt. Do not publish credentials or private source code.
