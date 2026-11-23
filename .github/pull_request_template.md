## Change

Which user workflow changes? Link the issue or describe how to reproduce the original behavior.

## Verification

List the platforms and commands you tested. For a runtime or benchmark change, include the model revision, quantization, context, hardware, sample count and measurement definition. For a UI change, include a screenshot with a sample project.

If you added, renamed or removed a file, run `python release/build.py --write-manifest` and read every line it adds to `release/public-files.txt`; `python -m unittest tests.test_release` names what is missing. If you changed `requirements.txt`, regenerate `requirements.lock` with the command in `release/README.md`.

## Upstream attribution

List any new upstream code, model, asset or dependency and its license. Remove credentials, session transcripts, personal paths and third-party project data from the diff.
