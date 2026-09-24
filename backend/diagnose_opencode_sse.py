"""One-off diagnostic: capture the real OpenCode /event SSE schema.

The serve+SSE runner maps bus events into frontend log lines. Field names in the
bus payload can differ between OpenCode versions, so this helper starts a headless
`opencode serve` against a real workspace, optionally fires a `run --attach` prompt,
and records:

  * backend/diagnostics/opencode_openapi.json  - the server OpenAPI 3.1 spec (/doc)
  * backend/diagnostics/opencode_sse_capture.jsonl - every raw /event bus event (one JSON per line)

Use the capture to calibrate OpenCodeRunner._map_bus_event field paths.

Usage (from the backend directory):
    python diagnose_opencode_sse.py <workspace_path> [--prompt "..."] [--duration 180] [--no-run]

This tool never writes credentials: it reuses the same redacted child environment as
the runner, and the server binds to loopback only.
"""
import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from app.config import Config
from app.services.opencode_runner import OpenCodeRunner


def _config_dict():
    return {
        key: getattr(Config, key)
        for key in dir(Config)
        if key.isupper()
    }


def main():
    parser = argparse.ArgumentParser(description='Capture OpenCode serve /event SSE schema.')
    parser.add_argument('workspace', help='Workspace path under AUTOMATION_WORKSPACE_ROOT (a Git + Playwright project).')
    parser.add_argument('--prompt', default=None, help='Optional prompt to drive a real run --attach session.')
    parser.add_argument('--duration', type=int, default=180, help='Max seconds to capture events (default 180).')
    parser.add_argument('--no-run', action='store_true', help='Only start serve and capture the bus; do not launch run.')
    args = parser.parse_args()

    out_dir = Path(__file__).resolve().parent / 'diagnostics'
    out_dir.mkdir(parents=True, exist_ok=True)
    openapi_path = out_dir / 'opencode_openapi.json'
    capture_path = out_dir / 'opencode_sse_capture.jsonl'

    config = _config_dict()
    runner = OpenCodeRunner(config)
    _, workspace = runner.resolve_workspace(args.workspace)
    config_path = runner._config_path()

    port = int(config.get('OPENCODE_SERVE_PORT') or 0) or runner._free_port()
    base_url = f'http://{runner._serve_host()}:{port}'
    print(f'[*] starting serve on {base_url} (cwd={workspace})')
    serve = runner._start_serve(workspace, config_path, port)

    stop = threading.Event()
    capture_lock = threading.Lock()
    counts = {}
    run_proc = None
    capture_fh = capture_path.open('w', encoding='utf-8')

    def on_event(event):
        event_type = event.get('type') if isinstance(event, dict) else None
        with capture_lock:
            counts[event_type] = counts.get(event_type, 0) + 1
            capture_fh.write(json.dumps(event, ensure_ascii=False) + '\n')
            capture_fh.flush()
        print(f'    event: {event_type}')

    def sse_worker():
        runner._consume_sse(base_url, stop, on_event)

    try:
        health = runner._wait_serve_healthy(base_url, serve, config.get('OPENCODE_SERVE_STARTUP_TIMEOUT', 60))
        print(f'[*] serve healthy: {health}')

        try:
            doc = requests.get(f'{base_url}/doc', timeout=10)
            openapi_path.write_text(doc.text, encoding='utf-8')
            print(f'[*] wrote OpenAPI spec -> {openapi_path} ({len(doc.text)} bytes)')
        except requests.RequestException as exc:
            print(f'[!] could not fetch /doc: {exc}')

        sse_thread = threading.Thread(target=sse_worker, daemon=True)
        sse_thread.start()
        print(f'[*] capturing /event -> {capture_path}')

        run_proc = None
        if args.prompt and not args.no_run:
            command = runner._command(workspace, prompt=args.prompt, serve_url=base_url)
            print(f'[*] launching run --attach: {" ".join(command[:-1])} "<prompt>"')
            run_proc = subprocess.Popen(
                command,
                cwd=str(workspace),
                env=runner._child_environment(config_path),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
            )

        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            if run_proc is not None and run_proc.poll() is not None:
                print(f'[*] run exited with code {run_proc.returncode}; capturing trailing events for 5s')
                time.sleep(5)
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print('[!] interrupted by user')
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        print(f'[!] diagnostic failed: {exc}')
        return 1
    finally:
        stop.set()
        try:
            if run_proc is not None and run_proc.poll() is None:
                runner._terminate_process(run_proc, workspace, config.get('OPENCODE_BIN'))
        except Exception:
            pass
        try:
            if serve.poll() is None:
                runner._terminate_process(serve, None, config.get('OPENCODE_BIN'))
        except Exception:
            pass
        capture_fh.close()

    print('\n[*] event type counts:')
    for event_type, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        print(f'    {count:>5}  {event_type}')
    print(f'\n[*] done. Inspect {capture_path} to calibrate _map_bus_event.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
