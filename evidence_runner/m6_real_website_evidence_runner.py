from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

M6_HEAD = "b02bd7743504ef135aed68656f26e84c90cf5af0"
M6_RUNTIME_ARCHIVE_SHA256 = "97dee77907c8595b05e523c7d920fb3dea1982455f11cb275922dad4f618e1e0"
WEBSITE_REPOSITORY = "pitchu213-sys/ahla-qisas"
WEBSITE_SHA = "98c72e343f35cca378436e8f764b235786c8a70d"
SUPPORTED = ("story", "article", "video", "short")

ROOT = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
M6 = ROOT / "m6"
WEBSITE = ROOT / "website"
OUT = ROOT / "evidence-output"
LOGS = OUT / "logs"
OUT.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str], cwd: Path, *, timeout: int = 900, env: dict[str, str] | None = None, log_name: str | None = None) -> dict:
    started = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        p = None
        timed_out = True
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes): stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes): stderr = stderr.decode("utf-8", "replace")
    duration = round(time.monotonic() - started, 3)
    if p is not None:
        stdout, stderr = p.stdout, p.stderr
        rc = p.returncode
    else:
        rc = 124
    rec = {
        "command": " ".join(cmd),
        "exit_status": rc,
        "duration_seconds": duration,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
    }
    if log_name:
        (LOGS / f"{log_name}.log").write_text(
            f"$ {' '.join(cmd)}\nexit={rc}\nduration={duration}s\n\n--- stdout ---\n{stdout}\n\n--- stderr ---\n{stderr}\n",
            encoding="utf-8",
        )
    return rec


