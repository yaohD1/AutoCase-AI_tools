import json
import hashlib
import os
import re
import socket
import subprocess
import threading
import time
import zipfile
from collections import deque
from pathlib import Path
from queue import Empty, Full, Queue

import requests

from app.utils.sensitive import redact_sensitive_text


class WorkspaceBusyError(ValueError):
    pass


class OpenCodeRunner:
    _locks = {}
    _locks_guard = threading.Lock()
    _active_runs = {}
    _active_runs_guard = threading.Lock()

    def __init__(self, app_config):
        self.app_config = app_config

    @classmethod
    def _register_active_run(cls, generation_id):
        with cls._active_runs_guard:
            return cls._active_runs.setdefault(
                generation_id,
                {'cancel_event': threading.Event(), 'process': None}
            )

    @classmethod
    def register_run(cls, generation_id):
        return cls._register_active_run(generation_id)

    @classmethod
    def _set_active_process(cls, generation_id, process):
        with cls._active_runs_guard:
            record = cls._active_runs.get(generation_id)
            if record:
                record['process'] = process

    @classmethod
    def _unregister_active_run(cls, generation_id):
        with cls._active_runs_guard:
            cls._active_runs.pop(generation_id, None)

    @classmethod
    def request_cancel(cls, generation_id):
        with cls._active_runs_guard:
            record = cls._active_runs.get(generation_id)
            if not record:
                return False
            record['cancel_event'].set()
            return True

    def resolve_workspace(self, workspace_path):
        root = Path(self.app_config['AUTOMATION_WORKSPACE_ROOT']).resolve()
        provided = Path(workspace_path or '').expanduser()
        workspace = (provided if provided.is_absolute() else root / provided).resolve()
        if workspace != root and root not in workspace.parents:
            raise ValueError('workspace_path must stay inside AUTOMATION_WORKSPACE_ROOT')
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError('workspace_path must point to an existing directory')
        return root, workspace

    def validate_workspace(self, workspace):
        package_path = workspace / 'package.json'
        if not package_path.exists():
            raise ValueError('workspace must contain package.json')
        try:
            package = json.loads(package_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ValueError('workspace package.json is invalid')
        dependencies = {}
        dependencies.update(package.get('dependencies') or {})
        dependencies.update(package.get('devDependencies') or {})
        if '@playwright/test' not in dependencies:
            raise ValueError('workspace package.json must declare @playwright/test')
        try:
            result = subprocess.run(
                ['git', '-C', str(workspace), 'rev-parse', '--show-toplevel'],
                check=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=10
            )
            repository_root = Path(result.stdout.strip()).resolve()
            if repository_root != workspace:
                raise ValueError('workspace must be the Git repository root')
        except (subprocess.SubprocessError, OSError):
            raise ValueError('workspace must be a Git repository')

    def resolve_storage_state(self, workspace, storage_state_path):
        if not storage_state_path:
            return None
        state = Path(storage_state_path).expanduser()
        if state.is_absolute():
            resolved = state.resolve()
        else:
            resolved = (workspace / state).resolve()
        if resolved != workspace and workspace not in resolved.parents:
            raise ValueError('auth_state_path must stay inside workspace_path')
        if not resolved.exists() or not resolved.is_file():
            raise ValueError('auth_state_path does not exist')
        return resolved

    def _config_path(self):
        project_root = Path(__file__).resolve().parents[3]
        configured = self.app_config.get('OPENCODE_CONFIG') or str(project_root / 'opencode.json')
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise ValueError('OpenCode config file was not found')
        return path

    def _cancel_path(self, generation_id):
        root = Path(self.app_config['AUTOMATION_CONTROL_FOLDER']).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root / f'automation-cancel-{generation_id}'

    @classmethod
    def _workspace_lock(cls, workspace):
        key = str(workspace).lower()
        with cls._locks_guard:
            lock = cls._locks.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False):
            raise WorkspaceBusyError('another OpenCode generation is already running in this workspace')
        return lock

    @staticmethod
    def _process_exists(pid):
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ValueError, SystemError):
            return False

    @staticmethod
    def _live_backend_parent(pid):
        if os.name == 'nt':
            try:
                script = (
                    f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\"; "
                    f"if ($p) {{ (Get-CimInstance Win32_Process -Filter \"ProcessId = $($p.ParentProcessId)\").CommandLine }}"
                )
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=5,
                    check=False
                )
                parent_command = result.stdout.strip().lower().replace('\\', '/')
                return 'backend/run.py' in parent_command or 'backend\\run.py' in result.stdout.lower()
            except (OSError, subprocess.SubprocessError):
                return False
        try:
            proc_dir = Path('/proc') / str(int(pid))
            parent_pid = int((proc_dir / 'stat').read_text(encoding='ascii').split(') ', 1)[1].split()[1])
            command = (Path('/proc') / str(parent_pid) / 'cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', errors='replace').lower()
            return 'backend/run.py' in command
        except (OSError, ValueError, UnicodeError, IndexError):
            return False

    def _acquire_file_lock(self, workspace):
        lock_path = workspace / '.autocase' / 'generation.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            descriptor = None
            try:
                descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(descriptor, f'launcher:{os.getpid()}'.encode('ascii'))
                os.close(descriptor)
                descriptor = None
                return lock_path
            except FileExistsError:
                try:
                    raw_owner = lock_path.read_text(encoding='ascii').strip()
                    if raw_owner.startswith('opencode:'):
                        owner = int(raw_owner.split(':', 1)[1])
                    elif raw_owner.startswith('launcher:'):
                        owner = None
                    else:
                        owner = int(raw_owner)
                except (OSError, ValueError):
                    owner = None
                if owner and not self._process_exists(owner):
                    try:
                        lock_path.unlink()
                    except OSError:
                        pass
                    continue
                if owner is None:
                    try:
                        stale = time.time() - lock_path.stat().st_mtime > 300
                    except OSError:
                        stale = False
                    if stale:
                        try:
                            lock_path.unlink()
                        except OSError:
                            pass
                        continue
                raise WorkspaceBusyError('another OpenCode generation is already running in this workspace')
            except OSError:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                raise
        raise WorkspaceBusyError('another OpenCode generation is already running in this workspace')

    @staticmethod
    def _release_file_lock(lock_path, expected_owner=None):
        try:
            if expected_owner and lock_path.read_text(encoding='ascii').strip() != expected_owner:
                return
            lock_path.unlink()
        except (OSError, UnicodeError):
            pass

    @staticmethod
    def _terminate_process(process, workspace=None, expected_binary=None):
        if process is None or process.poll() is not None:
            return True
        try:
            # terminate_pid force-kills the whole server tree (shim + real binary + MCP
            # children). Its own liveness probe uses os.kill(pid, 0), which keeps reporting
            # True on Windows while we still hold the child handle, so we do not trust its
            # return value here. process.wait() on the owned handle is authoritative.
            OpenCodeRunner.terminate_pid(process.pid, workspace, expected_binary)
            process.wait(timeout=10)
            return True
        except (OSError, subprocess.SubprocessError):
            return process.poll() is not None

    @staticmethod
    def _lock_owner_matches(workspace, pid):
        # The headless server is launched with cwd=workspace, so on Windows its command line
        # carries no workspace path. The generation lock records the exact owning pid, which
        # lets us tie a bare `opencode serve` process back to this workspace safely.
        try:
            owner = (Path(workspace) / '.autocase' / 'generation.lock').read_text(encoding='ascii').strip()
        except (OSError, UnicodeError, ValueError):
            return False
        return owner == f'opencode:{int(pid)}'

    @staticmethod
    def terminate_pid(pid, expected_workspace=None, expected_binary=None):
        if not pid:
            return True
        if os.name == 'nt':
            try:
                if not OpenCodeRunner._process_exists(pid):
                    return True
                command_line = subprocess.run(
                    [
                        'powershell', '-NoProfile', '-NonInteractive', '-Command',
                        f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\").CommandLine"
                    ],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=5,
                    check=False
                ).stdout.strip()
                binary_name = Path(expected_binary or 'opencode').stem.lower()
                if not command_line or binary_name not in command_line.lower():
                    return False
                if expected_workspace:
                    actual_command = command_line.lower().replace('\\', '/')
                    expected_path = str(Path(expected_workspace).resolve()).lower().replace('\\', '/')
                    if expected_path not in actual_command and not OpenCodeRunner._lock_owner_matches(expected_workspace, pid):
                        return False
                result = subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(pid)],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=10,
                    check=False
                )
                if result.returncode != 0:
                    return False
                deadline = time.monotonic() + 5
                while OpenCodeRunner._process_exists(pid) and time.monotonic() < deadline:
                    time.sleep(0.1)
                return not OpenCodeRunner._process_exists(pid)
            except (OSError, subprocess.SubprocessError):
                return False
        else:
            try:
                if not OpenCodeRunner._process_exists(pid):
                    return True
                proc_dir = Path('/proc') / str(int(pid))
                command_line = (proc_dir / 'cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', errors='replace')
                process_cwd = os.path.realpath(proc_dir / 'cwd')
                binary_name = Path(expected_binary or 'opencode').stem.lower()
                if binary_name not in command_line.lower():
                    return False
                if expected_workspace:
                    expected_path = str(Path(expected_workspace).resolve())
                    if expected_path not in command_line and expected_path != process_cwd:
                        return False
            except (OSError, ProcessLookupError, ValueError, UnicodeError):
                return False
            try:
                os.killpg(pid, 9)
            except ProcessLookupError:
                try:
                    os.kill(pid, 9)
                except OSError:
                    return False
            except OSError:
                return False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if not OpenCodeRunner._process_exists(pid):
                    try:
                        os.killpg(pid, 0)
                    except OSError:
                        return True
                time.sleep(0.1)
            return not OpenCodeRunner._process_exists(pid)

    def _command(self, workspace, model=None, prompt='', serve_url=None):
        binary = self.app_config.get('OPENCODE_BIN') or 'opencode'
        command = [
            binary, 'run',
            '--agent', 'playwright-test-orchestrator',
            '--dir', str(workspace),
            '--format', 'json',
            '--print-logs',
            '--auto'
        ]
        if serve_url:
            command.extend(['--attach', serve_url])
        if self.app_config.get('OPENCODE_PURE'):
            command.insert(2, '--pure')
        if model:
            command.extend(['--model', model])
        command.append(prompt)
        return command

    @staticmethod
    def _exit_code_details(return_code):
        if return_code is None:
            return {'decimal': None, 'hex': None, 'name': 'unknown'}
        unsigned = return_code & 0xffffffff
        known = {
            0xC0000409: 'STATUS_STACK_BUFFER_OVERRUN',
            0xC0000005: 'STATUS_ACCESS_VIOLATION',
            0xC0000135: 'STATUS_DLL_NOT_FOUND',
            0xC0000142: 'STATUS_DLL_INIT_FAILED'
        }
        return {
            'decimal': return_code,
            'hex': f'0x{unsigned:08X}',
            'name': known.get(unsigned, 'unknown')
        }

    def _child_environment(self, config_path):
        blocked_markers = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASSWD', 'AUTH', 'COOKIE', 'CREDENTIAL', 'DATABASE', 'REDIS', 'SENTRY', 'DSN')
        env = {
            key: value for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in blocked_markers)
        }
        env['OPENCODE_CONFIG'] = str(config_path)
        return env

    def _serve_host(self):
        return self.app_config.get('OPENCODE_SERVE_HOST') or '127.0.0.1'

    @staticmethod
    def _free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(('127.0.0.1', 0))
            return probe.getsockname()[1]

    def _serve_command(self, port):
        binary = self.app_config.get('OPENCODE_BIN') or 'opencode'
        command = [binary, 'serve', '--port', str(port), '--hostname', self._serve_host()]
        if self.app_config.get('OPENCODE_PURE'):
            command.insert(2, '--pure')
        return command

    def _start_serve(self, workspace, config_path, port):
        popen_kwargs = {
            'cwd': str(workspace),
            'env': self._child_environment(config_path),
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.PIPE,
            'text': True,
            'encoding': 'utf-8',
            'errors': 'replace',
            'bufsize': 1,
        }
        command = self._serve_command(port)
        if os.name != 'nt':
            return subprocess.Popen(command, start_new_session=True, **popen_kwargs)
        # On Windows, isolate the server into its own process group and (when the OS
        # permits) break it away from the launcher's Job Object. This guarantees that
        # tearing down the OpenCode process tree with `taskkill /T` can never cascade
        # back into the Flask backend, even when both run inside an IDE-managed job.
        group_flag = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
        breakaway_flag = getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0x01000000)
        for flags in (group_flag | breakaway_flag, group_flag):
            try:
                return subprocess.Popen(command, creationflags=flags, **popen_kwargs)
            except OSError:
                continue
        return subprocess.Popen(command, **popen_kwargs)

    @staticmethod
    def _drain_stderr(stream, sink):
        if not stream:
            return
        try:
            for line in stream:
                if line is None:
                    continue
                line = line.rstrip('\r\n')
                if not line:
                    continue
                try:
                    sink(line)
                except Exception:
                    pass
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _wait_serve_healthy(self, base_url, process, timeout):
        deadline = time.monotonic() + max(5, int(timeout or 60))
        last_error = 'no response'
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                raise RuntimeError(f'OpenCode server exited early with code {process.returncode}')
            try:
                response = requests.get(f'{base_url}/global/health', timeout=3)
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    if not isinstance(payload, dict) or payload.get('healthy', True):
                        return payload if isinstance(payload, dict) else {}
                last_error = f'health endpoint returned {response.status_code}'
            except requests.RequestException as exc:
                last_error = str(exc)
            time.sleep(0.5)
        raise RuntimeError(f'OpenCode server did not become healthy within {timeout} seconds: {last_error}')

    def _consume_sse(self, base_url, stop_event, on_event, reconnect_limit=6):
        attempt = 0
        while not stop_event.is_set() and attempt <= reconnect_limit:
            response = None
            try:
                response = requests.get(f'{base_url}/event', stream=True, timeout=(5, 90))
                if response.status_code != 200:
                    attempt += 1
                    if stop_event.wait(1.0):
                        break
                    continue
                attempt = 0
                # /event 的 Content-Type 是 text/event-stream 且不带 charset，requests 会按
                # RFC 对 text/* 默认使用 Latin-1 解码，导致 UTF-8 中文和 ' 等字符损坏成乱码
                # （如 I'm→Iâm、中文→æä¼é­）。这里强制 UTF-8，保证 AI 输出正确入库。
                response.encoding = 'utf-8'
                data_buffer = []
                for raw_line in response.iter_lines(decode_unicode=True):
                    if stop_event.is_set():
                        break
                    if raw_line is None:
                        continue
                    line = raw_line.rstrip('\r')
                    if line == '':
                        if data_buffer:
                            self._dispatch_sse_payload('\n'.join(data_buffer), on_event)
                            data_buffer = []
                        continue
                    if line.startswith(':'):
                        continue
                    if line.startswith('data:'):
                        data_buffer.append(line[5:].lstrip())
                if data_buffer:
                    self._dispatch_sse_payload('\n'.join(data_buffer), on_event)
                if stop_event.is_set():
                    break
                attempt += 1
                if stop_event.wait(1.0):
                    break
            except requests.RequestException:
                if stop_event.is_set():
                    break
                attempt += 1
                if stop_event.wait(1.0):
                    break
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

    @staticmethod
    def _dispatch_sse_payload(payload, on_event):
        try:
            event = json.loads(payload)
        except (TypeError, ValueError):
            return
        if not isinstance(event, dict):
            return
        try:
            on_event(event)
        except Exception:
            pass

    def _abort_sessions(self, base_url, session_ids):
        for session_id in list(session_ids or []):
            if not session_id:
                continue
            try:
                requests.post(f'{base_url}/session/{session_id}/abort', timeout=3)
            except requests.RequestException:
                pass

    @staticmethod
    def _split_model(model):
        # OpenCode model strings are `providerID/modelID`; the HTTP API needs them split.
        if not model or '/' not in str(model):
            return None
        provider_id, _, model_id = str(model).partition('/')
        provider_id = provider_id.strip()
        model_id = model_id.strip()
        if not provider_id or not model_id:
            return None
        return {'id': model_id, 'providerID': provider_id}

    def _auto_reply_permission(self, base_url, event_type, properties):
        # Replaces `run --auto`: any permission the agent config marks as `ask`
        # surfaces here and is approved so the workflow never blocks on a prompt.
        request_id = properties.get('id')
        session_id = properties.get('sessionID') or properties.get('sessionId')
        if not request_id or not base_url:
            return
        try:
            if event_type == 'permission.v2.asked' and session_id:
                requests.post(
                    f'{base_url}/api/session/{session_id}/permission/{request_id}/reply',
                    json={'reply': 'always'}, timeout=5
                )
            else:
                requests.post(
                    f'{base_url}/permission/{request_id}/reply',
                    json={'reply': 'always'}, timeout=5
                )
        except requests.RequestException:
            pass

    def _handle_bus_side_effects(self, event, driver, base_url):
        # Side effects that must run for every server event: auto-approve permissions,
        # track when the orchestrator session starts/finishes, and capture fatal errors.
        if not isinstance(event, dict):
            return
        event_type = str(event.get('type') or '')
        properties = event.get('properties') if isinstance(event.get('properties'), dict) else {}
        if event_type in ('permission.asked', 'permission.v2.asked'):
            self._auto_reply_permission(base_url, event_type, properties)
            return
        session_id = driver.get('sid')
        if not session_id:
            return
        event_session = properties.get('sessionID') or properties.get('sessionId')
        if event_session != session_id:
            return
        if event_type == 'session.status':
            status = properties.get('status') if isinstance(properties.get('status'), dict) else {}
            if status.get('type') == 'busy':
                driver['busy'] = True
        elif event_type in ('message.part.updated', 'message.part.delta', 'message.updated'):
            driver['busy'] = True
        elif event_type == 'session.error':
            driver['fatal'] = self._summarize_error(properties.get('error')) or 'OpenCode 报告会话错误。'
        elif event_type == 'session.idle' and driver['busy']:
            driver['done'].set()

    _STAGE_LABELS = {
        'orchestrator': '总控协调', 'planner': 'Planner 规划', 'generator': 'Generator 生成',
        'test_run': '执行测试', 'healer': 'Healer 修复'
    }

    @staticmethod
    def _agent_to_stage(agent):
        value = (agent or '').lower()
        if 'heal' in value or 'fix' in value or 'debug' in value:
            return 'healer'
        if 'generat' in value or 'write' in value:
            return 'generator'
        if 'plan' in value or 'explor' in value:
            return 'planner'
        if 'test' in value and 'run' in value:
            return 'test_run'
        return 'orchestrator'

    @staticmethod
    def _info_session(properties):
        info = properties.get('info')
        if isinstance(info, dict):
            return info.get('sessionID') or info.get('sessionId')
        return None

    @staticmethod
    def _summarize_error(error):
        if not error:
            return ''
        if isinstance(error, dict):
            for key in ('message', 'error', 'reason', 'detail', 'data'):
                if error.get(key):
                    return str(error[key])[:1500]
            return json.dumps(error, ensure_ascii=False)[:1500]
        return str(error)[:1500]

    @staticmethod
    def _summarize_tool_input(tool_input):
        if not isinstance(tool_input, dict):
            return ''
        for key in ('url', 'command', 'filePath', 'path', 'pattern', 'description', 'title', 'query', 'subagent_type'):
            value = tool_input.get(key)
            if value:
                return str(value)[:200]
        return ''

    def _stage_for(self, state, session_id):
        if session_id and session_id in state['session_stage']:
            stage = state['session_stage'][session_id]
            state['current_stage'] = stage
            return stage
        return state.get('current_stage') or 'orchestrator'

    def _register_task_session(self, state, tool_state):
        metadata = tool_state.get('metadata') if isinstance(tool_state.get('metadata'), dict) else {}
        child_id = metadata.get('sessionID') or metadata.get('sessionId')
        if not child_id:
            return
        tool_input = tool_state.get('input') if isinstance(tool_state.get('input'), dict) else {}
        agent = (tool_input.get('subagent_type') or tool_input.get('subagent')
                 or tool_input.get('agent') or tool_input.get('description') or '')
        stage = self._agent_to_stage(str(agent))
        state['session_stage'].setdefault(child_id, stage)
        if stage == 'healer':
            state['healer_sessions'].add(child_id)

    def _map_bus_event(self, event, state):
        if not isinstance(event, dict):
            return None
        event_type = str(event.get('type') or '')
        properties = event.get('properties')
        if not isinstance(properties, dict):
            properties = {}

        if event_type in ('server.connected', 'message.part.removed'):
            return None

        if event_type == 'message.part.delta':
            return self._map_delta(properties, state)

        if event_type == 'session.error':
            session_id = properties.get('sessionID') or self._info_session(properties)
            message = self._summarize_error(properties.get('error')) or 'OpenCode 报告会话错误。'
            return self._stage_for(state, session_id), 'error', message

        if event_type == 'session.idle':
            session_id = properties.get('sessionID') or self._info_session(properties)
            stage = self._stage_for(state, session_id)
            label = self._STAGE_LABELS.get(stage, stage)
            # 仅代表该会话本轮 LLM 轮次结束（不等于交付物完成），措辞避免误导为“已成功”。
            return stage, 'diagnostic', f'{label} 本轮结束，等待下一步调度。'

        if event_type in ('permission.asked', 'permission.v2.asked'):
            session_id = properties.get('sessionID') or properties.get('sessionId')
            if event_type == 'permission.v2.asked':
                action = properties.get('action') or 'permission'
                detail = ', '.join(str(item) for item in (properties.get('resources') or []))[:200]
            else:
                action = properties.get('permission') or 'permission'
                detail = ', '.join(str(item) for item in (properties.get('patterns') or []))[:200]
            suffix = f'（{detail}）' if detail else ''
            return self._stage_for(state, session_id), 'diagnostic', f'权限请求：{action}{suffix} — 已自动批准'

        if event_type in ('message.part.updated', 'message.updated'):
            part = properties.get('part')
            if isinstance(part, dict):
                return self._map_part(part, state)
            return None

        return None

    def _map_part(self, part, state):
        if not isinstance(part, dict):
            return None
        session_id = part.get('sessionID') or part.get('sessionId')
        part_type = part.get('type')
        part_id = part.get('id')
        if part_id and part_type:
            part_types = state.setdefault('part_type', {})
            part_types[part_id] = part_type
            if len(part_types) > 2000:
                part_types.clear()
        stage = self._stage_for(state, session_id)

        if len(state['seen']) > 8000:
            state['seen'].clear()

        if part_type == 'tool':
            tool = part.get('tool') or 'tool'
            tool_state = part.get('state') if isinstance(part.get('state'), dict) else {}
            status = tool_state.get('status') or 'unknown'
            if tool == 'task':
                self._register_task_session(state, tool_state)
                stage = self._stage_for(state, session_id)
            if status not in ('running', 'completed', 'error'):
                return None
            key = (part_id, tool, status)
            if key in state['seen']:
                return None
            state['seen'].add(key)
            detail = tool_state.get('title') or self._summarize_tool_input(tool_state.get('input'))
            message = f'{tool} [{status}]' + (f' {detail}' if detail else '')
            if status == 'error':
                output = tool_state.get('output') or tool_state.get('error')
                if output:
                    message = f'{message}\n{str(output)[:1200]}'
                return stage, 'error', message
            return stage, 'tool', message

        if part_type == 'text':
            time_info = part.get('time') if isinstance(part.get('time'), dict) else {}
            completed = time_info.get('end') or time_info.get('completed')
            text = (part.get('text') or '').strip()
            if not text:
                return None
            if not completed:
                return None
            key = (part_id, 'text-final')
            if key in state['seen']:
                return None
            state['seen'].add(key)
            return stage, 'text', text[:2000]

        if part_type == 'reasoning':
            time_info = part.get('time') if isinstance(part.get('time'), dict) else {}
            completed = time_info.get('end') or time_info.get('completed')
            text = (part.get('text') or '').strip()
            if not text or not completed:
                return None
            key = (part_id, 'reasoning-final')
            if key in state['seen']:
                return None
            state['seen'].add(key)
            return stage, 'diagnostic', f'[推理] {text[:800]}'

        if part_type in ('step-start', 'step-finish', 'file', 'patch'):
            # patch 是 OpenCode 内部的文件变更快照（大量 .playwright-mcp/page-*.yml 噪声），
            # 真正的文件写入已由 tool(write/edit) 事件和结束时的 git 变更覆盖，无需原样 dump。
            return None

        compact = json.dumps(part, ensure_ascii=False)[:800]
        key = (part_id, part_type, 'raw')
        if key in state['seen']:
            return None
        state['seen'].add(key)
        return stage, 'diagnostic', f'{part_type}: {compact}'

    def _map_delta(self, properties, state):
        part_id = properties.get('partID') or properties.get('partId') or properties.get('id')
        chunk = properties.get('delta')
        if not part_id or not isinstance(chunk, str) or not chunk:
            return None
        session_id = properties.get('sessionID') or properties.get('sessionId')
        field = properties.get('field') or ''
        buffers = state.setdefault('delta_buf', {})
        if len(buffers) > 500:
            buffers.clear()
        entry = buffers.get(part_id)
        if entry is None:
            entry = {
                'text': '', 'session': session_id, 'field': field,
                'last_emit': 0.0, 'emitted': 0,
                'type': state.get('part_type', {}).get(part_id),
            }
            buffers[part_id] = entry
        entry['text'] = (entry['text'] + chunk)[-4000:]
        if session_id and not entry.get('session'):
            entry['session'] = session_id
        now = time.monotonic()
        grown = len(entry['text']) - entry['emitted']
        if now - entry['last_emit'] < 2.5 or grown < 24:
            return None
        entry['last_emit'] = now
        entry['emitted'] = len(entry['text'])
        tail = entry['text'][-400:].strip()
        if not tail:
            return None
        part_type = entry.get('type') or state.get('part_type', {}).get(part_id) or entry.get('field')
        stage = self._stage_for(state, entry['session'])
        prefix = '[思考] ' if part_type == 'reasoning' else ''
        return stage, 'text', f'{prefix}…{tail}'

    @staticmethod
    def _git_snapshot(workspace, extra_roots=()):
        listed = subprocess.run(
            ['git', '-C', str(workspace), 'ls-files', '-co', '--exclude-standard', '-z'],
            check=True,
            capture_output=True,
            timeout=20
        ).stdout.decode('utf-8', errors='replace').split('\0')
        status = OpenCodeRunner._git_status(workspace)
        snapshot = {}
        for raw_path in listed:
            if not raw_path:
                continue
            path = raw_path.replace('\\', '/')
            if path == '.autocase/generation.lock':
                continue
            target = (workspace / path).resolve()
            if target.is_file():
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
            else:
                digest = '<missing>'
            snapshot[path] = {'hash': digest, 'status': status.get(path, '')}
        for root_name in extra_roots:
            root = (workspace / root_name).resolve()
            if root != workspace and workspace not in root.parents:
                continue
            if not root.exists() or not root.is_dir():
                continue
            for target in root.rglob('*'):
                if not target.is_file() or target.is_symlink():
                    continue
                relative = target.relative_to(workspace).as_posix()
                if relative in snapshot:
                    continue
                snapshot[relative] = {
                    'hash': hashlib.sha256(target.read_bytes()).hexdigest(),
                    'status': status.get(relative, '??')
                }
        return snapshot

    @staticmethod
    def _changed_files(workspace, before, extra_roots=()):
        after = OpenCodeRunner._git_snapshot(workspace, extra_roots)
        paths = set(before) | set(after)
        changed = []
        for path in sorted(paths):
            old = before.get(path, {}).get('hash')
            new = after.get(path, {}).get('hash')
            if old == new:
                continue
            changed.append({
                'path': path,
                'status': after.get(path, {}).get('status') or 'D',
                'sha256': after.get(path, {}).get('hash') if path in after else None,
                'kind': OpenCodeRunner._artifact_kind(path)
            })
        return changed

    @staticmethod
    def _artifact_kind(path):
        normalized = path.replace('\\', '/')
        if normalized.startswith('test-plans/'):
            return 'plan'
        if normalized.endswith('.spec.ts') or normalized.endswith('.spec.js'):
            return 'script'
        return 'config'

    @classmethod
    def _artifact_index(cls, workspace, specs_path, before):
        current = cls._git_snapshot(workspace, (specs_path or 'tests', 'test-plans'))
        specs = Path(specs_path or 'tests').as_posix().rstrip('/') + '/'
        artifacts = []
        for path, info in current.items():
            normalized = path.replace('\\', '/')
            if not (normalized.startswith('test-plans/') or normalized.startswith(specs) or normalized.startswith('playwright.config.')):
                continue
            old_hash = before.get(path, {}).get('hash')
            if old_hash == info.get('hash'):
                continue
            artifacts.append({
                'path': normalized,
                'kind': cls._artifact_kind(normalized),
                'status': info.get('status') or '??',
                'sha256': info.get('hash')
            })
        return sorted(artifacts, key=lambda item: item['path'])

    @staticmethod
    def _git_status(workspace):
        result = subprocess.run(
            ['git', '-C', str(workspace), 'status', '--porcelain', '--untracked-files=all'],
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20
        )
        current = {}
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            status = line[:2].strip() or '??'
            path = line[3:].strip().replace('\\', '/')
            current[path] = status
        return current

    def _archive(self, workspace, files, generation_id):
        export_folder = Path(self.app_config['EXPORT_FOLDER'])
        export_folder.mkdir(parents=True, exist_ok=True)
        archive_path = export_folder / f'automation_{generation_id}.zip'
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for item in files:
                relative = Path(item['path'])
                target = (workspace / relative).resolve()
                if target != workspace and workspace not in target.parents:
                    continue
                if target.is_file():
                    archive.write(target, relative.as_posix())
        return archive_path

    @staticmethod
    def _redact_output(output):
        return redact_sensitive_text(output)[-200000:]

    @staticmethod
    def redact_sensitive_text(value):
        return redact_sensitive_text(value)

    @staticmethod
    def _file_scope(files, specs_path):
        specs = Path(specs_path or 'tests').as_posix().rstrip('/') + '/'
        allowed_root_files = {'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock'}
        violations = []
        for item in files:
            path = item['path'].replace('\\', '/')
            allowed = (
                path.startswith(specs)
                or path.startswith('test-plans/')
                or path.startswith('.autocase/')
                or path in allowed_root_files
                or path.startswith('playwright.config.')
            )
            if not allowed:
                violations.append(path)
        return violations

    @staticmethod
    def _overwrite_violations(files, before, overwrite_policy):
        if overwrite_policy != 'reject':
            return []
        return [item['path'] for item in files if item['path'] in before]

    @staticmethod
    def commit_files(workspace, paths, message):
        if not paths:
            raise ValueError('No files to commit')
        result = subprocess.run(
            ['git', '-C', str(workspace), 'status', '--porcelain', '--', *paths],
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20
        )
        if not result.stdout.strip():
            raise ValueError('No changes remain for the selected files')
        subprocess.run(
            ['git', '-C', str(workspace), 'add', '--', *paths],
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20
        )
        subprocess.run(
            ['git', '-C', str(workspace), 'commit', '--only', '-m', message, '--', *paths],
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=60
        )
        commit = subprocess.run(
            ['git', '-C', str(workspace), 'rev-parse', 'HEAD'],
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20
        )
        return commit.stdout.strip()

    def run(self, workspace, manifest, generation_id, model=None, timeout=3600, event_callback=None, process_callback=None):
        active_run = self._register_active_run(generation_id)
        lock = None
        file_lock = None
        process = None
        serve_process = None
        sse_stop = None
        sse_thread = None
        serve_stderr_thread = None
        base_url = None
        preserve_file_lock = False
        interrupted = False
        timed_out = False
        stalled = False
        stall_timeout = 180  # 运行中停滞阈值：busy 状态下超过该秒数无任何新事件即判定卡死，提前中止
        run_started = time.monotonic()
        current_stage = 'orchestrator'
        output_lines = deque(maxlen=2000)
        binary = self.app_config.get('OPENCODE_BIN')
        map_state = {'session_stage': {}, 'current_stage': 'orchestrator', 'seen': set(), 'healer_sessions': set()}
        driver = {'sid': None, 'busy': False, 'done': threading.Event(), 'fatal': None,
                  'last_activity': time.monotonic()}

        def emit(stage, event_type, message):
            if event_callback:
                try:
                    event_callback({
                        'event_type': event_type,
                        'stage': stage,
                        'message': self._redact_output(str(message))[:4000]
                    }, [])
                except Exception:
                    pass

        def live_stage():
            return map_state.get('current_stage') or 'orchestrator'

        def on_bus_event(event):
            driver['last_activity'] = time.monotonic()
            try:
                self._handle_bus_side_effects(event, driver, base_url)
            except Exception:
                pass
            mapped = self._map_bus_event(event, map_state)
            if mapped:
                emit(mapped[0], mapped[1], mapped[2])

        def serve_sink(line):
            output_lines.append(line)
            emit(live_stage(), 'server', line)

        try:
            lock = self._workspace_lock(workspace)
            file_lock = self._acquire_file_lock(workspace)
            extra_roots = (manifest.get('specs_path') or 'tests', 'test-plans')
            before = self._git_snapshot(workspace, extra_roots)
            task_dir = workspace / '.autocase'
            task_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = task_dir / f'automation-request-{generation_id}.json'
            cancel_path = self._cancel_path(generation_id)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            prompt = (
                f'Read exactly .autocase/{manifest_path.name} and execute the complete AutoCase Playwright workflow. '
                'Treat the manifest as the source of truth. Do not ask the user questions. '
                'Return a concise final summary with stages, files, test results, warnings, and healing attempts.'
            )
            config_path = self._config_path()
            emit('orchestrator', 'started', f'OpenCode workflow started for {workspace.name}.')

            serve_host = self._serve_host()
            configured_port = int(self.app_config.get('OPENCODE_SERVE_PORT') or 0)
            port = configured_port or self._free_port()
            base_url = f'http://{serve_host}:{port}'
            emit('orchestrator', 'server', f'Starting OpenCode server on {serve_host}:{port} for real-time subagent logs ...')
            serve_process = self._start_serve(workspace, config_path, port)
            self._set_active_process(generation_id, serve_process)
            file_lock.write_text(f'opencode:{serve_process.pid}', encoding='ascii')
            serve_stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(serve_process.stderr, serve_sink), daemon=True
            )
            serve_stderr_thread.start()
            health = self._wait_serve_healthy(
                base_url, serve_process, self.app_config.get('OPENCODE_SERVE_STARTUP_TIMEOUT', 60)
            )
            emit('orchestrator', 'server_ready', f"OpenCode server ready (version {health.get('version', 'unknown')}).")
            if process_callback:
                process_callback(serve_process.pid)

            sse_stop = threading.Event()
            sse_thread = threading.Thread(target=self._consume_sse, args=(base_url, sse_stop, on_bus_event), daemon=True)
            sse_thread.start()

            model_ref = self._split_model(model)
            return_code = 0
            try:
                session_body = {
                    'title': f'AutoCase {generation_id}',
                    'agent': 'playwright-test-orchestrator',
                }
                if model_ref:
                    session_body['model'] = model_ref
                try:
                    create = requests.post(f'{base_url}/session', json=session_body, timeout=30)
                except requests.RequestException as exc:
                    raise RuntimeError(f'Failed to create OpenCode orchestrator session: {exc}')
                if create.status_code not in (200, 201):
                    raise RuntimeError(
                        f'OpenCode session creation failed ({create.status_code}): {create.text[:500]}'
                    )
                try:
                    session_payload = create.json()
                except ValueError:
                    session_payload = {}
                session_id = session_payload.get('id') if isinstance(session_payload, dict) else None
                if not session_id:
                    raise RuntimeError('OpenCode session creation returned no session id')
                driver['sid'] = session_id
                map_state['session_stage'][session_id] = 'orchestrator'
                emit('orchestrator', 'process_started',
                     f'OpenCode orchestrator session {session_id} created on server {base_url}.')

                prompt_body = {'parts': [{'type': 'text', 'text': prompt}]}
                if model_ref:
                    prompt_body['model'] = {'providerID': model_ref['providerID'], 'modelID': model_ref['id']}
                try:
                    send = requests.post(
                        f'{base_url}/session/{session_id}/prompt_async', json=prompt_body, timeout=60
                    )
                except requests.RequestException as exc:
                    raise RuntimeError(f'Failed to dispatch orchestrator prompt: {exc}')
                if send.status_code not in (200, 202, 204):
                    raise RuntimeError(
                        f'OpenCode prompt dispatch failed ({send.status_code}): {send.text[:500]}'
                    )
                emit('orchestrator', 'server',
                     'Orchestrator prompt dispatched; streaming agent activity from the server event bus.')

                deadline = time.monotonic() + timeout
                busy_deadline = time.monotonic() + 180
                while not driver['done'].is_set():
                    if active_run['cancel_event'].is_set() or cancel_path.exists():
                        interrupted = True
                        emit(live_stage(), 'interrupted', 'OpenCode workflow cancellation requested by the user.')
                        self._abort_sessions(base_url, map_state['session_stage'])
                        break
                    if serve_process.poll() is not None:
                        return_code = serve_process.returncode or 1
                        emit(live_stage(), 'error', f'OpenCode server exited unexpectedly with code {return_code}.')
                        break
                    if time.monotonic() > deadline:
                        timed_out = True
                        emit(live_stage(), 'error', f'OpenCode workflow timed out after {timeout} seconds.')
                        self._abort_sessions(base_url, map_state['session_stage'])
                        break
                    if not driver['busy'] and time.monotonic() > busy_deadline:
                        return_code = 1
                        emit(live_stage(), 'error',
                             'OpenCode orchestrator did not start processing the prompt within 180 seconds.')
                        self._abort_sessions(base_url, map_state['session_stage'])
                        break
                    if driver['busy'] and (time.monotonic() - driver['last_activity']) > stall_timeout:
                        # 运行中停滞检测：某工具/会话卡死（如 browser_snapshot 挂起）导致事件总线长时间静默时，
                        # 不再干等满硬超时，提前中止并明确报“疑似卡死”，让失败更快更清晰。
                        stalled = True
                        emit(live_stage(), 'error',
                             f'OpenCode workflow stalled: no activity for {stall_timeout} seconds; aborting early.')
                        self._abort_sessions(base_url, map_state['session_stage'])
                        break
                    driver['done'].wait(0.3)
                if driver['fatal'] and not interrupted and not timed_out and not stalled and return_code == 0:
                    return_code = 1
                    emit(live_stage(), 'error', f"OpenCode orchestrator reported an error: {driver['fatal']}")
                if return_code == 0 and not interrupted and not timed_out and not stalled:
                    emit(live_stage(), 'process_exit', 'OpenCode orchestrator session completed.')
            finally:
                if sse_stop is not None:
                    sse_stop.set()
                if serve_process is not None and serve_process.poll() is None:
                    # The server command line carries no --dir (it uses cwd), and this is our
                    # own child process, so terminate it without the workspace substring guard.
                    if not self._terminate_process(serve_process, None, binary):
                        preserve_file_lock = True
                        emit(live_stage(), 'error', 'OpenCode server did not stop cleanly; the workspace lock was retained.')
                if sse_thread is not None:
                    sse_thread.join(timeout=3)
                if serve_stderr_thread is not None:
                    serve_stderr_thread.join(timeout=2)
                emit('orchestrator', 'server_stopped', 'OpenCode server stopped.')
                try:
                    manifest_path.unlink()
                except OSError:
                    pass
                try:
                    cancel_path.unlink()
                except OSError:
                    pass

            current_stage = map_state.get('current_stage') or 'orchestrator'
            cleanup_failed = preserve_file_lock
            files = [] if cleanup_failed else self._changed_files(workspace, before, extra_roots)
            violations = ['OpenCode process cleanup failed'] if cleanup_failed else self._file_scope(files, manifest.get('specs_path'))
            if not cleanup_failed:
                violations.extend(self._overwrite_violations(files, before, manifest.get('overwrite_policy', 'reject')))
            violations = sorted(set(violations))
            archive_path = self._archive(workspace, files, generation_id) if files and not violations else None
            output = '\n'.join(output_lines)
            success = return_code == 0 and bool(files) and not violations and not interrupted and not timed_out and not stalled
            if not files:
                output += '\nOpenCode completed without workspace changes.'
            heal_attempts = min(max(len(map_state['healer_sessions']), output.count('playwright-test-healer')), 3)
            return {
                'success': success,
                'return_code': return_code,
                'return_code_hex': self._exit_code_details(return_code)['hex'],
                'return_code_name': self._exit_code_details(return_code)['name'],
                'output': self._redact_output(output),
                'files': files,
                'artifacts': [] if cleanup_failed else self._artifact_index(workspace, manifest.get('specs_path'), before),
                'archive_path': str(archive_path) if archive_path else None,
                'heal_attempts': heal_attempts,
                'scope_violations': violations,
                'secret_violations': [],
                'timed_out': timed_out,
                'timeout_seconds': timeout if timed_out else None,
                'stalled': stalled,
                'idle_timeout_seconds': stall_timeout if stalled else None,
                'interrupted': interrupted,
                'elapsed_seconds': round(time.monotonic() - run_started, 1),
                'stage': current_stage,
                'cleanup_failed': cleanup_failed,
                'process_id': serve_process.pid if serve_process is not None and cleanup_failed else None
            }
        finally:
            # Safety net: ensure the SSE consumer and both child processes are torn down
            # even when startup failed before the inner teardown block ran.
            if sse_stop is not None:
                sse_stop.set()
            if process is not None and process.poll() is None:
                try:
                    self._terminate_process(process, workspace, binary)
                except Exception:
                    pass
            if serve_process is not None and serve_process.poll() is None:
                try:
                    if not self._terminate_process(serve_process, None, binary):
                        preserve_file_lock = True
                except Exception:
                    preserve_file_lock = True
            if file_lock and not preserve_file_lock:
                owner = f'opencode:{serve_process.pid}' if serve_process is not None else f'launcher:{os.getpid()}'
                self._release_file_lock(file_lock, owner)
            if lock:
                lock.release()
            self._unregister_active_run(generation_id)
