"""Pick a catalog model for a hardware description. Pure: no files, network or clock."""

GIB = 1024**3
KV_FACTOR = {"f16": 1.0, "q8_0": 0.5, "fp8": 0.5, "q4_0": 0.28}
TIER_SLACK = 0.5
RAM_TIER_SLACK = 2.0
TRY_FRACTION = 0.5
WHAT = {"vram": "GPU memory", "unified": "unified memory", "ram": "system memory"}


def working_set_estimate(ram_gib):
    return round((0.66 if ram_gib <= 32 else 0.75) * ram_gib, 2)


def sizing_backend(hardware):
    if hardware["backend"] in ("cuda", "vulkan") and hardware.get("memory_gib") is None:
        return "cpu"
    return hardware["backend"]


def budget(hardware):
    backend, ram = sizing_backend(hardware), hardware["ram_gib"]
    if backend == "metal":
        limit = hardware.get("working_set_gib") or working_set_estimate(ram)
        return dict(
            kind="unified",
            total_gib=ram,
            usable_gib=round(limit - 1.0, 2),
            rule="Metal working set minus 1.0 GiB",
            overhead_gib=3.0 + (8.0 if ram > 16 else 6.0 if ram > 8 else 3.5) + 3.0,
        )
    if backend == "cpu":
        return dict(
            kind="ram",
            total_gib=ram,
            usable_gib=round(max(0.0, ram - max(4.0, 0.25 * ram)), 2),
            rule="system memory minus the larger of 4.0 GiB and 25%",
        )
    total = hardware["memory_gib"]
    return dict(
        kind="vram",
        total_gib=total,
        usable_gib=round(total - max(1.5, 0.08 * total), 2),
        rule="GPU memory minus the larger of 1.5 GiB and 8%",
    )


def need(entry, backend):
    spec, settings = entry["memory_model"], entry["settings"]
    out = dict(
        method=spec["method"],
        weights_gib=round(entry["resident_bytes"] / GIB, 2),
        context_gib=None,
        compute_gib=None,
        extras_gib=None,
        need_gib=spec.get("need_gib"),
    )
    if spec["method"] != "formula":
        return out
    ctx = settings["context"]
    context = (
        ctx * spec["kv_kib_per_token"] * 1024 * KV_FACTOR[entry.get("kv", "f16")] / GIB
        + spec["kv_fixed_gib"]
    )
    compute = (
        spec["compute_gib_metal"] if backend == "metal" else spec["compute_gib"]
    ) + ctx * spec["token_overhead_bytes"] / GIB
    extras = sum(x["gib"] for x in entry.get("extras", [])) + entry["lora_bytes"] / GIB
    out.update(
        context_gib=round(context, 2),
        compute_gib=round(compute, 2),
        extras_gib=round(extras, 2),
        need_gib=round(out["weights_gib"] + context + compute + extras, 2),
    )
    return out


def rate(entry, hardware):
    room = budget(hardware)
    sizing = need(entry, sizing_backend(hardware))
    total = sizing["need_gib"]
    slack = RAM_TIER_SLACK if room["kind"] == "ram" else TIER_SLACK
    tier_ok = room["total_gib"] + slack >= entry["min_memory_gib"]
    if not tier_ok:
        fit = "no"
    elif total is None:
        fit = "listed"
    elif total > room["usable_gib"]:
        fit = "no"
    elif room["kind"] == "unified" and total + room["overhead_gib"] > room["total_gib"]:
        fit = "tight"
    else:
        fit = "comfortable"
    if sizing["method"] == "formula":
        text = (
            f"{sizing['weights_gib']:.2f} weights + {sizing['context_gib']:.2f} "
            f"context + {sizing['compute_gib']:.2f} compute + "
            f"{sizing['extras_gib']:.2f} extras = {total:.2f} GiB"
        )
    elif total is not None:
        text = f"{total:.2f} GiB measured peak"
    else:
        text = f"listed for {entry['min_memory_gib']} GB; memory use was not recorded"
    sizing.update(
        fit=fit,
        tier_ok=tier_ok,
        kind=room["kind"],
        total_gib=room["total_gib"],
        usable_gib=room["usable_gib"],
        arithmetic=(
            f"{text}; usable {room['usable_gib']:.2f} of {room['total_gib']:.2f} GiB "
            f"({room['rule']})"
        ),
    )
    return {**entry, "sizing": sizing}


PLACE = {"vram": "GPU", "unified": "Mac", "ram": "computer"}


