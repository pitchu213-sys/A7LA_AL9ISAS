from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

M6_HEAD='b02bd7743504ef135aed68656f26e84c90cf5af0'
ARCHIVE_SHA='dad62570a8db918e205b571a0d994588b2e86ab49f1f746a6b4250a2e85cd0f6'
SITE_REPO='pitchu213-sys/ahla-qisas'
SITE_SHA='98c72e343f35cca378436e8f764b235786c8a70d'
KINDS=('story','article','video','short')
ROOT=Path(os.environ.get('GITHUB_WORKSPACE',Path.cwd())).resolve(); M6=ROOT/'m6'; SITE=ROOT/'website'; OUT=ROOT/'evidence-output'; LOG=OUT/'logs'
OUT.mkdir(exist_ok=True); LOG.mkdir(exist_ok=True)

def cmd(argv,cwd,timeout=900,name=None):
    t=time.monotonic()
    try:
        p=subprocess.run(argv,cwd=cwd,capture_output=True,text=True,timeout=timeout); to=False
        rc,out,err=p.returncode,p.stdout,p.stderr
    except subprocess.TimeoutExpired as e:
        rc,out,err,to=124,e.stdout or '',e.stderr or '',True
        if isinstance(out,bytes): out=out.decode('utf-8','replace')
        if isinstance(err,bytes): err=err.decode('utf-8','replace')
    r={'command':' '.join(argv),'exit_status':rc,'duration_seconds':round(time.monotonic()-t,3),'timed_out':to,'stdout':out,'stderr':err}
    if name: (LOG/f'{name}.log').write_text(f"$ {r['command']}\nexit={rc}\nduration={r['duration_seconds']}s\n\nSTDOUT\n{out}\n\nSTDERR\n{err}\n",encoding='utf-8')
    return r

def git(cwd,*args):
    return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True,text=True).stdout.strip()

def tree_hash(root):
    h=hashlib.sha256()
    for p in sorted(root.rglob('*')):
        if not p.is_file() or '__pycache__' in p.parts or p.suffix=='.pyc' or p.name in {'.accepted-head','.runtime-archive-sha256'}: continue
        h.update(p.relative_to(root).as_posix().encode()); h.update(b'\0'); h.update(p.read_bytes()); h.update(b'\0')
    return h.hexdigest()

def clone_site(dest):
    p=subprocess.run(['git','clone','--local','--no-hardlinks','--quiet',str(SITE),str(dest)],capture_output=True,text=True)
    if p.returncode: raise RuntimeError(p.stderr)
    subprocess.run(['git','checkout','--detach','--quiet',SITE_SHA],cwd=dest,check=True)

def network_failure(r):
    s=(r.get('stdout','')+r.get('stderr','')).lower()
    return any(x in s for x in ('enotfound','eai_again','could not resolve','registry.npmjs.org','network','timed out','timeout'))

