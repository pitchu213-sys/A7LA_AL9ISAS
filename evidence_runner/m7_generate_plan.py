from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(os.environ["GITHUB_WORKSPACE"])
M6 = ROOT / "m6"
WEBSITE = ROOT / "website"
OUT = ROOT / "m7-input"
OUT.mkdir(parents=True, exist_ok=True)

M6_HEAD = "b02bd7743504ef135aed68656f26e84c90cf5af0"
WEBSITE_SHA = "98c72e343f35cca378436e8f764b235786c8a70d"
WEBSITE_REPO = "pitchu213-sys/ahla-qisas"
CONTENT_ID = "AQP-CONTENT-0199f134-4182-7b60-a873-a82b93770001"

def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

assert (M6/".accepted-head").read_text().strip() == M6_HEAD
assert git(WEBSITE, "rev-parse", "HEAD") == WEBSITE_SHA

import sys
sys.path.insert(0, str(M6/"src"))
sys.path.insert(0, str(M6))
from aqp_publisher.publisher import WebsiteBaseline, WebsitePublisherDryRunEngine, validate_publisher_eligibility
from aqp_core.schema import load_json

with tempfile.TemporaryDirectory(prefix="aqp-m7-synthetic-") as td:
    td = Path(td)
    bundle = td/"ops.bundle"
    raw = subprocess.run(["base64","--decode",str(ROOT/"assets/m7-synthetic-ops.bundle.b64")], check=True, capture_output=True).stdout
    bundle.write_bytes(raw)
    ops = td/"ops"
    subprocess.run(["git","clone","--quiet",str(bundle),str(ops)], check=True)
    ops_head = git(ops, "rev-parse", "HEAD")
    manifest = load_json(ops/f"aqp/registry/content/{CONTENT_ID}/current.json")
    baseline = WebsiteBaseline(WEBSITE, WEBSITE_REPO, WEBSITE_SHA, "main")
    engine = WebsitePublisherDryRunEngine(ops, baseline)
    eligibility = validate_publisher_eligibility(ops, CONTENT_ID)
    if not eligibility["eligible"]:
        raise SystemExit("synthetic package is not eligible")
    plan = engine.generate_plan(CONTENT_ID, expected_website_base_sha=WEBSITE_SHA)
    validated = engine.apply_and_validate(plan, build_timeout_seconds=900, install_timeout_seconds=900)["validated_change_set"]

    artifact_rel = manifest["source_files"][0]["source_artifact"]["path"]
    source = subprocess.run(["git","show",f"HEAD:{artifact_rel}"], cwd=ops, check=True, capture_output=True).stdout
    (OUT/"validated_m6_changeset.json").write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT/"eligibility.json").write_text(json.dumps(eligibility, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT/"source.md").write_bytes(source)
    subprocess.run(["git","bundle","create",str(OUT/"synthetic-ops.bundle"),"HEAD"], cwd=ops, check=True)

    summary = {
        "m6_head": M6_HEAD,
        "website_repository": WEBSITE_REPO,
        "website_base_sha": WEBSITE_SHA,
        "website_main_sha_at_generation": git(WEBSITE, "rev-parse", "HEAD"),
        "ops_head_sha": ops_head,
        "content_id": manifest["content_id"],
        "content_version": manifest["content_version"],
        "content_hash": manifest["content_hash"],
        "manifest_hash": manifest["manifest_hash"],
        "route": validated["route"],
        "branch": validated["branch_payload"]["intended_branch_name"],
        "changed_files": validated["branch_payload"]["intended_changed_files"],
        "build_validation": validated["build_validation"]["status"],
        "source_artifact_path": artifact_rel,
        "source_file_hash": manifest["source_files"][0]["file_hash"],
    }
    (OUT/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