def proof(pick, room):
    if pick["status"] == "measured":
        return f"This exact file was measured on {pick['measured_on']}."
    trial = pick.get("evidence")
    if room["kind"] == "unified" and isinstance(trial, dict) and trial.get("machine"):
        called = trial.get("tool_call") == "passed"
        did = "loaded, answered and called a tool" if called else "loaded and answered"
        return f"A suggestion tested on {trial['machine']}, where it {did}."
    return "A suggestion that has not been run on this hardware."


def window(pick):
    return f"{pick['settings']['context'] // 1024}K context"


def amount(pick, room):
    sizing, at = pick["sizing"], window(pick)
    if room["kind"] != "unified":
        return f"it needs about {sizing['need_gib']:.1f} GiB at {at}"
    if sizing["method"] == "measured":
        return f"its recorded peak is {sizing['need_gib']:.1f} GiB at {at}"
    return f"it needs an estimated {sizing['need_gib']:.1f} GiB at {at}"


def share(sizing, room, only=""):
    return (
        f"a model can use {only}{sizing['usable_gib']:.1f} of the "
        f"{sizing['total_gib']:.1f} GiB of {WHAT[room['kind']]} here"
    )


def reason(pick, room):
    what = WHAT[room["kind"]]
    if not pick:
        return (
            f"No catalog model fits {room['total_gib']:.0f} GiB of {what}, so use a "
            "hosted API or a machine over SSH, or choose a file yourself in "
            "Advanced → Model library."
        )
    sizing, basis, place = pick["sizing"], proof(pick, room), PLACE[room["kind"]]
    known = sizing["need_gib"] is not None
    short = known and sizing["need_gib"] > sizing["usable_gib"]
    if sizing["fit"] == "try" or short:
        if not short:
            return (
                f"Even the smallest model in the catalog is listed for "
                f"{pick['min_memory_gib']} GB of {what} and this {place} has "
                f"{sizing['total_gib']:.1f} GiB, so close other apps before you "
                f"start it. {basis}"
            )
        return (
            f"Even the smallest model in the catalog is more than this {place} can "
            f"comfortably hold, so close other apps before you start it: "
            f"{amount(pick, room)}, and {share(sizing, room, 'only ')}. {basis}"
        )
    if not known:
        return (
            f"Listed for {pick['min_memory_gib']} GB of {what} at {window(pick)}, "
            f"and this {place} has "
            f"{sizing['total_gib']:.1f} GiB. {basis}"
        )
    spare = " with room to spare" if room["kind"] == "unified" else ""
    lead = {
        "tight": f"A tight fit on this {place}",
        "unknown": "Sized on system memory because the GPU memory could not be read",
    }.get(sizing["fit"], f"Fits this {place}{spare}")
    return f"{lead}: {amount(pick, room)}, and {share(sizing, room)}. {basis}"


def recommend(hardware, entries, engines=None):
    backend, room = sizing_backend(hardware), budget(hardware)
    usable = [
        e
        for e in entries
        if backend in e["backends"] and (engines is None or e["engine"] in engines)
    ]
    ranked = []
    order = sorted(usable, key=lambda e: (-e["min_memory_gib"], e["rank"], e["id"]))
    for entry in order:
        rated = rate(entry, hardware)
        fits = rated["sizing"]["fit"] != "no"
        if fits and all(r["model"] != rated["model"] for r in ranked):
            ranked.append(rated)
    if not ranked and usable:
        smallest = min(usable, key=lambda e: (e["resident_bytes"], e["id"]))
        if smallest["resident_bytes"] / GIB <= TRY_FRACTION * room["total_gib"]:
            ranked = [rate(smallest, hardware)]
            ranked[0]["sizing"]["fit"] = "try"
    if backend != hardware["backend"]:
        for rated in ranked:
            rated["sizing"]["fit"] = "unknown"
    pick = ranked[0] if ranked else None
    warnings = [*hardware.get("unknown", []), *hardware.get("notes", [])]
    if pick:
        total, free = pick["sizing"]["need_gib"], hardware.get("free_gib")
        short = total is not None and free is not None and total > free - 1.0
        if room["kind"] == "vram" and short:
            warnings.append(
                f"Only {free:.1f} GiB of GPU memory is free right now; close other "
                "GPU apps before you start this model."
            )
        if room["kind"] == "vram" and hardware.get("gpu_vendor") == "intel":
            warnings.append("No Intel GPU has been tested with Agent Workbench.")
        if pick.get("licence_note"):
            warnings.append(pick["licence_note"])
    return dict(
        pick=pick,
        alternatives=ranked[1:3],
        backend=hardware["backend"],
        sizing_backend=backend,
        budget=room,
        reason=reason(pick, room),
        warnings=warnings,
    )
