"""List enabled MCP tools and ask the running local server to count the schema tokens."""

import asyncio
from contextlib import AsyncExitStack
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
import httpx
import requests

from runtime import mcp_servers, owned
from tool_load import complete, overflow


def child_env(conf):
    """Environment for a measured MCP server: ours without provider keys and Workbench variables."""
    from connections import PROVIDER_KEYS

    hidden = set(PROVIDER_KEYS.values())
    base = {
        k: v
        for k, v in os.environ.items()
        if k not in hidden and not k.startswith("WORKBENCH_")
    }
    return {**base, **conf.get("env", {})}


async def schemas(name, conf):
    async with AsyncExitStack() as stack:
        if conf.get("command"):
            params = StdioServerParameters(
                command=conf["command"],
                args=conf.get("args", []),
                cwd=conf.get("cwd"),
                env=child_env(conf),
            )
            reader, writer = await stack.enter_async_context(stdio_client(params))
        elif conf.get("url") and conf.get("type", "http") in (
            "http",
            "streamable-http",
            "streamableHttp",
        ):
            client = await stack.enter_async_context(
                httpx.AsyncClient(headers=conf.get("headers", {}), timeout=20)
            )
            reader, writer, _ = await stack.enter_async_context(
                streamable_http_client(conf["url"], http_client=client)
            )
        else:
            raise ValueError(
                f"{name}: tool inspection supports stdio and streamable HTTP."
            )
        session = await stack.enter_async_context(ClientSession(reader, writer))
        await session.initialize()
        tools = []
        cursor = None
        while True:
            page = await session.list_tools(cursor=cursor)
            for item in page.tools:
                tools.append(
                    dict(
                        type="function",
                        function=dict(
                            name=f"mcp__{name}_{item.name}",
                            description=item.description or "",
                            parameters=item.inputSchema,
                        ),
                    )
                )
            cursor = page.nextCursor
            if not cursor:
                return tools


def cause(exc):
    """The first underlying error when a task group wrapped it."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


async def inspect_one(name, conf):
    try:
        return await asyncio.wait_for(schemas(name, conf), timeout=30)
    except asyncio.TimeoutError:
        raise ValueError(
            f"{name} did not list its tools within 30 seconds; start its command once in a terminal "
            "(a first run may download packages), then measure again."
        ) from None
    except Exception as exc:
        if type(exc) is ValueError:
            raise
        root = cause(exc)
        if isinstance(root, FileNotFoundError):
            raise ValueError(
                f"The command for {name} was not found ({conf.get('command')}); install it or register "
                "the server again with the right command."
            ) from None
        rejected = getattr(root, "errors", None)
        if isinstance(root, ValueError) and callable(rejected):
            # A settings validation error quotes the rejected value, which may be a secret; name the field only.
            fields = ", ".join(sorted({str(e["loc"][0]) for e in rejected() if e.get("loc")}))
            raise ValueError(
                f"The registered settings for {name} are not valid ({fields or 'unknown field'}); "
                "register the server again, then measure again."
            ) from None
        detail = " ".join(str(root).split())[:200]
        raise ValueError(
            f"Could not list tools from {name} ({type(root).__name__}: {detail}); check its "
            "command and connection, then measure again."
        ) from exc


def measure(s):
    _, rec = owned()
    if not rec:
        raise ValueError("Start a local server before measuring plugin cost.")
    messages = [dict(role="user", content="Reply OK.")]

    def tokens(tools):
        try:
            return complete(messages, tools, 1)["usage"]["prompt_tokens"]
        except requests.HTTPError as err:
            if overflow(err):
                raise ValueError(
                    f"These tool schemas do not fit the model's {int(rec['spec']['context']):,}-token context; "
                    "turn off some plugins or raise the context, then measure again."
                ) from None
            raise ValueError(
                "The local model server rejected the measurement; check that the model is still "
                "serving, then measure again."
            ) from None
        except requests.ConnectionError:
            raise ValueError(
                "The local model server is not answering; start the model, then measure again."
            ) from None

    base = tokens([])
    servers = mcp_servers(s)
    rows = []
    combined = []
    for name, conf in servers.items():
        items = asyncio.run(inspect_one(name, conf))
        rows.append(dict(name=name, tools=len(items), tokens=tokens(items) - base))
        combined.extend(items)
    return dict(
        servers=rows,
        tokens=tokens(combined) - base if combined else 0,
        context=rec["spec"]["context"],
    )
