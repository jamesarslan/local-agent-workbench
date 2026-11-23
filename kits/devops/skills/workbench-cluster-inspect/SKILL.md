---
name: workbench-cluster-inspect
description: "Inspect a Kubernetes cluster without changing it. Use to find why a workload fails: confirm context and namespace, then get, describe, logs and events. No exec, delete or apply unasked."
license: MIT
compatibility: Kubernetes
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- A pod, deployment, job or service in a cluster is failing, slow or missing and you are asked why.
- You need to see what is running before you change a manifest.

Everything here only reads. Changing the cluster is a separate step that needs approval; see "Ask first".

## Steps

1. Confirm the cluster before anything else:

   ```
   kubectl config current-context
   kubectl config get-contexts
   ```

   The second command marks the current context with `*`.
   - `error: current-context is not set`, or the context is not one that `AGENTS.md` lists under Environments or Cloud access: stop and ask which context to use.
   - Never run `kubectl config use-context` or `kubectl config set-context`. They change the context for every terminal of the user.
2. Find the namespace. Use the one from the task or from `AGENTS.md`. Otherwise:

   ```
   kubectl config view --minify -o jsonpath='{..namespace}'
   ```

   Empty output means the namespace is `default`.
3. Name the context and the namespace in every later command, so that none can reach another cluster by accident. Test the connection first:

   ```
   kubectl --context <context> -n <namespace> --request-timeout=15s auth can-i get pods
   ```

   It prints `yes` or `no`. On a connection or login error, stop and report the first error line. Do not log in, and do not edit the kubeconfig file.
4. Get the overview:

   ```
   kubectl --context <context> -n <namespace> get pods -o wide
   kubectl --context <context> -n <namespace> get deployments,statefulsets,daemonsets,jobs
   kubectl --context <context> -n <namespace> events --types=Warning
   ```

   A healthy pod shows `Running` with all containers ready, for example `2/2`, or `Completed` for a job. Note every pod that shows anything else or a restart count above 0.
5. Look at one failing pod:

   ```
   kubectl --context <context> -n <namespace> describe pod <pod>
   kubectl --context <context> -n <namespace> logs <pod> --tail=100
   ```

   - In `describe`, read `State`, `Last State`, `Reason`, `Exit Code` and `Restart Count` of each container, then the `Events` at the end.
   - More than one container: add `-c <container>` to `logs`. The names are in `describe`.
   - Restart count above 0: add `--previous` to `logs` to read the run that crashed.
6. Match what you see. In this table `kubectl ...` is short for `kubectl --context <context> -n <namespace>`; type the long form.

   | Status or sign | Meaning | Look next at |
   | --- | --- | --- |
   | `Pending` | No node was found for it yet, or the image is still downloading | `Events`: a `FailedScheduling` line gives the reason, such as `Insufficient memory` |
   | `ImagePullBackOff`, `ErrImagePull` | The image cannot be pulled | Image name and tag in the manifest; whether the tag exists in the registry; the pull credentials |
   | `CrashLoopBackOff` | The container starts and exits again and again | `kubectl ... logs <pod> --tail=100 --previous`, and `Exit Code` under `Last State` |
   | `OOMKilled`, exit code 137 | The container used more memory than its limit | The memory limit in `describe`; `kubectl ... top pod <pod> --containers`, which works only when the Metrics Server is installed |
   | `Running` but not ready, such as `0/1` | The readiness check fails | `Events` with reason `Unhealthy`; the probe settings in the manifest; the log |
   | Service gives no answer | No ready pod matches the service | `kubectl ... get endpointslices -l kubernetes.io/service-name=<service>`: no addresses means the selector matches no ready pod. Compare `kubectl ... get service <service> -o jsonpath='{.spec.selector}'` with `kubectl ... get pods --show-labels` |
   | Rollout does not finish | New pods do not become ready | `kubectl ... rollout status deployment/<name> --watch=false`, then steps 5 and 6 for a new pod |

   For any other status, the reason is in the `Events` of `describe`.
7. Propose the fix in the manifest files of this repository and show the diff. Do not repair the cluster by hand. Applying is a separate step that needs approval.

## Allowed without asking

`get`, `describe`, `logs`, `events`, `top`, `explain`, `api-resources`, `version`, `auth can-i`, `rollout status --watch=false`, `rollout history`, `config current-context`, `config get-contexts`, `config view`.

## Ask first

Show the complete command, with context and namespace, and wait for a yes in this session:

- Runs a process in a pod or opens a connection into the cluster: `exec`, `attach`, `cp`, `debug`, `run`, `port-forward`, `proxy`.
- Changes objects: `apply`, `create`, `delete`, `edit`, `patch`, `replace`, `scale`, `autoscale`, `set`, `label`, `annotate`, `expose`, `rollout restart`, `rollout undo`, `rollout pause`, `rollout resume`.
- Changes nodes: `cordon`, `uncordon`, `drain`, `taint`.
- Any other tool that changes the cluster, such as a Helm install or upgrade.

## Secrets

- `get secrets` and `describe secret <name>` show names, key names and sizes. That is all you need.
- Never add `-o yaml`, `-o json`, `-o jsonpath` or `-o go-template` to a command that reads a Secret. The values come out base64-encoded, and that is not encryption.
- Never run `kubectl config view --raw`. It prints the credentials of every cluster.
- `describe pod` and `get pod -o yaml` print environment values written directly in the manifest. Do not copy them into your report.

## Report

```
Cluster: context <context>, namespace <namespace>
Checked: <the kubectl subcommands you ran>
Finding: <object> is <status>: <the reason line from Events or the log>
Cause: <one sentence, or "not found yet">
Proposed fix: <file and change, or none>
Changed in the cluster: nothing
```

## Gotchas

- `logs -f`, `get -w`, `events --watch` and `rollout status` without `--watch=false` never return. Do not use them.
- `logs` without `--tail` prints the whole log. Always set `--tail`.
- `logs --previous` fails when the container has never restarted. Drop the flag then.
- Events are kept for a short time, one hour by default. An empty event list does not mean nothing happened.
- `-A` lists every namespace and can print thousands of lines. Send it to a file and search the file.
- A pod name changes each time the pod is recreated. Run `get pods` again before you reuse a name.
