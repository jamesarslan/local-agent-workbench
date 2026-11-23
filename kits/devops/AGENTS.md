# {{project}}

TODO: one line that says what this repository manages, on which cloud or platform, and names the tools with versions.

## Commands

A line that still says `TODO` has no command yet. Find it in the README or the CI files; do not guess.

- Install tools and modules: `{{cmd:install}}`
- Format and validate: `TODO: add the command`
- Plan or dry run: `TODO: add the command`
- Test: `{{cmd:test}}`

## Verify before finishing

- Run the format check and the validation of every tool whose files you changed, and read the output.
- For infrastructure code, also run a plan. Report how many resources it adds, changes and destroys, and name each one it destroys or replaces.
- Say which checks you ran, which passed, and which you could not run.

## Conventions

- Pin the versions of providers, modules, base images and CI actions. Never `latest`. TODO: list only the choices that differ from the tool defaults.

## Environments

- TODO: one line for each environment: name, folder or workspace, account or cluster, and whether the agent may run a plan against it.
- An environment that is not listed here counts as production: read its files, run nothing against it.

## Change safety

- You plan and read; a person applies. Apply, destroy, import and state commands, changes to cluster objects, image pushes and pipeline runs or re-runs happen only when the user asked for that exact command in this session.
- State is stored in: TODO. Never open, edit, move or delete state files by hand.

## Secrets

- Never print, log, commit or paste a secret value. Refer to a secret by its name.
- Do not read `.env` files, state files, kubeconfig files or private keys. When you need to know what they define, list the names, never the values.
- Secrets are stored in: TODO. Files in this folder hold a reference to a secret, never the value.

## Cloud access

- Profile, subscription or project, and regions: TODO. Use only these and pass them explicitly on each command.

## Boundaries

Always:
- Check which environment, account and cluster a command will reach before you run it.

Ask first:
- Adding or upgrading a provider, module, base image or CI action, or changing CI, deployment or access-control configuration.

Never:
- Commit secrets, keys, tokens, state files or plan files.
- Push, force-push or rewrite history unless asked.
- Change the default cloud profile, the current cluster context or the login state.
- Pass a flag that skips a safety check: `-auto-approve`, `--force`, `-lock=false`, `--no-verify`.

## Gotchas

<!-- Add one line here each time you have corrected the agent twice about the same thing. -->