def git_text(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return p.stdout.strip()


def runtime_tree_hash(root: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in {".accepted-head", ".runtime-archive-sha256"}:
            continue
        if "__pycache__" in path.parts or rel.endswith(".pyc"):
            continue
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def networkish(text: str) -> bool:
    low = text.lower()
    keys = ("enotfound", "eai_again", "network", "could not resolve", "registry.npmjs.org", "fetch failed", "timed out", "timeout")
    return any(k in low for k in keys)


def clone_local(src: Path, dest: Path) -> None:
    p = subprocess.run(["git", "clone", "--local", "--no-hardlinks", "--quiet", str(src), str(dest)], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"local clone failed: {p.stderr}")
    subprocess.run(["git", "checkout", "--detach", "--quiet", WEBSITE_SHA], cwd=dest, check=True)


result: dict = {
    "workflow_run_id": os.environ.get("GITHUB_RUN_ID", "UNKNOWN"),
    "execution_date_utc": datetime.now(timezone.utc).isoformat(),
    "runner_os": os.environ.get("RUNNER_OS", "UNKNOWN"),
    "runner_name": os.environ.get("RUNNER_NAME", "UNKNOWN"),
    "m6_implementation_head_expected": M6_HEAD,
    "website_repository": WEBSITE_REPOSITORY,
    "website_exact_sha_expected": WEBSITE_SHA,
    "classification": None,
    "baseline": {},
    "types": {},
    "negative_test": {},
    "immutability": {},
}
classification = "PASS"
failure_reason = None

try:
    accepted_head_file = M6 / ".accepted-head"
    archive_hash_file = M6 / ".runtime-archive-sha256"
    if not accepted_head_file.is_file() or accepted_head_file.read_text(encoding="utf-8").strip() != M6_HEAD:
        classification, failure_reason = "ENVIRONMENT_FAILURE", "M6 accepted-head provenance marker missing or wrong"
        raise RuntimeError(failure_reason)
    if not archive_hash_file.is_file() or archive_hash_file.read_text(encoding="utf-8").strip() != M6_RUNTIME_ARCHIVE_SHA256:
        classification, failure_reason = "ENVIRONMENT_FAILURE", "M6 runtime archive hash provenance marker missing or wrong"
        raise RuntimeError(failure_reason)
    m6_tree_before = runtime_tree_hash(M6)
    website_head_before = git_text(WEBSITE, "rev-parse", "HEAD")
    if website_head_before != WEBSITE_SHA:
        classification, failure_reason = "ENVIRONMENT_FAILURE", f"Website checkout is {website_head_before}, expected {WEBSITE_SHA}"
        raise RuntimeError(failure_reason)

    website_status_before = git_text(WEBSITE, "status", "--porcelain")
    website_remote_before = run(["git", "ls-remote", "origin"], WEBSITE, timeout=120, log_name="website_remote_before")
    if website_remote_before["exit_status"] != 0:
        classification, failure_reason = "ENVIRONMENT_FAILURE", "Unable to read Website remote refs before test"
        raise RuntimeError(failure_reason)

    node = run(["node", "--version"], ROOT, log_name="node_version")
    npm = run(["npm", "--version"], ROOT, log_name="npm_version")
    if node["exit_status"] or npm["exit_status"]:
        classification, failure_reason = "ENVIRONMENT_FAILURE", "Node/npm unavailable on runner"
        raise RuntimeError(failure_reason)
    result["node_version"] = node["stdout"].strip()
    result["npm_version"] = npm["stdout"].strip()

    with tempfile.TemporaryDirectory(prefix="aqp-m6-real-baseline-") as td:
        base = Path(td) / "website"
        clone_local(WEBSITE, base)
        baseline_sha = git_text(base, "rev-parse", "HEAD")
        ci = run(["npm", "ci", "--no-audit", "--no-fund"], base, timeout=900, log_name="baseline_npm_ci")
        result["baseline"]["sha"] = baseline_sha
        result["baseline"]["npm_ci"] = {k: ci[k] for k in ("command", "exit_status", "duration_seconds", "timed_out")}
        if ci["exit_status"] != 0:
            classification = "ENVIRONMENT_FAILURE" if networkish(ci["stderr"] + ci["stdout"]) else "BASELINE_WEBSITE_FAILURE"
            failure_reason = "Untouched Website baseline npm ci failed"
            raise RuntimeError(failure_reason)
        build = run(["npm", "run", "build"], base, timeout=900, log_name="baseline_build")
        result["baseline"]["build"] = {k: build[k] for k in ("command", "exit_status", "duration_seconds", "timed_out")}
        if build["exit_status"] != 0:
            classification, failure_reason = "BASELINE_WEBSITE_FAILURE", "Untouched Website baseline build failed"
            raise RuntimeError(failure_reason)
        result["baseline"]["status"] = "PASS"

    sys.path.insert(0, str(M6 / "src"))
    sys.path.insert(0, str(M6))
    from aqp_publisher.publisher import WebsiteBaseline, WebsitePublisherDryRunEngine, PublisherError
    from tests.test_m6_publisher_dry_run import make_ops_repo
    from tests.m3_helpers import current

    website_baseline = WebsiteBaseline(WEBSITE, WEBSITE_REPOSITORY, WEBSITE_SHA, "main")
    website_contract = website_baseline.inspect()
    result["website_contract_fingerprint"] = website_contract["fingerprint"]

    for content_type in SUPPORTED:
        with tempfile.TemporaryDirectory(prefix=f"aqp-m6-real-{content_type}-") as td:
            temp = Path(td)
            ops, manifest = make_ops_repo(temp / "ops", content_type)
            ops_head_before = git_text(ops, "rev-parse", "HEAD")
            ops_status_before = git_text(ops, "status", "--porcelain")
            engine = WebsitePublisherDryRunEngine(ops, website_baseline)
            try:
                plan = engine.generate_plan(manifest["content_id"], expected_website_base_sha=WEBSITE_SHA)
                dry = engine.apply_and_validate(plan, build_timeout_seconds=900, install_timeout_seconds=900)
            except PublisherError as exc:
                classification = "ENVIRONMENT_FAILURE" if exc.code == "M6_BUILD_ENVIRONMENT_UNAVAILABLE" else "M6_OUTPUT_FAILURE"
                failure_reason = f"{content_type}: {exc.code}: {exc.message}"
                result["types"][content_type] = {"status": "FAIL", "error": exc.as_dict()}
                raise RuntimeError(failure_reason) from exc
            ops_head_after = git_text(ops, "rev-parse", "HEAD")
            ops_status_after = git_text(ops, "status", "--porcelain")
            _, workflow = current(ops, manifest["content_id"])
            if dry.get("status") != "PASS" or dry.get("build", {}).get("status") != "PASS" or dry.get("route_validation", {}).get("status") != "PASS":
                classification, failure_reason = "M6_OUTPUT_FAILURE", f"{content_type}: M6 dry-run result not PASS"
                raise RuntimeError(failure_reason)
            if workflow.get("current_state") != "APPROVED_FOR_PUBLISH":
                classification, failure_reason = "M6_OUTPUT_FAILURE", f"{content_type}: M6 changed workflow state"
                raise RuntimeError(failure_reason)
            if ops_head_before != ops_head_after or ops_status_before != ops_status_after:
                classification, failure_reason = "M6_OUTPUT_FAILURE", f"{content_type}: synthetic canonical Ops mutated"
                raise RuntimeError(failure_reason)
            result["types"][content_type] = {
                "status": "PASS",
                "content_id": manifest["content_id"],
                "route": plan["route"],
                "destination_path": plan["operations"][0]["destination_path"],
                "dry_run_id": plan["dry_run_id"],
                "website_base_commit_sha": plan["website_base_commit_sha"],
                "ops_head_before": ops_head_before,
                "ops_head_after": ops_head_after,
                "workflow_state_after": workflow.get("current_state"),
                "build": dry["build"],
                "route_validation": dry["route_validation"],
                "remote_operation": dry.get("remote_operation"),
                "real_website_mutation": dry.get("real_website_mutation"),
                "canonical_ops_mutation": dry.get("canonical_ops_mutation"),
            }
            (LOGS / f"{content_type}_m6_build_summary.log").write_text(
                json.dumps(dry["build"], ensure_ascii=False, indent=2), encoding="utf-8"
            )

    with tempfile.TemporaryDirectory(prefix="aqp-m6-real-negative-") as td:
        neg = Path(td) / "website"
        clone_local(WEBSITE, neg)
        ci = run(["npm", "ci", "--no-audit", "--no-fund"], neg, timeout=900, log_name="negative_npm_ci")
        if ci["exit_status"] != 0:
            classification = "ENVIRONMENT_FAILURE" if networkish(ci["stderr"] + ci["stdout"]) else "M6_OUTPUT_FAILURE"
            failure_reason = "Negative sandbox npm ci failed before validation"
            raise RuntimeError(failure_reason)
        invalid = neg / "src/content/articles/m6-negative-invalid.mdx"
        invalid.write_text('---\nlayout: "../../layouts/ArticleLayout.astro"\ntitle: "M6 invalid negative fixture"\n---\n\n<div>\n', encoding="utf-8")
        neg_build = run(["npm", "run", "build"], neg, timeout=900, log_name="negative_build")
        if neg_build["exit_status"] == 0:
            classification, failure_reason = "M6_OUTPUT_FAILURE", "Intentionally invalid MDX unexpectedly built successfully"
            result["negative_test"] = {"status": "FAIL", "build_exit_status": 0}
            raise RuntimeError(failure_reason)
        result["negative_test"] = {
            "status": "PASS",
            "input": "intentionally malformed MDX in disposable Website clone",
            "build_exit_status": neg_build["exit_status"],
            "real_website_mutation": False,
            "canonical_ops_mutation": False,
        }

    m6_tree_after = runtime_tree_hash(M6)
    website_head_after = git_text(WEBSITE, "rev-parse", "HEAD")
    website_status_after = git_text(WEBSITE, "status", "--porcelain")
    website_remote_after = run(["git", "ls-remote", "origin"], WEBSITE, timeout=120, log_name="website_remote_after")
    if website_remote_after["exit_status"] != 0:
        classification, failure_reason = "ENVIRONMENT_FAILURE", "Unable to read Website remote refs after test"
        raise RuntimeError(failure_reason)
    refs_unchanged = website_remote_before["stdout"] == website_remote_after["stdout"]
    result["immutability"] = {
        "m6_accepted_head": M6_HEAD,
        "m6_runtime_archive_sha256": M6_RUNTIME_ARCHIVE_SHA256,
        "m6_runtime_tree_hash_before": m6_tree_before,
        "m6_runtime_tree_hash_after": m6_tree_after,
        "website_head_before": website_head_before,
        "website_head_after": website_head_after,
        "website_status_before": website_status_before,
        "website_status_after": website_status_after,
        "website_remote_refs_unchanged": refs_unchanged,
        "remote_write": False,
        "pr_created": False,
        "vercel_deployment": False,
        "published_transition": False,
    }
    if not (m6_tree_before == m6_tree_after and website_head_before == website_head_after == WEBSITE_SHA and website_status_before == website_status_after and refs_unchanged):
        classification, failure_reason = "M6_OUTPUT_FAILURE", "Immutability check failed"
        raise RuntimeError(failure_reason)

except Exception as exc:
    if failure_reason is None:
        classification = "ENVIRONMENT_FAILURE"
        failure_reason = f"Unexpected harness/environment error: {type(exc).__name__}: {exc}"
finally:
    result["classification"] = classification
    result["failure_reason"] = failure_reason
    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def yesno(v: bool) -> str:
        return "YES" if v else "NO"

    type_lines = []
    for c in SUPPORTED:
        v = result.get("types", {}).get(c, {})
        type_lines.append(f"- **{c.title()}:** {v.get('status','NOT RUN')}")
        if v.get("route"):
            type_lines.append(f"  - Route: `{v['route']}`")
            type_lines.append(f"  - Output: `{v.get('route_validation',{}).get('output_path','')}`")
            type_lines.append(f"  - Build exit: `{v.get('build',{}).get('build_exit_status','')}`")

    imm = result.get("immutability", {})
    baseline = result.get("baseline", {})
    md = f"""# REAL WEBSITE BUILD EVIDENCE — M6\n\n## Execution identity\n\n- **Workflow run ID:** `{result['workflow_run_id']}`\n- **Execution date (UTC):** `{result['execution_date_utc']}`\n- **Runner OS:** `{result['runner_os']}`\n- **Runner:** `{result['runner_name']}`\n- **M6 implementation HEAD:** `{M6_HEAD}`\n- **Website repository:** `{WEBSITE_REPOSITORY}`\n- **Website exact SHA:** `{WEBSITE_SHA}`\n- **Node:** `{result.get('node_version','NOT RECORDED')}`\n- **npm:** `{result.get('npm_version','NOT RECORDED')}`\n\n## Untouched pinned Website baseline\n\n- **Baseline SHA verified:** `{baseline.get('sha','NOT RUN')}`\n- **npm ci:** `{('PASS' if baseline.get('npm_ci',{}).get('exit_status') == 0 else 'FAIL / NOT RUN')}`\n- **npm ci exit:** `{baseline.get('npm_ci',{}).get('exit_status','N/A')}`\n- **npm ci duration:** `{baseline.get('npm_ci',{}).get('duration_seconds','N/A')}s`\n- **npm run build:** `{('PASS' if baseline.get('build',{}).get('exit_status') == 0 else 'FAIL / NOT RUN')}`\n- **build exit:** `{baseline.get('build',{}).get('exit_status','N/A')}`\n- **build duration:** `{baseline.get('build',{}).get('duration_seconds','N/A')}s`\n\n## Existing M6 Dry Run against the real pinned Website\n\n{chr(10).join(type_lines)}\n\n## Route validation\n\n**Overall:** `{('PASS' if result.get('types') and all(result.get('types',{}).get(c,{}).get('status') == 'PASS' for c in SUPPORTED) else 'FAIL / NOT RUN')}`\n\nEach supported content type was planned by the accepted M6 implementation, applied only to M6's disposable Website sandbox, built with the real Website lockfile/toolchain, and checked for its expected `dist` route.\n\n## Negative test\n\n- **Result:** `{result.get('negative_test',{}).get('status','NOT RUN')}`\n- **Build exit:** `{result.get('negative_test',{}).get('build_exit_status','N/A')}`\n- **Input:** `{result.get('negative_test',{}).get('input','N/A')}`\n\n## Immutability / no remote write\n\n- **M6 implementation HEAD (provenance):** `{imm.get('m6_accepted_head','N/A')}`\n- **M6 runtime archive SHA-256:** `{imm.get('m6_runtime_archive_sha256','N/A')}`\n- **M6 runtime tree hash before:** `{imm.get('m6_runtime_tree_hash_before','N/A')}`\n- **M6 runtime tree hash after:** `{imm.get('m6_runtime_tree_hash_after','N/A')}`\n- **Website HEAD before:** `{imm.get('website_head_before','N/A')}`\n- **Website HEAD after:** `{imm.get('website_head_after','N/A')}`\n- **Website status before:** `{imm.get('website_status_before','') or '(clean)'}`\n- **Website status after:** `{imm.get('website_status_after','') or '(clean)'}`\n- **Website remote refs unchanged:** `{yesno(bool(imm.get('website_remote_refs_unchanged')))}`\n- **Remote writes:** `NO`\n- **Remote branch creation:** `NO`\n- **Pull Request:** `NO`\n- **Vercel:** `NO`\n- **PUBLISHED transition:** `NO`\n\nThe workflow declares `permissions: contents: read`, uses `persist-credentials: false`, and contains no publication/deployment command.\n\n## Final classification\n\n`{classification}`\n\n{('Failure reason: ' + failure_reason) if failure_reason else ''}\n\n## Acceptance summary\n\n- **REAL PINNED WEBSITE BUILD:** `{('PASS' if classification == 'PASS' else classification)}`\n- **STORY:** `{result.get('types',{}).get('story',{}).get('status','NOT RUN')}`\n- **ARTICLE:** `{result.get('types',{}).get('article',{}).get('status','NOT RUN')}`\n- **VIDEO:** `{result.get('types',{}).get('video',{}).get('status','NOT RUN')}`\n- **SHORT:** `{result.get('types',{}).get('short',{}).get('status','NOT RUN')}`\n- **ROUTE VALIDATION:** `{('PASS' if classification == 'PASS' else 'NOT COMPLETE')}`\n- **NEGATIVE TEST:** `{result.get('negative_test',{}).get('status','NOT RUN')}`\n- **REAL WEBSITE UNCHANGED:** `{yesno(classification == 'PASS' and imm.get('website_head_before') == imm.get('website_head_after') and imm.get('website_status_before') == imm.get('website_status_after'))}`\n- **OPS UNCHANGED:** `{yesno(classification == 'PASS' and all(result.get('types',{}).get(c,{}).get('ops_head_before') == result.get('types',{}).get(c,{}).get('ops_head_after') for c in SUPPORTED))}`\n- **REMOTE WRITE:** `NO`\n- **READY FOR M6 FINAL ACCEPTANCE:** `{yesno(classification == 'PASS')}`\n"""
    (OUT / "REAL_WEBSITE_BUILD_EVIDENCE.md").write_text(md, encoding="utf-8")

print(json.dumps({"classification": classification, "failure_reason": failure_reason, "evidence": str(OUT / 'REAL_WEBSITE_BUILD_EVIDENCE.md')}, ensure_ascii=False))
sys.exit(0 if classification == "PASS" else 1)
