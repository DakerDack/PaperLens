"""Offline desktop checks. Refuse unsupported execution instead of weakening guards.

This is a test harness for trusted repository tests, not an OS security sandbox.
Evidence is retained in a newly created temporary directory, never user data.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]


def allowed_acl_command(args, synthetic_root):
    """Only the two existing ACL probes, on this run's synthetic directory."""
    if not isinstance(args, list) or args[:4] != ["powershell", "-NoProfile", "-NonInteractive", "-Command"] or len(args) != 5:
        return False
    script = args[4]
    match = re.search(r"::(?:Set|Get)AccessControl\('([^']+)'", script)
    if not match or not Path(match[1]).resolve().is_relative_to(synthetic_root):
        return False
    # Reconstruct the exact scripts from their reviewed source expressions.
    # AST evaluation is limited to string constants/addition; no code is run.
    import ast
    candidates = (
        (ROOT / "eval/test_eval.py", "test_revision_three_case_diagnostic_real_private_acl"),
        (ROOT / "eval/run_eval.py", "_check_private_evidence_path"),
    )
    for source, name in candidates:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        assignment = next(node for node in ast.walk(fn) if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "script" for t in node.targets))
        def render(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return render(node.left) + render(node.right)
            if isinstance(node, ast.Name) and node.id == "path_literal":
                return match[1]
            if ast.unparse(node) == 'str(path).replace("\'", "\'\'")':
                return match[1]
            raise ValueError("unsupported ACL expression")
        try:
            if render(assignment.value) == script:
                return True
        except ValueError:
            continue
    return False


def clean_environment() -> dict[str, str]:
    allowed = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC",
               "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}
    environment = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                       PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                       PAPERLENS_DESKTOP="1", PAPERLENS_RUN_MINERU_INTEGRATION="0")
    return environment


def _loopback(host) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install_guard(synthetic_root: Path) -> None:
    """Install before business import, including in every Python child probe."""
    synthetic_root = synthetic_root.resolve()
    protected = (ROOT / "data", Path(r"D:\Hy3"))
    launch_allowed = False

    def audit(event, args):
        if event in {"open", "sqlite3.connect"} and isinstance(args[0], (str, bytes)):
            path = Path(os.fsdecode(args[0])).resolve()
            if any(path.is_relative_to(p) for p in protected):
                raise PermissionError("DESKTOP_VERIFY_PRIVATE_DATA_BLOCKED")
            if path.name.lower().startswith(".env") and not path.is_relative_to(synthetic_root):
                raise PermissionError("DESKTOP_VERIFY_DOTENV_BLOCKED")
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
            host = args[0] if event == "socket.getaddrinfo" else args[1][0]
            if not _loopback(host):
                raise PermissionError("DESKTOP_VERIFY_NETWORK_BLOCKED")
        if event == "os.system":
            raise PermissionError("DESKTOP_VERIFY_PROCESS_BLOCKED")
        if event == "subprocess.Popen":
            if not launch_allowed:
                raise PermissionError("DESKTOP_VERIFY_PROCESS_BLOCKED")

    sys.addaudithook(audit)
    original = subprocess.Popen

    class GuardedPopen(original):
        def __init__(self, args, *positional, **kwargs):
            nonlocal launch_allowed
            if isinstance(args, (list, tuple)) and args and Path(args[0]).resolve() == Path(sys.executable).resolve():
                if positional or kwargs.get("shell"):
                    raise PermissionError("DESKTOP_VERIFY_PROCESS_BLOCKED")
                tail = list(args[1:])
                if tail[:1] == ["-B"]:
                    tail.pop(0)
                if not tail or (tail[0].startswith("-") and tail[0] != "-c"):
                    raise PermissionError("DESKTOP_VERIFY_PROCESS_BLOCKED")
                child_env = clean_environment()
                # Explicit synthetic test variables may differ from parent settings.
                provided = kwargs.get("env", os.environ)
                for key, value in provided.items():
                    if key.upper().startswith(("HY3_", "PAPERLENS_", "MAX_PDF_")):
                        child_env[key] = value
                child_env["PYTHONPATH"] = str(ROOT)
                child_env["PAPERLENS_RUN_MINERU_INTEGRATION"] = "0"
                child_env["TEMP"] = child_env["TMP"] = str(synthetic_root)
                bootstrap = (
                    "import sys,runpy;from pathlib import Path;"
                    "from tools.desktop_verify import install_guard;"
                    f"install_guard(Path({str(synthetic_root)!r}));"
                )
                if tail[0] == "-c":
                    script, rest = tail[1], tail[2:]
                    bootstrap += f"sys.argv=['-c']+sys.argv[1:];exec(compile({script!r},'<probe>','exec'))"
                else:
                    script, rest = tail[0], tail[1:]
                    bootstrap += f"sys.argv=[{script!r}]+sys.argv[1:];runpy.run_path({script!r},run_name='__main__')"
                args = [sys.executable, "-B", "-c", bootstrap, *rest]
                kwargs["env"] = child_env
            elif kwargs.get("shell") or (args != ["git", "rev-parse", "HEAD"]
                                         and not allowed_acl_command(args, synthetic_root)):
                raise PermissionError("DESKTOP_VERIFY_PROCESS_BLOCKED")
            launch_allowed = True
            try:
                super().__init__(args, *positional, **kwargs)
            finally:
                launch_allowed = False

    subprocess.Popen = GuardedPopen


