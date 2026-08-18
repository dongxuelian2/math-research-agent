"""Zero-build Research Observatory web UI.

The server is intentionally dependency-free.  It reads the durable project
artifacts written by a run or by ``showcase_demo`` and exposes a small JSON API
plus a polished single-page view.  No browser-side framework or network asset
is required.
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines()[-300:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _run_snapshot(run_dir: Path) -> dict:
    state = _read_json(run_dir / "state.json", {})
    usage = _read_json(run_dir / "usage.json", {})
    pipeline = _read_json(run_dir / "pipeline.json", {})
    gate = _read_json(run_dir / "audits" / "gate.json", {})
    failure_map = _read_json(run_dir / "FAILURE_MAP.json", {})
    return {
        **state,
        "run_id": state.get("run_id") or run_dir.name,
        "usage": usage,
        "pipeline": pipeline,
        "gate": gate,
        "failure_map": failure_map,
        "artifact_root": str(run_dir.name),
    }


def _fallback_dag(theorems: list[dict]) -> dict:
    nodes = [
        {"id": theorem.get("id"), "label": theorem.get("title", theorem.get("id")), "kind": "theorem", "status": theorem.get("status")}
        for theorem in theorems
    ]
    edges = []
    for theorem in theorems:
        for dependency in theorem.get("dependencies", []):
            edges.append([dependency, theorem.get("id")])
    return {"nodes": nodes, "edges": edges}


def build_snapshot(project_root: str | Path) -> dict:
    root = Path(project_root).resolve()
    project = _read_json(root / "project.json", {})
    index = _read_json(root / "index.json", {})
    theorems = index.get("theorems") if isinstance(index, dict) else []
    if not isinstance(theorems, list):
        theorems = []
    target_id = project.get("current_target")
    target = _read_json(root / "theorems" / f"{target_id}.json", {}) if target_id else {}

    runs = []
    runs_root = root / "runs"
    if runs_root.is_dir():
        for run_dir in sorted((item for item in runs_root.iterdir() if item.is_dir())):
            if (run_dir / "state.json").exists():
                runs.append(_run_snapshot(run_dir))
    runs.sort(key=lambda item: (item.get("completed_at", ""), item.get("run_id", "")))
    latest = runs[-1] if runs else {}

    usage = {
        "calls": 0,
        "api_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "wall_clock_seconds": 0.0,
    }
    for run in runs:
        value = run.get("usage", {})
        if not isinstance(value, dict):
            continue
        for key in usage:
            try:
                usage[key] += float(value.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
    usage["calls"] = int(usage["calls"])
    usage["api_requests"] = int(usage["api_requests"])
    for key in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        usage[key] = int(usage[key])

    custom = _read_json(root / "observatory.json", {})
    dag = custom.get("dag") if isinstance(custom, dict) else None
    if not isinstance(dag, dict):
        dag = _fallback_dag(theorems)
    formal = _read_json(root / "formal_status.json", {})
    provenance = _read_json(root / "provenance.json", {})
    failed_routes = _read_json(root / "failed_routes.json", {})
    events = _read_events(root / "events.jsonl")
    agents = []
    pipeline = latest.get("pipeline", {}) if latest else {}
    if isinstance(pipeline, dict) and isinstance(pipeline.get("nodes"), list):
        agents = pipeline["nodes"]
    if not agents:
        metrics = latest.get("metrics", {}) if latest else {}
        agents = [
            {"id": role, "label": role.replace("_", " ").title(), "status": "RUNNING"}
            for role in metrics
            if isinstance(metrics, dict)
        ]
    failed_items = []
    for run in runs:
        value = run.get("failure_map", {})
        if isinstance(value, dict):
            items = value.get("items", [])
            if isinstance(items, list):
                failed_items.extend(items)

    return {
        "schema_version": 3,
        "project": {
            "id": project.get("id", root.name),
            "name": project.get("name", root.name),
            "root": str(root),
            "target_id": target_id,
            "target": target,
            "status": target.get("status") or project.get("status", "OPEN"),
        },
        "headline": custom.get("headline", "Live proof research, visible end to end."),
        "runs": runs,
        "latest_run": latest,
        "agents": agents,
        "dag": dag,
        "audit_gate": latest.get("gate", {}),
        "failed_routes": failed_routes.get("routes", []) if isinstance(failed_routes, dict) else [],
        "failure_items": failed_items,
        "usage": usage,
        "formal": formal,
        "provenance": provenance,
        "events": events,
        "updated_at": project.get("last_updated"),
    }


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Observatory</title>
<style>
:root{--bg:#07111f;--panel:#0d1b2e;--panel2:#11253d;--line:#213954;--text:#e8f0fa;--muted:#91a6bd;--cyan:#64d8ff;--green:#68e0a0;--amber:#ffc66d;--red:#ff7c8d;--violet:#b99cff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#123457 0,#07111f 40%,#050b14 100%);color:var(--text);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
.shell{max-width:1480px;margin:0 auto;padding:28px 30px 48px}.top{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:26px}.eyebrow{color:var(--cyan);font-size:11px;letter-spacing:.18em;text-transform:uppercase;font-weight:700}.title{font-size:34px;line-height:1.1;margin:7px 0 7px;letter-spacing:-.03em}.subtitle{color:var(--muted);max-width:680px}.status{border:1px solid var(--line);background:rgba(13,27,46,.78);padding:13px 16px;border-radius:14px;min-width:170px}.status strong{display:block;font-size:18px;color:var(--green);margin-top:4px}.status small{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}.panel{background:linear-gradient(145deg,rgba(17,37,61,.95),rgba(8,21,37,.95));border:1px solid var(--line);border-radius:18px;padding:19px;box-shadow:0 16px 50px rgba(0,0,0,.18)}.span-3{grid-column:span 3}.span-4{grid-column:span 4}.span-5{grid-column:span 5}.span-7{grid-column:span 7}.span-8{grid-column:span 8}.span-12{grid-column:span 12}.panel h2{font-size:13px;text-transform:uppercase;letter-spacing:.13em;color:var(--muted);margin:0 0 15px}.metric{font-size:25px;font-weight:750;letter-spacing:-.02em}.metric-label{color:var(--muted);font-size:12px;margin-top:2px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.metric-box{padding:13px 0;border-right:1px solid var(--line)}.metric-box:last-child{border-right:0}.accent{color:var(--cyan)}.green{color:var(--green)}.amber{color:var(--amber)}.red{color:var(--red)}.violet{color:var(--violet)}.statement{font-size:20px;line-height:1.35;margin:0 0 11px}.mono{font-family:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--muted)}.chips{display:flex;flex-wrap:wrap;gap:7px}.chip{border:1px solid var(--line);border-radius:999px;padding:4px 9px;color:var(--muted);font-size:12px}.chip.good{color:var(--green);border-color:#286c57}.chip.bad{color:var(--red);border-color:#713945}.chip.live{color:var(--cyan);border-color:#245f7c}.dag{display:flex;align-items:stretch;gap:10px;overflow:auto;padding:7px 2px 4px}.dag-col{display:flex;flex-direction:column;gap:10px;min-width:170px}.dag-arrow{display:flex;align-items:center;color:var(--cyan);font-size:24px}.node{border:1px solid var(--line);border-radius:13px;padding:11px;background:rgba(7,17,31,.65);min-height:72px}.node b{display:block;font-size:13px;margin-bottom:6px}.node small{color:var(--muted)}.node.good{border-color:#286c57}.node.bad{border-color:#713945}.node.found{border-color:#83662e}.agent-list{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.agent{border:1px solid var(--line);border-radius:12px;padding:11px}.agent-top{display:flex;justify-content:space-between;gap:8px}.agent b{font-size:13px}.agent small{display:block;color:var(--muted);margin-top:6px}.dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:var(--green);margin-right:5px}.dot.bad{background:var(--red)}.dot.amber{background:var(--amber)}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid var(--line);padding:9px 6px;font-size:12px;vertical-align:top}th{color:var(--muted);font-weight:600}.timeline{display:flex;flex-direction:column;gap:9px;max-height:350px;overflow:auto}.event{display:grid;grid-template-columns:132px 1fr;gap:10px;padding-bottom:9px;border-bottom:1px solid rgba(33,57,84,.65)}.event time{color:var(--muted);font-family:ui-monospace,monospace;font-size:11px}.event b{font-size:12px}.event span{color:var(--muted);display:block;font-size:12px}.footer{color:var(--muted);font-size:12px;margin-top:22px;text-align:center}@media(max-width:1000px){.span-3,.span-4,.span-5,.span-7,.span-8{grid-column:span 12}.top{display:block}.status{margin-top:16px}.metrics{grid-template-columns:repeat(2,1fr)}.agent-list{grid-template-columns:1fr}}@media(max-width:560px){.shell{padding:20px 14px}.title{font-size:28px}.metrics{grid-template-columns:1fr 1fr}.event{grid-template-columns:1fr;gap:2px}}
</style>
</head>
<body><main class="shell">
<header class="top"><div><div class="eyebrow">Gemini Math Research · Research Observatory</div><h1 class="title">Proofs under observation.</h1><div class="subtitle" id="headline">Loading the durable campaign stream…</div></div><div class="status"><small>THEOREM STATE</small><strong id="state">CONNECTING</strong><small id="target">—</small></div></header>
<section class="grid">
<div class="panel span-12"><h2>Target</h2><p class="statement" id="statement">—</p><div class="chips" id="target-meta"></div></div>
<div class="panel span-12"><div class="metrics"><div class="metric-box"><div class="metric accent" id="tokens">0</div><div class="metric-label">tokens observed</div></div><div class="metric-box"><div class="metric green" id="requests">0</div><div class="metric-label">Gemini requests</div></div><div class="metric-box"><div class="metric amber" id="elapsed">0s</div><div class="metric-label">wall-clock time</div></div><div class="metric-box"><div class="metric violet" id="cost">$0.00</div><div class="metric-label">reported cost</div></div></div></div>
<div class="panel span-8"><h2>Dependency & repair graph</h2><div class="dag" id="dag"></div></div>
<div class="panel span-4"><h2>Audit gate</h2><div id="gate"></div></div>
<div class="panel span-7"><h2>Agent lanes</h2><div class="agent-list" id="agents"></div></div>
<div class="panel span-5"><h2>Campaign runs</h2><div id="runs"></div></div>
<div class="panel span-7"><h2>Failure map</h2><div id="failures"></div></div>
<div class="panel span-5"><h2>Trust surface</h2><div id="trust"></div></div>
<div class="panel span-12"><h2>Live event stream</h2><div class="timeline" id="events"></div></div>
</section><div class="footer">Artifacts are read from the project directory · refresh is automatic · <span id="updated">—</span></div>
</main>
<script>
const esc=(v)=>String(v??'—');
const cls=(s)=>{s=String(s||'').toUpperCase();return s.includes('FAIL')||s.includes('REJECT')||s.includes('FOUND')?'bad':s.includes('PENDING')||s.includes('BLOCK')?'amber':'good'};
const chip=(v,c='')=>`<span class="chip ${c||cls(v)}">${esc(v)}</span>`;
function render(s){
  const p=s.project||{}, t=p.target||{}; document.getElementById('headline').textContent=s.headline||'Live proof research, visible end to end.'; document.getElementById('state').textContent=p.status||'OPEN'; document.getElementById('state').className=cls(p.status); document.getElementById('target').textContent=p.target_id||'No active target'; document.getElementById('statement').textContent=t.statement||'No theorem statement yet.';
  document.getElementById('target-meta').innerHTML=[chip(t.id||p.target_id,'live'),chip(t.audit_status||'NOT_AUDITED'),chip(t.proof_type||'NATURAL_LANGUAGE')].join('');
  const u=s.usage||{}; document.getElementById('tokens').textContent=Number(u.total_tokens||0).toLocaleString(); document.getElementById('requests').textContent=Number(u.api_requests||0).toLocaleString(); document.getElementById('elapsed').textContent=`${Number(u.wall_clock_seconds||0).toFixed(1)}s`; document.getElementById('cost').textContent=`$${Number(u.cost_usd||0).toFixed(2)}`;
  const nodes=(s.dag&&s.dag.nodes)||[]; const edges=(s.dag&&s.dag.edges)||[]; const outgoing={}; edges.forEach(e=>(outgoing[e[0]]??=[]).push(e[1])); const roots=nodes.filter(n=>!edges.some(e=>e[1]===n.id)); const columns=[]; const seen=new Set(); let current=roots.length?roots:nodes.slice(0,1); while(current.length){columns.push(current);current=current.flatMap(n=>outgoing[n.id]||[]).map(id=>nodes.find(n=>n.id===id)).filter(Boolean).filter(n=>!seen.has(n.id)); current.forEach(n=>seen.add(n.id)); if(columns.length>8)break} if(!columns.length)columns.push(nodes); document.getElementById('dag').innerHTML=columns.map((col,i)=>`${i?'<div class="dag-arrow">→</div>':''}<div class="dag-col">${col.map(n=>`<div class="node ${cls(n.status)}"><b>${esc(n.label||n.id)}</b><small>${esc(n.kind||'node')} · ${esc(n.status||'—')}</small></div>`).join('')}</div>`).join('');
  const gate=s.audit_gate||{}; const flags=['forward_implication','parameter_ranges','boundary_cases','dependencies_valid','no_counterexample','auditors_pass','final_auditor_pass','computational_evidence_separated']; document.getElementById('gate').innerHTML=`<div class="metric ${gate.passed?'green':'red'}">${gate.passed?'PASS':'BLOCKED'}</div><div class="chips" style="margin-top:12px">${flags.map(k=>chip(`${k.replaceAll('_',' ')}: ${gate[k]?'✓':'—'}`,gate[k]?'good':'bad')).join('')}</div>${(gate.failure_reasons||[]).map(x=>`<p class="mono red">${esc(x)}</p>`).join('')}`;
  document.getElementById('agents').innerHTML=(s.agents||[]).map(a=>`<div class="agent"><div class="agent-top"><b><i class="dot ${cls(a.status)==='bad'?'bad':cls(a.status)==='amber'?'amber':''}"></i>${esc(a.label||a.role||a.id)}</b>${chip(a.status||'RUNNING')}</div><small>${esc(a.role||a.kind||'pipeline node')} ${a.model?'· '+esc(a.model):''}</small></div>`).join('')||'<div class="mono">Waiting for agent events…</div>';
  document.getElementById('runs').innerHTML=(s.runs||[]).map(r=>`<div class="agent"><div class="agent-top"><b>${esc(r.run_id)}</b>${chip(r.status||'RUNNING')}</div><small>${r.parent_run_id?'successor of '+esc(r.parent_run_id):'root route'} · <a href="/artifact?path=${encodeURIComponent(r.artifact_root+'/CANDIDATE_PROOF.md')}" style="color:var(--cyan)">candidate</a></small></div>`).join('')||'<div class="mono">No runs yet.</div>';
  document.getElementById('failures').innerHTML=(s.failure_items||[]).map(f=>`<div class="agent"><div class="agent-top"><b class="red">${esc(f.category||'FAILURE')}</b>${chip(f.auditor||'gate','bad')}</div><small>${esc(f.exact_rejected_claim||f.repair_suggestion||'')}</small></div>`).join('')||'<div class="metric green">No unresolved failure items</div>';
  const formal=s.formal||{}; const prov=s.provenance||{}; document.getElementById('trust').innerHTML=`<div class="chips">${chip('LLM audits '+((gate.passed)?'✓':'—'),gate.passed?'good':'bad')}${chip('provenance '+(prov.entries?'✓':'—'),prov.entries?'good':'amber')}${chip('formal '+(formal.status||'NOT_REQUESTED'),cls(formal.status||'amber'))}</div><p class="mono">${esc((prov.entries&&prov.entries[0]&&prov.entries[0].sha256)||'No provenance hash yet')}</p>`;
  document.getElementById('events').innerHTML=(s.events||[]).slice().reverse().map(e=>`<div class="event"><time>${esc(e.at||'')}</time><div><b class="${cls(e.type)}">${esc(e.type||'EVENT')}</b><span>${esc(e.role||e.finding||e.change||e.status||e.route_id||'')}</span></div></div>`).join('')||'<div class="mono">Waiting for event stream…</div>'; document.getElementById('updated').textContent=`updated ${esc(s.updated_at||new Date().toISOString())}`;
}
async function refresh(){try{const r=await fetch('/api/snapshot',{cache:'no-store'});render(await r.json())}catch(e){document.getElementById('state').textContent='OFFLINE'}} refresh();setInterval(refresh,1500);
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "ResearchObservatory/1.0"

    def _send(self, body: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    @property
    def project_root(self) -> Path:
        return self.server.project_root  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/snapshot":
            body = json.dumps(
                build_snapshot(self.project_root), ensure_ascii=False
            ).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
            return
        if parsed.path == "/artifact":
            requested = parse_qs(parsed.query).get("path", [""])[0]
            candidate = (self.project_root / requested).resolve()
            try:
                candidate.relative_to(self.project_root)
            except ValueError:
                self._send(b"invalid artifact path", "text/plain; charset=utf-8", HTTPStatus.BAD_REQUEST)
                return
            if not candidate.is_file():
                self._send(b"artifact not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(str(candidate))[0] or "text/plain"
            self._send(candidate.read_bytes(), f"{content_type}; charset=utf-8")
            return
        self._send(b"not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args) -> None:
        return


class ObservatoryServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address, project_root: Path):
        self.project_root = project_root.resolve()
        super().__init__(address, _Handler)


def run_server(project_root: str | Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ObservatoryServer((host, port), Path(project_root))
    print(f"Research Observatory: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Research Observatory")
    parser.add_argument("--project", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    run_server(args.project, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
