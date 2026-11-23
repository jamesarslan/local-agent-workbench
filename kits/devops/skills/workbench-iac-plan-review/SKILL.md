---
name: workbench-iac-plan-review
description: "Review a Terraform or OpenTofu plan before anything is applied. Use when .tf or .tofu files change: run plan, never apply, list destroys, replacements and drift, state the risk, then ask."
license: MIT
compatibility: Terraform, OpenTofu. Plan commands checked with Terraform 1.17 and OpenTofu 1.13.
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- You changed `.tf`, `.tofu` or `.tfvars` files, or were asked what such a change will do.
- Someone asks you to apply. Do this review first, then ask.

Your work ends with a reviewed plan. Do not run `apply`, `destroy`, `import`, `taint`, `force-unlock`, any `state` command, or `workspace select`, `new` or `delete`, unless the user asks for that exact command in this session.

## Steps

The commands below say `terraform`. With OpenTofu type `tofu` instead; every flag used here is the same in both. The shell commands are for macOS, Linux, Git Bash or WSL.

1. Pick the folder. Use the one `AGENTS.md` names for the environment. Otherwise list the candidates:

   ```
   find . -name .terraform.lock.hcl -not -path "*/.terraform/*"
   ```

   - One result: use that folder. No result: use the folder that holds the files you changed.
   - Several results and the task does not say which: ask.
   - `AGENTS.md` has an Environments list and does not say you may plan against this folder: stop and ask.

   Run every later command inside that folder.
2. Pick the tool, first rule that matches:
   - `AGENTS.md` or the README gives a plan command or a wrapper script: use it as written, save its output to `/tmp/plan.log`, and go to step 7.
   - The folder has `*.tofu` files, or `grep -c "registry.opentofu.org" .terraform.lock.hcl` prints 1 or more: `tofu`.
   - Otherwise: `terraform`.

   Check it is installed: `terraform version`. If the command is not found, stop and report it. Do not install anything.
3. Check the format. This needs no network:

   ```
   terraform fmt -check -recursive -diff
   ```

   Exit code 0 and no output means formatted. If it lists a file you changed, run `terraform fmt <file>` on that file. Leave files you did not change alone and mention them in the report.
4. Initialise only when the folder has no `.terraform` directory, or a later command tells you to run init:

   ```
   terraform init -input=false -lockfile=readonly
   ```

   - It fails because the lock file would have to change, and your change added or upgraded a provider: run it again without `-lockfile=readonly`, and list `.terraform.lock.hcl` as a changed file in your report.
   - It fails on credentials or the backend: do not log in and do not change any profile. Run `terraform init -input=false -backend=false`, do step 5, skip steps 6 to 8, and report `plan not run: no backend access`.
   - Never add `-upgrade`, `-reconfigure` or `-migrate-state` unless asked.
5. Validate: `terraform validate -no-color`. Exit code 0 and `Success! The configuration is valid.` means passed. On an error, fix the file and line it names and go back to step 3.
6. Confirm the environment, then plan:

   ```
   terraform workspace show
   terraform plan -input=false -no-color -detailed-exitcode -out=/tmp/tfplan > /tmp/plan.log 2>&1; echo "exit=$?"; tail -n 30 /tmp/plan.log
   ```

   `workspace show` prints `default` when the project does not use workspaces. If `AGENTS.md` names a different workspace for this environment, stop and ask before the second command. Do not switch workspaces yourself.
   Add `-var-file=<file>` only when the README or `AGENTS.md` names that file for this environment.

   | `exit=` | Meaning | Next |
   | --- | --- | --- |
   | 0 | The plan worked and changes nothing | Report "no changes". If you expected a change, check the folder and the workspace first |
   | 2 | The plan worked and has changes | Step 7 |
   | 1 | The plan failed | Find the first error with the command below, then use the list |

   ```
   grep -n -A6 "Error:" /tmp/plan.log | head -n 40
   ```

   - `Error acquiring the state lock`: the state is locked, usually because someone else is running a plan or an apply. Stop and report. Never use `-lock=false` or `force-unlock`.
   - `No value for required variable`: look for a variable file that the README names. Do not invent a value. If there is none, ask.
   - A credentials or permission error: stop and report the first error line.
   - An error in the configuration: fix it and go back to step 3. After two failed fixes, stop and report.
