from __future__ import annotations
import json, os, shutil, subprocess, tempfile
from pathlib import Path
import yaml

ROOT=Path(os.environ["GITHUB_WORKSPACE"])
M6=ROOT/"m6"; WEBSITE=ROOT/"website"; OUT=ROOT/"m7-input"; OUT.mkdir(parents=True,exist_ok=True)
M6_HEAD="b02bd7743504ef135aed68656f26e84c90cf5af0"
WEBSITE_SHA="98c72e343f35cca378436e8f764b235786c8a70d"
WEBSITE_REPO="pitchu213-sys/ahla-qisas"
CONTENT_ID="AQP-CONTENT-0199f134-4182-7b60-a873-a82b93770001"
FIXED="2026-09-18T08:00:00Z"

def git(root,*args):
    return subprocess.run(["git",*args],cwd=root,check=True,capture_output=True,text=True).stdout.strip()

def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")

def att(path,value):
    return {"field_path":path,"asserted_value":value,"source_type":"HUMAN_ATTESTED","source_reference":"fixture://m7","attested_by":"fixture-human","attested_at":FIXED}

def build_manifest():
    m={
      "manifest_schema_version":"0.1","content_id":CONTENT_ID,"content_type":"story","content_version":"v1.0","status":"DRAFT",
      "title":"Synthetic story","slug":"m2-story","description":"Synthetic M7 acceptance fixture.","created_at":FIXED,"updated_at":FIXED,
      "approved_for_publish_at":None,"published_at":None,"content_hash":"sha256:"+"0"*64,"manifest_hash":"sha256:"+"0"*64,
      "source_files":[{"role":"primary_content","path":"src/content/stories/m2-story.md","format":"markdown","required":True,
        "file_hash":"sha256:"+"0"*64,"source_artifact":{"storage_type":"OPS_GIT_PATH","path":f"aqp/source_artifacts/{CONTENT_ID}/v1.0/m2-story.md"}}],
      "assets":[],
      "website":{"publish":True,"source_path":"src/content/stories/m2-story.md","route":"/stories/m2-story","published_url":None,
        "frontmatter":{"title":"Synthetic story","pages":[{"front":"front.png","back":"back.png"}]},
        "body":{"present":False,"required":False,"body_hash":None},"asset_bindings":[]},
      "publication_context":{"intended_audience":["CHILDREN"],"target_age_band":{"min_years":4,"max_years":7},"publish_channel":"WEBSITE",
        "distribution_type":"FREE_PUBLIC","commercial_context":"NON_COMMERCIAL","direct_sale":"FALSE","print_or_kdp":"FALSE"},
      "source_basis":{"source_basis_type":"ORIGINAL_PROJECT","adaptation_basis_id":None,"external_reference_uses":[]},
      "educational_context":{"triggers":{"educational":"FALSE","factual":"FALSE","science":"FALSE","health_body":"FALSE","development":"FALSE","parenting":"FALSE","learning":"FALSE","curriculum":"FALSE"}},
      "production_context":{"new_production_media_master":"FALSE","production_master_reference":None},
      "privacy":{"contains_personal_data":"FALSE","contains_child_data":"FALSE","contains_real_person_image_voice":"FALSE","consent_record_id":None},
      "safety":{"dangerous_imitation":"FALSE","violence_injury":"FALSE","fear_disturbing":"FALSE","bullying_humiliation_shame":"FALSE","self_harm":"FALSE","private_body_sensitive":"FALSE","health_safety_behavior":"FALSE","emergency_instruction":"FALSE","other_safety_sensitive":"FALSE"},
      "parent_trust":{"contains_advertising":"FALSE","contains_sponsorship":"FALSE","contains_affiliate_content":"FALSE","contains_testimonial_or_endorsement":"FALSE","contains_educational_outcome_claim":"FALSE","contains_health_or_safety_claim":"FALSE","contains_child_purchase_pressure":"FALSE"},
      "attestations":{"critical_fields_attestation_source":"HUMAN_ATTESTED","critical_field_attestations":[]},
      "gates":{},
      "publish_gate_aggregate":{"decision":"PENDING","author_editable":False,"policy_version":"AQP-PUBLISH-AGGREGATION-v0.1","content_id":CONTENT_ID,
        "content_version":"v1.0","content_hash":"sha256:"+"0"*64,"manifest_hash":"sha256:"+"0"*64,"evaluated_at":FIXED,"blocking_reasons":[]},
      "revocation":None,
    }
    items=[]
    for n,v in m["educational_context"]["triggers"].items():
        if v=="FALSE": items.append(att(f"educational_context.triggers.{n}","FALSE"))
    items.append(att("production_context.new_production_media_master","FALSE"))
    for sec in ("privacy","safety","parent_trust"):
        for n,v in m[sec].items():
            if n!="consent_record_id" and v=="FALSE": items.append(att(f"{sec}.{n}","FALSE"))
    items += [att("publication_context.direct_sale","FALSE"),att("publication_context.print_or_kdp","FALSE")]
    m["attestations"]["critical_field_attestations"]=sorted(items,key=lambda x:x["field_path"])
    return m

