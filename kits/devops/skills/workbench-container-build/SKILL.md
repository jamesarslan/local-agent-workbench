---
name: workbench-container-build
description: "Build or change a container image. Use when you edit a Dockerfile, Containerfile or .dockerignore: pin the base, use build stages, run as non-root, then build it and run a smoke check."
license: MIT
metadata:
  source: agent-workbench
  version: "0.4.0"
---

## When to use

- You create or edit a `Dockerfile`, a `Containerfile` or a `.dockerignore`.
- You are asked to make an image smaller, faster to build or safer.
- An image build fails.

## Steps

The shell commands are for macOS, Linux, Git Bash or WSL.

1. Find how the project builds the image. Use the first source that has it: `AGENTS.md`, the README, the Makefile, a `build:` entry in the Compose file, the CI files. Use that command, with its tag replaced by `<name>:smoke`. If there is none, use the commands below.
   When the file is not `./Dockerfile`, add `-f <file>` to every `docker build` command, for example `-f Containerfile`.
2. Check that Docker is running: `docker info --format '{{.ServerVersion}}'`. A version number means yes. `Cannot connect to the Docker daemon` means no: do steps 3 and 4 by reading, skip steps 5 to 8, and report `build not run: Docker is not running`. Do not try to start it.
3. Make the Dockerfile follow these rules. Change only what breaks one.

   | Rule | How |
   | --- | --- |
   | Base image pinned | `FROM <image>:<exact version>`. Never `latest`, never without a tag |
   | Build tools stay out of the final image | First stage `FROM <image>:<version> AS build` holds compilers and dev dependencies. The last stage copies only the result: `COPY --from=build <path> <path>` |
   | Dependencies cached | Copy the manifest and lock file, install, and only then `COPY` the source |
   | Not root | A `USER` line in the last stage, after all files are in place. `USER 10001:10001` works without creating a user |
   | No secrets in layers | No password, token or key in `ARG`, `ENV` or `COPY`. Use `RUN --mount=type=secret,id=<id> <command>`, which reads the secret from `/run/secrets/<id>`, and build with `--secret id=<id>,env=<VARIABLE>` |
   | Start command in list form | `ENTRYPOINT ["<program>", "<arg>"]` or `CMD ["<program>", "<arg>"]` |
   | Packages in one step | `RUN apt-get update && apt-get install -y --no-install-recommends <packages> && rm -rf /var/lib/apt/lists/*` |
   | Local files with `COPY` | Not `ADD` |

   Add a digest to `FROM` only when other Dockerfiles in the project have one or `AGENTS.md` asks for it. Get it from the `Digest:` line of `docker buildx imagetools inspect <image>:<version>` and write `FROM <image>:<version>@sha256:<digest>`.

   Shape of a two-stage file:

   ```
   FROM <build image>:<version> AS build
   WORKDIR /src
   COPY <manifest> <lock file> ./
   RUN <install dependencies>
   COPY . .
   RUN <build>

   FROM <runtime image>:<version>
   WORKDIR /app
   COPY --from=build /src/<build output> ./
   USER 10001:10001
   ENTRYPOINT ["./<program>"]
   ```
4. Make sure a `.dockerignore` exists in the folder you pass to `docker build`. If it exists, add missing lines; do not replace it. It needs at least the lines that apply to this project:

   ```
   .git
   **/.env*
   **/*.pem
   **/*.key
   **/node_modules
   **/__pycache__
   **/.terraform
   **/*.tfstate*
   ```

   A line that starts with `!` brings a file back, for example `!.env.example`. The last matching line wins.
5. Lint without building: `docker build --check .`. Exit code 0 means no warnings. Every warning names a rule and a line; fix each one and run it again.
6. Build and keep the log:

   ```
   docker build --progress=plain -t <name>:smoke . > /tmp/build.log 2>&1; echo "exit=$?"; tail -n 30 /tmp/build.log
   ```

   On `exit=` other than 0, find the first error, not the last: `grep -n -E "ERROR|error:" /tmp/build.log | head -n 10`. Fix that step and build again. After two failed fixes, stop and report the first error line.
7. Inspect the result:

   ```
   docker image inspect --format 'user={{.Config.User}} platform={{.Os}}/{{.Architecture}} bytes={{.Size}} entrypoint={{.Config.Entrypoint}} cmd={{.Config.Cmd}}' <name>:smoke
   ```

   - `user=` empty, `0` or `root`: the image runs as root. Go back to step 3.
   - `entrypoint=[]` and `cmd=[]` together: the image has no start command.
   - When the size matters, `docker history <name>:smoke` shows the size each step adds.
8. Smoke check. Pick one:
   - The image is a command-line tool or a job:

     ```
     docker run --rm <name>:smoke <arguments that print a version or help>; echo "exit=$?"
     ```

     Passed when `exit=0` and the expected text is printed.
   - The image is a service that listens on a port:

     ```
     docker run -d --name smoke-check -p 127.0.0.1:18080:<container port> <name>:smoke
     sleep 5
     docker inspect --format '{{.State.Status}} exit={{.State.ExitCode}}' smoke-check
     docker logs --tail 30 smoke-check
     curl -fsS -m 10 -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18080/<path>
     docker rm -f smoke-check
     ```

     Passed when the status is `running`, the log shows a normal start, and `curl` prints the expected status code. `exited` means the container stopped: the reason is in the log. Run the last line in every case, also after a failure.
   - The container needs settings to start: pass made-up, non-secret values with `-e NAME=value`. If it needs a real secret, a database or another service, do not supply one. Report how far it started and what is missing.

## Never without an explicit request

- `docker push`, `docker login`, `docker logout`.
- `docker system prune`, `docker image prune`, `docker volume rm`, `docker rm` or `docker image rm` on anything you did not create in this session.
- A build argument or `ENV` line that carries a secret.

## Report

```
Image: <name>:smoke from <file>, <platform>, <size>
Rules: base pinned <yes|no>, stages <n>, user <value>, .dockerignore <present|added|missing>
Lint: docker build --check: <passed | warnings: <rule names> | not run>
Build: <passed | failed: <first error line> | not run: <reason>>
Smoke check: <command>: <passed | failed: <what you saw> | not run: <reason>>
Left behind: image <name>:smoke
```

## Gotchas

- Each `RUN`, `COPY` and `ADD` adds a layer. Deleting a file in a later step does not make the image smaller and does not remove a secret from it.
- `COPY` reaches only files inside the folder you pass to `docker build`, and not files that `.dockerignore` excludes.
- `.dockerignore` is separate from `.gitignore`. A `.env` file that Git ignores is still copied by `COPY . .` unless `.dockerignore` lists it.
- A numeric `USER` has no home directory and cannot write to files that root created. Give it the files it must write with `COPY --chown=10001:10001`, or run `chown` in a `RUN` step before the `USER` line.
- An Apple Silicon computer builds `linux/arm64` images. Add `--platform linux/amd64` to the build only when the project's build command or `AGENTS.md` does.
- Validate a Compose file with `docker compose config --quiet`. Without `--quiet` it prints every environment value, secrets included.