def run_frontend() -> int:
    """Use temporary configs: empty dotenv dir, guarded Node, proxied Chromium."""
    evidence = Path(tempfile.mkdtemp(prefix="paperlens-desktop-frontend-"))
    print(f"EVIDENCE_DIR={evidence}", flush=True)
    frontend = ROOT / "frontend"
    node = shutil.which("node")
    npm = shutil.which("npm.cmd")
    if not node or not npm:
        raise PermissionError("frontend tools unavailable")
    # All Chromium traffic except numeric loopback is sent to a local rejecting
    # proxy. This applies below page.route, including preconfigured UI fixtures.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    class RejectProxy(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            self.send_error(403)
        def do_GET(self):
            if self.path == "/guard-health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"synthetic-loopback-ok")
            else:
                self.send_error(403)
        do_POST = do_CONNECT
        def log_message(self, *args):
            pass
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), RejectProxy)
    Thread(target=proxy.serve_forever, daemon=True).start()
    launch = {"proxy": {"server": f"http://127.0.0.1:{proxy.server_port}",
                        "bypass": "127.0.0.1,localhost,[::1]"},
              "args": ["--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]}
    guard = evidence / "node-guard.cjs"
    guard.write_text(r'''
const fs=require('node:fs'), path=require('node:path'), net=require('node:net');
const {syncBuiltinESMExports}=require('node:module');
const root=__ROOT__, safe=__SAFE__;
function check(p) {
  if(typeof p!=='string' && !Buffer.isBuffer(p) && !(p instanceof URL)) return;
  if(p instanceof URL) p=require('node:url').fileURLToPath(p);
  p=path.resolve(p.toString());
  const inside=(a,b)=>a===b||a.startsWith(b+path.sep);
  if((path.basename(p).toLowerCase().startsWith('.env')&&!inside(p,safe)) ||
     inside(p.toLowerCase(),path.join(root,'data').toLowerCase()) ||
     inside(p.toLowerCase(),path.resolve('D:/Hy3').toLowerCase())) throw Error('DESKTOP_VERIFY_FILE_BLOCKED');
}
for(const name of ['readFileSync','readFile','openSync','open','createReadStream']) {
  const original=fs[name]; fs[name]=function(p,...args){check(p);return original.call(this,p,...args)};
}
for(const name of ['readFile','open']) {
  const original=fs.promises[name]; fs.promises[name]=async function(p,...args){check(p);return original.call(this,p,...args)};
}
const connect=net.Socket.prototype.connect;
net.Socket.prototype.connect=function(...args) {
  const o=(Array.isArray(args[0])?args[0]:net._normalizeArgs(args))[0];
  if(!o.path && !['127.0.0.1','localhost','::1'].includes(o.host||'localhost'))
    throw Error('DESKTOP_VERIFY_NETWORK_BLOCKED');
  return connect.apply(this,args);
};
syncBuiltinESMExports();
'''.replace("__ROOT__", json.dumps(str(ROOT))).replace("__SAFE__", json.dumps(str(evidence))), encoding="utf-8")
    environment = clean_environment()
    environment["NODE_OPTIONS"] = f'--require="{guard.as_posix()}"'
    # Browser binaries are an existing dependency cache, not a user profile.
    environment["LOCALAPPDATA"] = os.environ.get("LOCALAPPDATA", str(Path.home()/"AppData/Local"))
    environment["APPDATA"] = str(evidence)
    vite = evidence / "vite.config.mjs"
    vitest = evidence / "vitest.config.mjs"
    playwright = evidence / "playwright.config.mjs"
    vite.write_text(f"export default {{envDir:{json.dumps(str(evidence))}}};", encoding="utf-8")
    vitest.write_text(
        f"import config from {json.dumps((frontend/'vitest.config.ts').as_uri())};\n"
        f"import {{playwright}} from {json.dumps((frontend/'node_modules/@vitest/browser-playwright/dist/index.js').as_uri())};\n"
        f"config.envDir={json.dumps(str(evidence))};\n"
        f"config.test.browser.provider=playwright({{launchOptions:{json.dumps(launch)}}});\n"
        "export default config;", encoding="utf-8")
    playwright.write_text(
        f"import config from {json.dumps((frontend/'playwright.config.ts').as_uri())};\n"
        f"config.testDir={json.dumps(str(frontend/'e2e'))};\n"
        f"config.use.launchOptions={json.dumps(launch)};\n"
        f"config.webServer.command+={json.dumps(' --config '+subprocess.list2cmdline([str(vite)]))};\n"
        f"config.webServer.cwd={json.dumps(str(frontend))};\n"
        "export default config;", encoding="utf-8")
    probe = evidence / "probe.cjs"
    probe.write_text(
        "const fs=require('node:fs'),net=require('node:net');let n=0;"
        f"try{{fs.readFileSync({json.dumps(str(ROOT/'.env'))})}}catch(e){{if(e.message==='DESKTOP_VERIFY_FILE_BLOCKED')n++}};"
        f"try{{fs.readFileSync({json.dumps('D:/Hy3/synthetic-never-open')})}}catch(e){{if(e.message==='DESKTOP_VERIFY_FILE_BLOCKED')n++}};"
        "try{net.connect({host:'192.0.2.1',port:443})}catch(e){if(e.message==='DESKTOP_VERIFY_NETWORK_BLOCKED')n++};"
        "console.log('NODE_GUARD_COUNT='+n);if(n!==3)process.exit(2);"
        f"const {{chromium}}=require({json.dumps(str(frontend/'node_modules/playwright'))});"
        f"(async()=>{{const b=await chromium.launch({json.dumps(launch)});const p=await b.newPage();"
        f"const local=await p.goto('http://127.0.0.1:{proxy.server_port}/guard-health');if(local.status()!==200)throw Error('LOOPBACK_FAILED');"
        "let blocked=false;try{await p.goto('https://192.0.2.1/',{timeout:5000})}catch(e){console.log(String(e));blocked=String(e).includes('ERR_TUNNEL_CONNECTION_FAILED')}"
        "await b.close();if(!blocked)process.exit(2);console.log('NODE_AND_CHROMIUM_GUARDS_VERIFIED')})().catch(e=>{console.log(String(e));process.exit(2)});",
        encoding="utf-8")
    commands = [
        [node, str(probe)],
        [npm, "run", "test", "--", "--run", "--config", str(vitest)],
        [npm, "run", "build", "--", "--config", str(vite)],
        [str(frontend/"node_modules/.bin/playwright.cmd"), "test", "--config", str(playwright)],
    ]
    try:
        for index, command in enumerate(commands):
            with (evidence/f"{index}.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=frontend, env=environment,
                                        stdout=log, stderr=subprocess.STDOUT)
            print(f"FRONTEND_STEP={index} EXIT={result.returncode}", flush=True)
            if result.returncode:
                return result.returncode
        return 0
    finally:
        proxy.shutdown()
        proxy.server_close()


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("focused", "backend", "frontend"), required=True)
    parser.add_argument("--tests", nargs="+")
    args = parser.parse_args(arguments)
    if args.suite == "focused" and not args.tests:
        parser.error("focused requires --tests")
    if args.suite != "focused" and args.tests:
        parser.error("--tests is only valid for focused")
    if args.suite == "frontend":
        try:
            return run_frontend()
        except (OSError, ValueError):
            print("DESKTOP_VERIFY_REFUSED=FRONTEND_ISOLATION_UNAVAILABLE", flush=True)
            return 2
    paths = args.tests if args.suite == "focused" else ["backend/tests", "eval/test_eval.py"]
    for name in paths:
        path = (ROOT / name).resolve()
        if not (path.is_relative_to(ROOT / "backend/tests") or path == ROOT / "eval/test_eval.py"):
            parser.error("only repository test paths are allowed")
        if not path.exists():
            parser.error("test path does not exist")
    evidence = Path(tempfile.mkdtemp(prefix="paperlens-desktop-verify-"))
    print(f"EVIDENCE_DIR={evidence}", flush=True)
    environment = clean_environment()
    environment["TEMP"] = environment["TMP"] = str(evidence)
    os.environ.clear()
    os.environ.update(environment)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    install_guard(evidence)
    # Real reads are forbidden above; now prove refusal before importing tests.
    try:
        (ROOT / ".env").read_bytes()
    except PermissionError:
        pass
    else:
        print("DESKTOP_VERIFY_REFUSED=GUARD_INACTIVE", flush=True)
        return 2
    import pytest
    return pytest.main([*paths, "-q", "--tb=short", "-p", "no:cacheprovider",
                        "--basetemp", str(evidence / "pytest")])


if __name__ == "__main__":
    raise SystemExit(main())