assert (M6/".accepted-head").read_text().strip()==M6_HEAD
assert git(WEBSITE,"rev-parse","HEAD")==WEBSITE_SHA

import sys
sys.path.insert(0,str(M6/"src")); sys.path.insert(0,str(M6))
from aqp_core.applicability import evaluate_all_applicability
from aqp_core.hashes import compute_content_hash, compute_manifest_hash, sha256_prefixed
from aqp_core.schema import load_json
from aqp_core.state_engine import StateTransitionEngine
from aqp_core.concurrency import git_head_sha
from aqp_publisher.publisher import WebsiteBaseline, WebsitePublisherDryRunEngine, validate_publisher_eligibility

def current(root,cid):
    return load_json(root/"aqp/registry/content"/cid/"current.json"),load_json(root/"aqp/registry/workflow"/cid/"current.json")

def command(root,cid,target,event,role="00",actor_type="HUMAN",command_id="CMD"):
    m,w=current(root,cid)
    return {"command_id":command_id,"idempotency_key":"IDEM-"+command_id,"correlation_id":"CORR-"+command_id,"task_id":"TASK-M7","content_id":cid,
      "expected_state":w["current_state"],"requested_target_state":target,"expected_content_version":w["content_version"],
      "expected_content_hash":w["content_hash"],"expected_manifest_hash":w["manifest_hash"],"expected_registry_revision":w["registry_revision"],
      "expected_ops_head_sha":git_head_sha(root),"actor_type":actor_type,"actor_id":"actor-"+role,"actor_role":role,"triggering_event":event,
      "reason":"synthetic M7 acceptance","contract_version":"AQP-WORKFLOW-STATE-MACHINE-v0.1","timestamp":FIXED,
      "blocker_id":None,"blocker_type":None,"specialist_gate":None,"specialist_disposition":None}

def ingest_cmd(root,cid,role,actor_id="fixture-reviewer",command_id="ING"):
    m,w=current(root,cid)
    return {"command_id":command_id,"idempotency_key":"IDEM-"+command_id,"correlation_id":"CORR-"+command_id,"task_id":"TASK-M7",
      "content_id":cid,"expected_state":w["current_state"],"expected_content_version":w["content_version"],"expected_content_hash":w["content_hash"],
      "expected_manifest_hash":w["manifest_hash"],"expected_registry_revision":w["registry_revision"],"expected_ops_head_sha":git_head_sha(root),
      "actor_type":"HUMAN","actor_id":actor_id,"actor_role":role,"reason":"synthetic M7 approval","contract_version":"AQP-WORKFLOW-STATE-MACHINE-v0.1","timestamp":FIXED}

def gate_record(m,gate_id,decision,role,review_id):
    return {"review_id":review_id,"gate_id":gate_id,"reviewed_by_role":role,"reviewed_by_id":"fixture-reviewer","reviewed_at":FIXED,
      "reviewed_content_id":m["content_id"],"reviewed_content_version":m["content_version"],"reviewed_content_hash":m["content_hash"],
      "reviewed_manifest_hash":m["manifest_hash"],"decision":decision,"safeguards":[],"notes":None,
      "source_record":{"source_type":"DIRECT_FOUNDER_ACTION" if gate_id=="final_publish" else "IMPORTED_SPECIALIST_HANDOFF",
        "source_role":role,"imported_by":None if gate_id=="final_publish" else "fixture-00","source_reference":"fixture://m7-gate"}}