R={'workflow_run_id':os.getenv('GITHUB_RUN_ID','UNKNOWN'),'execution_date_utc':datetime.now(timezone.utc).isoformat(),'runner_os':os.getenv('RUNNER_OS','UNKNOWN'),'runner_name':os.getenv('RUNNER_NAME','UNKNOWN'),'m6_head':M6_HEAD,'website_repository':SITE_REPO,'website_sha':SITE_SHA,'baseline':{},'types':{},'negative_test':{},'immutability':{}}
classification='PASS'; reason=None
try:
    if (M6/'.accepted-head').read_text().strip()!=M6_HEAD or (M6/'.runtime-archive-sha256').read_text().strip()!=ARCHIVE_SHA: raise RuntimeError('M6 provenance marker mismatch')
    m6_before=tree_hash(M6); site_head_before=git(SITE,'rev-parse','HEAD'); site_status_before=git(SITE,'status','--porcelain')
    if site_head_before!=SITE_SHA: raise RuntimeError('Website pinned SHA mismatch')
    rem_before=cmd(['git','ls-remote','origin'],SITE,120,'remote_before')
    if rem_before['exit_status']: classification='ENVIRONMENT_FAILURE'; reason='Cannot read Website remote refs'; raise RuntimeError(reason)
    node=cmd(['node','--version'],ROOT,name='node'); npm=cmd(['npm','--version'],ROOT,name='npm'); R['node_version']=node['stdout'].strip(); R['npm_version']=npm['stdout'].strip()
    if node['exit_status'] or npm['exit_status']: classification='ENVIRONMENT_FAILURE'; reason='Node/npm unavailable'; raise RuntimeError(reason)

    with tempfile.TemporaryDirectory(prefix='m6-baseline-') as td:
        b=Path(td)/'website'; clone_site(b); R['baseline']['sha']=git(b,'rev-parse','HEAD')
        ci=cmd(['npm','ci','--no-audit','--no-fund'],b,name='baseline_npm_ci'); R['baseline']['npm_ci']={k:ci[k] for k in ('exit_status','duration_seconds')}
        if ci['exit_status']:
            classification='ENVIRONMENT_FAILURE' if network_failure(ci) else 'BASELINE_WEBSITE_FAILURE'; reason='Untouched baseline npm ci failed'; raise RuntimeError(reason)
        bu=cmd(['npm','run','build'],b,name='baseline_build'); R['baseline']['build']={k:bu[k] for k in ('exit_status','duration_seconds')}
        if bu['exit_status']: classification='BASELINE_WEBSITE_FAILURE'; reason='Untouched baseline build failed'; raise RuntimeError(reason)
        R['baseline']['status']='PASS'

    sys.path[:0]=[str(M6/'src'),str(M6)]
    from aqp_publisher.publisher import WebsiteBaseline,WebsitePublisherDryRunEngine,PublisherError
    from aqp_core.state_engine import StateTransitionEngine
    from tests.m2_helpers import init_repo,base_manifest,materialize_computed_applicability,write_current_package,gate_record
    from tests.m3_helpers import current,command,ingestion_command,init_git
    def make_ops(path,kind):
        root=init_repo(path,M6); m=base_manifest(kind); materialize_computed_applicability(root,m); write_current_package(root,m); init_git(root); e=StateTransitionEngine(root); cid=m['content_id']
        assert e.execute_transition(command(root,cid,'IN_REVIEW','REVIEW_REQUESTED',command_id=f'M6-{kind}-0')).accepted
        if kind in {'video','short'}:
            cur,_=current(root,cid); rec=gate_record(cur,'production','PASS','05',review_id=f'M6-PROD-{kind}'); c=ingestion_command(root,cid,role='05',actor_id=rec['reviewed_by_id'],command_id=f'M6-{kind}-1'); assert e.ingest_gate_result(c,rec).accepted
        _,w=current(root,cid)
        if w['current_state']=='IN_REVIEW': assert e.execute_transition(command(root,cid,'READY_FOR_PUBLISH_REVIEW','NON_FINAL_GATES_SATISFIED',role='SYSTEM',actor_type='SYSTEM',command_id=f'M6-{kind}-2')).accepted
        cur,_=current(root,cid); rec=gate_record(cur,'final_publish','APPROVED','Founder',review_id=f'M6-FINAL-{kind}'); c=ingestion_command(root,cid,role='Founder',actor_id=rec['reviewed_by_id'],command_id=f'M6-{kind}-3'); z=e.ingest_gate_result(c,rec); assert z.accepted and z.resulting_state=='APPROVED_FOR_PUBLISH'; return root,current(root,cid)[0]

    wb=WebsiteBaseline(SITE,SITE_REPO,SITE_SHA,'main'); R['website_contract_fingerprint']=wb.inspect()['fingerprint']
    for kind in KINDS:
        with tempfile.TemporaryDirectory(prefix=f'm6-{kind}-') as td:
            ops,m=make_ops(Path(td)/'ops',kind); oh0=git(ops,'rev-parse','HEAD'); os0=git(ops,'status','--porcelain'); eng=WebsitePublisherDryRunEngine(ops,wb)
            try: plan=eng.generate_plan(m['content_id'],expected_website_base_sha=SITE_SHA); dry=eng.apply_and_validate(plan,build_timeout_seconds=900,install_timeout_seconds=900)
            except PublisherError as e:
                classification='ENVIRONMENT_FAILURE' if e.code=='M6_BUILD_ENVIRONMENT_UNAVAILABLE' else 'M6_OUTPUT_FAILURE'; reason=f'{kind}: {e.code}: {e.message}'; R['types'][kind]={'status':'FAIL','error':e.as_dict()}; raise RuntimeError(reason)
            oh1=git(ops,'rev-parse','HEAD'); os1=git(ops,'status','--porcelain'); _,w=current(ops,m['content_id'])
            if dry.get('status')!='PASS' or dry.get('build',{}).get('status')!='PASS' or dry.get('route_validation',{}).get('status')!='PASS' or w['current_state']!='APPROVED_FOR_PUBLISH' or oh0!=oh1 or os0!=os1:
                classification='M6_OUTPUT_FAILURE'; reason=f'{kind}: dry-run/build/immutability invariant failed'; raise RuntimeError(reason)
            R['types'][kind]={'status':'PASS','route':plan['route'],'destination_path':plan['operations'][0]['destination_path'],'build':dry['build'],'route_validation':dry['route_validation'],'ops_head_before':oh0,'ops_head_after':oh1,'workflow_state_after':w['current_state']}
            (LOG/f'{kind}_build_summary.json').write_text(json.dumps(dry['build'],indent=2),encoding='utf-8')

    with tempfile.TemporaryDirectory(prefix='m6-negative-') as td:
        n=Path(td)/'website'; clone_site(n); ci=cmd(['npm','ci','--no-audit','--no-fund'],n,name='negative_npm_ci')
        if ci['exit_status']: classification='ENVIRONMENT_FAILURE' if network_failure(ci) else 'M6_OUTPUT_FAILURE'; reason='Negative sandbox install failed'; raise RuntimeError(reason)
        p=n/'src/content/articles/m6-negative-invalid.mdx'; p.write_text('---\nlayout: "../../layouts/ArticleLayout.astro"\ntitle: "M6 invalid"\n---\n\n<div>\n',encoding='utf-8'); nb=cmd(['npm','run','build'],n,name='negative_build')
        if nb['exit_status']==0: classification='M6_OUTPUT_FAILURE'; reason='Invalid MDX unexpectedly built'; raise RuntimeError(reason)
        R['negative_test']={'status':'PASS','build_exit_status':nb['exit_status']}

    m6_after=tree_hash(M6); site_head_after=git(SITE,'rev-parse','HEAD'); site_status_after=git(SITE,'status','--porcelain'); rem_after=cmd(['git','ls-remote','origin'],SITE,120,'remote_after')
    if rem_after['exit_status']: classification='ENVIRONMENT_FAILURE'; reason='Cannot read Website remote refs after test'; raise RuntimeError(reason)
    R['immutability']={'m6_runtime_tree_before':m6_before,'m6_runtime_tree_after':m6_after,'website_head_before':site_head_before,'website_head_after':site_head_after,'website_status_before':site_status_before,'website_status_after':site_status_after,'remote_refs_unchanged':rem_before['stdout']==rem_after['stdout'],'remote_write':False,'pr_created':False,'vercel':False,'published_transition':False}
    if not(m6_before==m6_after and site_head_before==site_head_after==SITE_SHA and site_status_before==site_status_after and rem_before['stdout']==rem_after['stdout']): classification='M6_OUTPUT_FAILURE'; reason='Final immutability check failed'; raise RuntimeError(reason)
