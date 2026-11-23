---
name: workbench-secrets-hygiene
description: "Keep secrets out of output, files and commits. Use before every commit, when you handle keys, tokens, passwords or .env files, and when a secret has leaked: scan the diff, say what to rotate."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- Before every commit.
- A task touches credentials: API keys, tokens, passwords, private keys, `.env` files, kubeconfig files, state files.
- You see a secret value in a file, a diff, a log or command output.

A secret is any value that gives access. Refer to it by its name. Never write the value, or a part of it, into a file, a command, a commit message or your answer.

## Steps

1. Do not put secret values on the screen. Use the safe form:

   | Do not run | Run instead |
   | --- | --- |
   | `cat .env`, or opening a key or credentials file | `cut -s -d= -f1 .env` prints the names without the values |
   | `env`, `printenv`, `echo $TOKEN` | `if test -n "${TOKEN:-}"; then echo set; else echo missing; fi` |
   | `docker compose config` | `docker compose config --quiet` to validate, `docker compose config --no-interpolate` to read |
   | `docker inspect <container>` | `docker inspect --format '{{.State.Status}}' <container>`, or another single field |
   | `kubectl get secret <name> -o yaml` | `kubectl describe secret <name>` prints key names and sizes |
   | `kubectl config view --raw` | `kubectl config view` hides the credentials |
   | `terraform show -json`, `terraform output -json`, `terraform output <name>`, opening a `*.tfstate` file | `terraform output` with no name masks values marked sensitive |

   Also: do not add `set -x` to a script that handles a secret. It prints every command with the values filled in.
2. Pass a secret by reference, never as text in a file:

   | Where | Write |
   | --- | --- |
   | Shell script | `"$NAME"`. Fail early when it is missing: `: "${NAME:?NAME is not set}"` |
   | Compose file | `${NAME}`, or `env_file:` that points to a file Git ignores |
   | Dockerfile | `RUN --mount=type=secret,id=<id> <command>`, built with `--secret id=<id>,env=<NAME>`. Never `ARG` or `ENV` |
   | GitHub Actions workflow | `${{ secrets.NAME }}` |
   | Terraform, OpenTofu | A variable with `sensitive = true`; the value comes from the environment variable `TF_VAR_<variable name>` |
   | Kubernetes manifest | `valueFrom:` with `secretKeyRef:` (`name` and `key`) |

   Do not pass a secret as a command-line argument either. Put it in an environment variable or a file that Git ignores.
3. Keep secret files out of Git. These never get committed: `.env` and `.env.*` (except an example file with made-up values), `*.pem`, `*.key`, private SSH keys, `*.tfstate*`, saved plan files, kubeconfig files, cloud credential files. For each such file in the project:

   ```
   git check-ignore -v <path>; echo "exit=$?"
   git ls-files --error-unmatch <path>; echo "exit=$?"
   ```

   - First command `exit=1`: Git does not ignore the file. Add a line for it to `.gitignore`.
   - Second command `exit=0`: Git already tracks the file. Stop and tell the user. Stopping the tracking is `git rm --cached <path>`, which keeps the file on disk; run it only when asked. The value stays in the history either way, so treat it as leaked (step 6).
4. Stage your files by name, then scan what is staged. This prints file names only, never values:

   ```
   git --no-pager diff --staged --name-only --regexp-ignore-case -G 'AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|(password|passwd|secret|token|api_?key)[a-z0-9_]*"? *[:=] *"[^"$<{ ]{8,}'
   ```

   No output means no match. It finds cloud access key ids, private key blocks, GitHub and Slack tokens, and a double-quoted text of 8 or more characters assigned to a name that contains password, secret, token or api key.
5. For each file the scan lists, get the line numbers without the values:

   ```
   grep -n -i -E '<the same pattern>' <file> | cut -d: -f1
   ```

   Open the file at those lines and decide:
   - A reference, a placeholder or the name of a secret (`var.db_password`, `<your-token>`, `prod/db/password`): fine. Go on.
   - A real value: take the file out of the commit with `git restore --staged <file>`, replace the value with a reference from step 2, stage the file and scan again.

   The scan knows only common shapes. Also read `git --no-pager diff --staged` yourself and look at every new line that sets a credential.
6. When a secret has leaked, find out how far it went. `git --no-pager log --all --oneline -- <path>` lists the commits that touched the file. `git branch -r --contains <commit>` prints the remote branches that hold a commit; no output means it was not pushed.

   | Where the value is | What to do |
   | --- | --- |
   | Only in a file on this computer, never committed | Replace it with a reference and scan again. Tell the user it was there |
   | In a commit that was not pushed | Stop and tell the user. Removing it rewrites history, so do that only when asked |
   | Pushed, or in a CI log, an image layer, an issue, a chat or your own output | It is compromised. Only revoking it and creating a new one fixes that. Tell the user at once: what kind of secret, where, in which commit. Deleting the file or the commit does not undo the leak |

   You do not revoke or create credentials yourself unless asked. Do not force-push and do not rewrite history to hide a leak.

## Report

```
Secrets check
- Staged scan: <clean | <file>:<line> <kind of secret>, value not shown>
- Secret files: <path>: <ignored | not ignored, added to .gitignore | tracked, user told>
- Leak: <none | <kind> in <file or log>, commit <short hash>, pushed <yes|no>>
- To rotate: <which credential, or nothing>
```

Name the kind and the place. Never include the value, not even its first or last characters.

## Gotchas

- Base64 is an encoding, not encryption. A base64 value from a Kubernetes Secret is the secret.
- `.gitignore` has no effect on a file Git already tracks.
- Removing a secret in a new commit leaves it in every older commit.
- `.dockerignore` is a separate list. A `.env` file that Git ignores is still copied into an image by `COPY . .`.
- A saved Terraform or OpenTofu plan file and the state hold secret values in plain text, also those the terminal shows as `(sensitive value)`.
- In examples and tests, use a value that cannot be mistaken for a real one, such as `<your-token>`. A made-up key in a real format sets off scanners.
- A secret that was printed in this session has left this computer when the model runs somewhere else. Say that it was printed; the user decides whether to rotate it.