with tempfile.TemporaryDirectory(prefix="aqp-m7-synthetic-") as td:
    td=Path(td); ops=td/"ops"; (ops/"aqp").mkdir(parents=True)
    shutil.copytree(M6/"aqp/schemas",ops/"aqp/schemas")
    for rel in ("registry/content","registry/workflow","registry/gates","registry/assets","registry/blockers","audit/events","publishing/executions"):
        (ops/"aqp"/rel).mkdir(parents=True,exist_ok=True)
    m=build_manifest()
    apps=evaluate_all_applicability(ops,m,manifest_errors=[])
    gates={}
    for gid,r in apps.items():
        gates[gid]={"applicability":{"decision":r["applicability"],"author_editable":False,"policy_version":r["policy_version"],"rule_id":r["rule_id"],
          "matched_rule_ids":r["rule_ids"],"reason":r["reason"],"evaluation_source":"DETERMINISTIC_POLICY_ENGINE","evidence_sources":r["evidence_sources"],
          "evaluated_at":FIXED},"review_result":None}
    m["gates"]=gates
    raw=yaml.safe_dump(m["website"]["frontmatter"],allow_unicode=True,sort_keys=False,default_flow_style=False,width=4096).encode("utf-8")
    raw=b"---\n"+raw+b"---\n"
    art=m["source_files"][0]["source_artifact"]["path"]; ap=ops/art; ap.parent.mkdir(parents=True,exist_ok=True); ap.write_bytes(raw)
    m["source_files"][0]["file_hash"]=sha256_prefixed(raw)
    m["content_hash"]=compute_content_hash(m); m["publish_gate_aggregate"]["content_hash"]=m["content_hash"]
    m["manifest_hash"]=compute_manifest_hash(m); m["publish_gate_aggregate"]["manifest_hash"]=m["manifest_hash"]
    write_json(ops/f"aqp/registry/content/{CONTENT_ID}/current.json",m)
    write_json(ops/f"aqp/registry/workflow/{CONTENT_ID}/current.json",{"content_id":CONTENT_ID,"current_state":"DRAFT","content_version":"v1.0",
      "content_hash":m["content_hash"],"manifest_hash":m["manifest_hash"],"active_owner":{"role":"00","id":"fixture-00"},"review_started":False,
      "gate_summary":{},"active_blocker_ids":[],"last_event_id":None,"registry_revision":1,"updated_at":FIXED})
    git(ops,"init","-q"); git(ops,"config","user.email","m7@example.invalid"); git(ops,"config","user.name","M7 Test"); git(ops,"add","."); git(ops,"commit","-q","-m","synthetic M7 fixture")
    e=StateTransitionEngine(ops)
    assert e.execute_transition(command(ops,CONTENT_ID,"IN_REVIEW","REVIEW_REQUESTED",command_id="M7-0")).accepted
    _,w=current(ops,CONTENT_ID)
    if w["current_state"]=="IN_REVIEW":
        assert e.execute_transition(command(ops,CONTENT_ID,"READY_FOR_PUBLISH_REVIEW","NON_FINAL_GATES_SATISFIED",role="SYSTEM",actor_type="SYSTEM",command_id="M7-1")).accepted
    cm,_=current(ops,CONTENT_ID)
    final=gate_record(cm,"final_publish","APPROVED","Founder","M7-FINAL")
    res=e.ingest_gate_result(ingest_cmd(ops,CONTENT_ID,"Founder",final["reviewed_by_id"],"M7-2"),final)
    assert res.accepted and res.resulting_state=="APPROVED_FOR_PUBLISH"
    m,_=current(ops,CONTENT_ID); ops_head=git(ops,"rev-parse","HEAD")
    elig=validate_publisher_eligibility(ops,CONTENT_ID); assert elig["eligible"]
    baseline=WebsiteBaseline(WEBSITE,WEBSITE_REPO,WEBSITE_SHA,"main"); engine=WebsitePublisherDryRunEngine(ops,baseline)
    plan=engine.generate_plan(CONTENT_ID,expected_website_base_sha=WEBSITE_SHA)
    dry=engine.apply_and_validate(plan,build_timeout_seconds=900,install_timeout_seconds=900)
    validated=dry["validated_change_set"]
    source=subprocess.run(["git","show",f"HEAD:{art}"],cwd=ops,check=True,capture_output=True).stdout
    (OUT/"validated_m6_changeset.json").write_text(json.dumps(validated,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"manifest.json").write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"eligibility.json").write_text(json.dumps(elig,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"source.md").write_bytes(source)
    subprocess.run(["git","bundle","create",str(OUT/"synthetic-ops.bundle"),"HEAD"],cwd=ops,check=True)
    summary={"m6_head":M6_HEAD,"website_repository":WEBSITE_REPO,"website_base_sha":WEBSITE_SHA,"website_main_sha_at_generation":git(WEBSITE,"rev-parse","HEAD"),
      "ops_head_sha":ops_head,"content_id":m["content_id"],"content_version":m["content_version"],"content_hash":m["content_hash"],"manifest_hash":m["manifest_hash"],
      "route":validated["route"],"branch":validated["branch_payload"]["intended_branch_name"],"changed_files":validated["branch_payload"]["intended_changed_files"],
      "build_validation":validated["build_validation"]["status"],"source_artifact_path":art,"source_file_hash":m["source_files"][0]["file_hash"]}
    (OUT/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))
