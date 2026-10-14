"""Actual installed-wheel regression: demo and a real public-mode RoadCase start.

Run with an already built wheel; all installs/data/server writes use own temp:
    python tests/wheel_consumer.py /path/to/visionflow-0.1.0-py3-none-any.whl
No AWS/provider calls, no GUI, and no editable/source-checkout imports.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CONSUMER = r'''
from pathlib import Path
import json,threading,urllib.request,urllib.error,sys,time
import visionflow
from visionflow.api import create_server
from visionflow.road_runtime import build_road_runtime
from visionflow.demo import run_demo

root=Path.cwd()
installed=Path(sys.argv[1]).resolve()
assert Path(visionflow.__file__).resolve().is_relative_to(installed)
assert not (installed/"examples").exists()
demo=run_demo(root/"demo")
assert demo["status"]=="passed" and demo["model_mode"]=="fixture"
runtime=build_road_runtime(root/"road", allow_native_costs=False,max_live_sessions=1)
assert runtime.road_cases is not None, runtime.road_unavailable_reason
server=create_server(runtime.controller,host="127.0.0.1",port=0,data_dir=root/"api",
                     evidence_root=runtime.controller.evidence_root,
                     road_sessions=runtime.road_sessions,road_cases=runtime.road_cases,public_demo=True)
thread=threading.Thread(target=server.serve_forever,daemon=True)
thread.start()
url="http://127.0.0.1:"+str(server.server_address[1])
cookie=csrf=""
def request(method,path,payload=None):
    headers={"Origin":url,"Accept":"application/json"}
    if cookie: headers["Cookie"]=cookie
    if csrf: headers["X-CSRF-Token"]=csrf
    if payload is not None: headers["Content-Type"]="application/json"
    req=urllib.request.Request(url+path,method=method,headers=headers,
                               data=json.dumps(payload).encode() if payload is not None else None)
    with urllib.request.urlopen(req,timeout=10) as response:
        return response.status,json.loads(response.read()),response.headers
try:
    status,session,headers=request("GET","/session")
    assert status==200
    cookie=headers["Set-Cookie"].split(";",1)[0]
    csrf=session["csrf_token"]
    status,source,_=request("POST","/road/sessions",{"seed":17})
    assert status==201
    status,accepted,_=request("POST","/road/cases",{"source_id":source["source_id"],"mode":"fixture","fixture_profile":"pending"})
    assert status==202,accepted
    case=accepted["case"]
    deadline=time.monotonic()+10
    while True:
        status,body,_=request("GET","/road/cases/"+case["case_id"])
        assert status==200,body
        snapshot=body
        assert snapshot["phase"] not in {"failed","error"},snapshot
        if snapshot["run_id"] and runtime.controller.status(snapshot["run_id"]).findings:
            break
        if time.monotonic()>deadline: raise TimeoutError("installed wheel did not analyze real road frames")
        time.sleep(.05)
    count=len(runtime.controller.status(snapshot["run_id"]).findings)
    report={"installed_module":str(Path(visionflow.__file__).resolve()),
            "demo_status":demo["status"],"case_start_http":202,"case_phase":snapshot["phase"],
            "actual_findings":count,"model_mode":"fixture","cloud_calls":0}
finally:
    runtime.close()
    server.shutdown()
    thread.join(timeout=3)
    server.server_close()
print(json.dumps(report))
'''


def main(wheel: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="visionflow-wheel-consumer-") as directory:
        root = Path(directory)
        installed, cwd = root / "installed", root / "external-cwd"
        cwd.mkdir()
        install = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(installed), str(wheel)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if install.returncode:
            raise RuntimeError(install.stdout + install.stderr)
        script = cwd / "consumer.py"
        script.write_text(CONSUMER, encoding="utf-8")
        environment = os.environ.copy()
        environment.update(PYTHONPATH=str(installed), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, str(script), str(installed)], cwd=cwd, env=environment,
                                capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        report["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        report["consumer_exit_code"] = result.returncode
        report["owned_temp_removed"] = True
    return report


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=2))