except Exception as e:
    if reason is None: classification='ENVIRONMENT_FAILURE'; reason=f'Unexpected harness/environment error: {type(e).__name__}: {e}'
finally:
    R['classification']=classification; R['failure_reason']=reason; (OUT/'results.json').write_text(json.dumps(R,ensure_ascii=False,indent=2),encoding='utf-8')
    imm=R.get('immutability',{}); route_ok=all(R.get('types',{}).get(k,{}).get('status')=='PASS' for k in KINDS)
    lines=['# REAL WEBSITE BUILD EVIDENCE — M6','',f"- **Workflow run ID:** `{R['workflow_run_id']}`",f"- **Execution date (UTC):** `{R['execution_date_utc']}`",f"- **Runner OS:** `{R['runner_os']}`",f"- **Runner:** `{R['runner_name']}`",f"- **M6 implementation HEAD:** `{M6_HEAD}`",f"- **Website repository:** `{SITE_REPO}`",f"- **Website exact SHA:** `{SITE_SHA}`",f"- **Node:** `{R.get('node_version','N/A')}`",f"- **npm:** `{R.get('npm_version','N/A')}`",'', '## Untouched baseline',f"- npm ci: {'PASS' if R.get('baseline',{}).get('npm_ci',{}).get('exit_status')==0 else 'FAIL/NOT RUN'}",f"- npm run build: {'PASS' if R.get('baseline',{}).get('build',{}).get('exit_status')==0 else 'FAIL/NOT RUN'}",'','## M6 real-baseline dry runs']
    for k in KINDS:
        x=R.get('types',{}).get(k,{}); lines += [f"- **{k.title()}:** {x.get('status','NOT RUN')}",f"  - Route: `{x.get('route','N/A')}`",f"  - Output: `{x.get('route_validation',{}).get('output_path','N/A')}`"]
    lines += ['','## Negative test',f"- **Result:** {R.get('negative_test',{}).get('status','NOT RUN')}",f"- **Build exit:** {R.get('negative_test',{}).get('build_exit_status','N/A')}",'','## Immutability / remote writes',f"- Website HEAD before: `{imm.get('website_head_before','N/A')}`",f"- Website HEAD after: `{imm.get('website_head_after','N/A')}`",f"- Website status before: `{imm.get('website_status_before','') or '(clean)'}`",f"- Website status after: `{imm.get('website_status_after','') or '(clean)'}`",f"- Website remote refs unchanged: {'YES' if imm.get('remote_refs_unchanged') else 'NO'}",'- Remote write: **NO**','- Remote branch: **NO**','- Pull Request: **NO**','- Vercel: **NO**','- PUBLISHED transition: **NO**','','## Final classification',f"`{classification}`"]
    if reason: lines += ['',f'Failure reason: {reason}']
    lines += ['','## Acceptance summary',f"- **REAL PINNED WEBSITE BUILD:** {'PASS' if classification=='PASS' else classification}",f"- **STORY:** {R.get('types',{}).get('story',{}).get('status','NOT RUN')}",f"- **ARTICLE:** {R.get('types',{}).get('article',{}).get('status','NOT RUN')}",f"- **VIDEO:** {R.get('types',{}).get('video',{}).get('status','NOT RUN')}",f"- **SHORT:** {R.get('types',{}).get('short',{}).get('status','NOT RUN')}",f"- **ROUTE VALIDATION:** {'PASS' if classification=='PASS' and route_ok else 'NOT COMPLETE'}",f"- **NEGATIVE TEST:** {R.get('negative_test',{}).get('status','NOT RUN')}",f"- **REAL WEBSITE UNCHANGED:** {'YES' if classification=='PASS' else 'NOT PROVEN'}",f"- **OPS UNCHANGED:** {'YES' if classification=='PASS' else 'NOT PROVEN'}",'- **REMOTE WRITE:** NO',f"- **READY FOR M6 FINAL ACCEPTANCE:** {'YES' if classification=='PASS' else 'NO'}"]
    (OUT/'REAL_WEBSITE_BUILD_EVIDENCE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'classification':classification,'failure_reason':reason},ensure_ascii=False)); sys.exit(0 if classification=='PASS' else 1)