7. Read the plan:

   ```
   grep -n -E "^Plan:|^No changes" /tmp/plan.log
   grep -n -E "will be destroyed|must be replaced|will be replaced|forces replacement|will no longer be managed|will be removed from|has been deleted|has changed" /tmp/plan.log
   ```

   When `jq` is installed, get the exact list from the plan file. This prints one line for each changed object: action, address and reason code, never a value:

   ```
   terraform show -json /tmp/tfplan | jq -r '.resource_changes[]? | select(.change.actions != ["no-op"] and .change.actions != ["read"]) | "\(.change.actions | join("+"))\t\(.address)\t\(.action_reason // "")"'
   terraform show -json /tmp/tfplan | jq -r '.resource_drift[]? | "\(.change.actions | join("+"))\t\(.address)"'
   ```

   The second command lists drift: objects that were changed outside the tool since the last apply. In the log the same thing appears under `Objects have changed outside of`.
   Never print the output of `show -json` without the `jq` filter. It holds secret values in plain text.
8. Judge every line of that list:

   | Action | In the log | Risk |
   | --- | --- | --- |
   | `create` | `will be created` | Low. Check name, size and whether it is open to the internet |
   | `update` | `will be updated in-place` | Medium. Read each changed attribute in the log |
   | `delete+create` | `must be replaced`, `# forces replacement` | High. The object is deleted first, then created again: data on it is lost and there is downtime |
   | `create+delete` | `must be replaced` | High. The new object is created first, then the old one is deleted: data on the old one is lost |
   | `delete` | `will be destroyed` | High. The object is gone after apply |
   | `forget` | `will no longer be managed` (Terraform), `will be removed from the OpenTofu state` (OpenTofu) | Medium. The object stays but is no longer managed |

   Then apply these rules:
   - Raise the risk to high for anything that stores data, grants access or routes traffic: databases, buckets, disks, access policies, firewall rules, DNS records, encryption keys.
   - Every line must follow from your diff. A change you cannot trace to an edited line is drift or someone else's work. List it under "Not explained". Do not edit the configuration to make it go away.
   - A destroy or replacement that the task did not ask for goes first in your report, before anything else.
9. Delete the plan file after you have reported: `rm /tmp/tfplan`. It holds secret values in plain text. Keep it only when the user says they will apply that exact plan.

## If the user then asks you to apply

Apply the saved plan and nothing else:

```
terraform apply -input=false /tmp/tfplan
```

This applies exactly what was reviewed and does not ask again. Never use `-auto-approve`. If `/tmp/tfplan` is gone, or the command answers `Saved plan is stale` (the state changed after the plan was made): repeat steps 6 to 8 and ask again.

## Report

```
Plan: <folder>, workspace <name>, <terraform or tofu> <version>
Checks: fmt <passed|failed>, validate <passed|failed>, plan exit=<0|1|2>
Result: <n> to add, <n> to change, <n> to destroy   (or: no changes | plan not run: <reason>)
Destroyed: <addresses, or none>
Replaced: <address and the attribute that forces it, or none>
Changed in place: <addresses, or none>
Created: <addresses, or none>
Not explained: <addresses, or none>
Risk: <low|medium|high>, <one sentence that says why>
Nothing was applied.
```

## Gotchas

- A plan changes nothing, but it reads the real cloud account and the stored state and holds the state lock while it runs. It needs working credentials.
- With `-detailed-exitcode`, `exit=2` is success with changes, not a failure.
- One replacement counts once under "to add" and once under "to destroy". `1 to add, 1 to destroy` can be a single object.
- `(known after apply)` is a value that cannot be reviewed yet. `(sensitive value)` is hidden on purpose. Do not try to reveal it.
- `-target` hides the rest of the plan. Do not use it to make a plan shorter.
- `.terraform.lock.hcl` belongs in version control. The `.terraform` directory, `*.tfstate` files and plan files do not.
- Do not open `*.tfstate` files. Plain `terraform output` masks sensitive values; `output -json`, `output -raw` and `output <name>` print them in plain text, so do not run those.
