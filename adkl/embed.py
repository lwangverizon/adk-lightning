#!/usr/bin/env python3
"""adkl embed — build the skill vector file from SKILL.md descriptions.

Closes a loop the console can currently only report. Editing a description in the
UI produces a "stale embeddings" warning and nothing else: retrieval keeps
matching the *old* text, which is silent and looks like a bad model rather than a
missing build step.

## What gets embedded

The `description` from frontmatter, and only that. It is what retrieval matches
on, and it is what the committed production files hold.

## Output format

Same shape as the production files, so the two are interchangeable:

    {"embedding_metadata": {"fingerprint": ..., "backend": "gateway",
                            "task_type": "RETRIEVAL_DOCUMENT",
                            "model": ..., "dimensions": ...},
     "skills": {"name": {"description": str, "embedding": [...]}}}

`task_type` is `RETRIEVAL_DOCUMENT`, matching production. Retrieval embeddings
are asymmetric -- the query side sends `RETRIEVAL_QUERY` -- and mismatching them
degrades ranking with no error.

The `fingerprint` is a hash of (model, dimensions, task_type, and every
description). That makes staleness *detectable* rather than a matter of
remembering: `--check` recomputes it and tells you whether the file still matches
the skills on disk.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import sys

import yaml

if __package__:
    from . import gateway as gw
    from . import skill_lint
else:  # pragma: no cover -- script mode
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from adkl import gateway as gw
    from adkl import skill_lint


def descriptions(skills_dir: pathlib.Path, include: list[str] | None = None
                 ) -> dict[str, str]:
    """Map skill name -> description, in the order given by `include`."""
    names = include if include is not None else sorted(
        p.parent.name for p in skills_dir.glob("*/SKILL.md"))
    out: dict[str, str] = {}
    for name in names:
        md = skills_dir / name / "SKILL.md"
        if not md.is_file():
            raise FileNotFoundError(f"{name}: {md} not found")
        fm, _body = skill_lint.parse_frontmatter(md)
        desc = (fm.get("description") or "").strip()
        if not desc:
            raise ValueError(
                f"{name}: no description. Retrieval matches on the description, "
                f"so a skill without one is unreachable.")
        out[name] = desc
    return out


def fingerprint(model: str, dimensions: int, task_type: str,
                descs: dict[str, str]) -> str:
    """Hash of everything that would change the vectors.

    Includes the descriptions themselves, so an edited description invalidates
    the file. Staleness becomes detectable instead of remembered.
    """
    h = hashlib.sha256()
    h.update(f"{model}\x00{dimensions}\x00{task_type}\x00".encode())
    for name in sorted(descs):
        h.update(name.encode())
        h.update(b"\x00")
        h.update(descs[name].encode())
        h.update(b"\x00")
    return h.hexdigest()[:16]


async def build(descs: dict[str, str], embedder, model: str,
                dimensions: int, backend: str = "gateway") -> dict:
    names = list(descs)
    vectors = await embedder.embed_batch([descs[n] for n in names])

    # Resolve the width BEFORE fingerprinting. `dimensions` is 0 when the
    # request omitted the parameter, but the file records the actual width --
    # fingerprinting with 0 and storing 768 made every fresh file report itself
    # stale on the first --check.
    resolved = dimensions or len(vectors[0])

    return {
        "embedding_metadata": {
            "fingerprint": fingerprint(model, resolved,
                                       "RETRIEVAL_DOCUMENT", descs),
            # Which vector space these live in. `check_vectors` refuses a
            # file whose backend is not the configured one, because cosine
            # across two spaces returns plausible numbers rather than failing.
            "backend": backend,
            "task_type": "RETRIEVAL_DOCUMENT",
            "model": model,
            "dimensions": resolved,
        },
        "skills": {
            n: {"description": descs[n], "embedding": list(v)}
            for n, v in zip(names, vectors)
        },
    }


def check(path: pathlib.Path, descs: dict[str, str]) -> list[str]:
    """Report why a vector file is stale. Empty list means it is current."""
    if not path.is_file():
        return [f"{path} does not exist -- run `adkl embed`"]
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("embedding_metadata") or {}
    stored = data.get("skills") or {}

    problems: list[str] = []

    missing = sorted(set(descs) - set(stored))
    extra = sorted(set(stored) - set(descs))
    if missing:
        problems.append(
            f"no vectors for {', '.join(missing)} -- these skills cannot be "
            f"retrieved at all")
    if extra:
        problems.append(
            f"vectors for skills no longer present: {', '.join(extra)} "
            f"(harmless, but the file is out of date)")

    changed = [n for n in sorted(set(descs) & set(stored))
               if (stored[n].get("description") or "").strip() != descs[n]]
    if changed:
        problems.append(
            f"description changed since embedding: {', '.join(changed)} -- "
            f"retrieval is still matching the OLD text")

    want = meta.get("fingerprint")
    if want and not problems:
        got = fingerprint(meta.get("model", ""), meta.get("dimensions") or 0,
                          meta.get("task_type", "RETRIEVAL_DOCUMENT"), descs)
        if got != want:
            problems.append(
                f"fingerprint mismatch ({want} -> {got}); the model or "
                f"dimensions changed even though the descriptions did not")
    return problems


def _load(agent_yaml: pathlib.Path):
    doc = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
    root = agent_yaml.parent
    skills = doc.get("skills") or {}
    sdir = root / (skills.get("dir") or "skills")
    include = list(skills.get("include") or []) or None

    feats = (doc.get("features") or {}).get("skill_retrieval") or {}
    rel = feats.get("vectors") or "embeddings/skills.json"
    return doc, root / rel, sdir, include


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build or check the skill vector file.")
    ap.add_argument("agent_yaml", type=pathlib.Path)
    ap.add_argument("--check", action="store_true",
                    help="report staleness without calling the gateway")
    ap.add_argument("-o", "--out", type=pathlib.Path,
                    help="override features.skill_retrieval.vectors")
    args = ap.parse_args()

    doc, out_path, sdir, include = _load(args.agent_yaml)
    if args.out:
        out_path = args.out

    try:
        descs = descriptions(sdir, include)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.check:
        problems = check(out_path, descs)
        if not problems:
            print(f"{out_path}: current ({len(descs)} skills)")
            return 0
        print(f"{out_path}: STALE")
        for p in problems:
            print(f"  {p}")
        return 1

    from adkl import execution
    try:
        if (args.agent_yaml.parent / "execution.yaml").is_file():
            execution.require_local(args.agent_yaml.parent)
    except execution.ExecutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Read the .env beside agent.yaml, not just the process environment. Without
    # this, embedding an example that carries its own gateway config reported
    # "gateway is not configured" with the credentials sitting right next to the
    # file it was handed.
    settings = gw.GatewaySettings.from_env(
        gw.read_env(args.agent_yaml.parent),
        provider=(doc.get("agent") or {}).get("provider"))
    if not settings.configured:
        print(f"error: provider {settings.provider!r} is not configured; "
              "missing " + ", ".join(settings.missing()), file=sys.stderr)
        if settings.provider == "gateway":
            print("       provision gateway variables in the execution profile.",
                  file=sys.stderr)
        return 2

    try:
        embedder = gw.GatewayEmbedder(settings, task_type="RETRIEVAL_DOCUMENT")
    except gw.GatewayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"embedding {len(descs)} skills via {settings.provider}/"
          f"{settings.embedding_model}")
    loop = asyncio.new_event_loop()
    try:
        data = loop.run_until_complete(
            build(descs, embedder, settings.embedding_model,
                  settings.embedding_dimensions,
                  backend=settings.spec.backend))
    except Exception as exc:
        print(f"error: {settings.provider} embedding call failed: {exc}",
              file=sys.stderr)
        return 3
    finally:
        loop.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    meta = data["embedding_metadata"]
    print(f"wrote {out_path}")
    print(f"  {len(data['skills'])} skills, {meta['dimensions']}-dim, "
          f"fingerprint {meta['fingerprint']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
