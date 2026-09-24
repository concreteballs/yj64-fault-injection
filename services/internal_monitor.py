"""Embedded diagnostic agent with target-process fault detection."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib import request

from jnius import autoclass


SCHEMA = "yj64.diagnostic.v1"
SERVICE_LAUNCH_FLAG = "yj64.internal_agent_launch"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "bridge.json"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def write_status(service: Any, payload: dict[str, Any]) -> None:
    path = Path(str(service.getFilesDir())) / "diagnostic-agent-status.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def spool_path(service: Any, filename: str) -> Path:
    return Path(str(service.getFilesDir())) / filename


def make_report(agent_id: str, event: str, **data: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "agent": agent_id,
        "event": event,
        "timestamp_ms": int(time.time() * 1000),
        "report_id": uuid.uuid4().hex,
        "data": data,
    }


def append_spool(service: Any, path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, sort_keys=True) + "\n")


def send_bridge(
    host: str,
    port: int,
    token: str,
    timeout: float,
    report: dict[str, Any],
) -> bool:
    envelope = {"token": token, "report": report}
    payload = (json.dumps(envelope, sort_keys=True) + "\n").encode("utf-8")

    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.sendall(payload)
            connection.settimeout(timeout)
            response = connection.recv(4096).decode("utf-8", errors="replace").strip()
            if not response:
                return False
            ack = json.loads(response)
            return (
                ack.get("ok") is True
                and ack.get("report_id") == report["report_id"]
            )
    except (OSError, ValueError, TypeError):
        return False


def send_with_retry(config: dict[str, Any], report: dict[str, Any]) -> bool:
    bridge = config["bridge"]
    attempts = int(bridge["retry_count"])
    delay = float(bridge["retry_delay_seconds"])

    for attempt in range(max(1, attempts)):
        if send_bridge(
            str(bridge["host"]),
            int(bridge["port"]),
            str(bridge["token"]),
            float(bridge["connect_timeout_seconds"]),
            report,
        ):
            return True
        if attempt + 1 < attempts:
            time.sleep(delay)

    return False


def upload_https(endpoint: str, report: dict[str, Any], timeout: float = 5.0) -> bool:
    """Upload a report when a real HTTPS endpoint is configured."""
    if not endpoint:
        return False

    try:
        body = json.dumps(report).encode("utf-8")
        req = request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except (OSError, ValueError, TypeError):
        return False


def self_diagnostic(config: dict[str, Any], service: Any) -> dict[str, Any]:
    """Run deterministic startup checks before handing control to the base app."""
    checks: dict[str, Any] = {}

    try:
        config["agent_id"]
        config["target_package"]
        config["bridge"]["host"]
        config["bridge"]["port"]
        checks["configuration"] = "ok"
    except (KeyError, TypeError):
        checks["configuration"] = "invalid"

    try:
        probe = Path(str(service.getFilesDir())) / ".diagnostic_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["storage"] = "ok"
    except OSError:
        checks["storage"] = "unavailable"

    try:
        package_manager = service.getPackageManager()
        visible = (
            package_manager.getLaunchIntentForPackage(str(config["target_package"]))
            is not None
        )
        checks["target_visibility"] = "visible" if visible else "not_visible"
    except Exception as exc:
        checks["target_visibility"] = f"probe_error:{type(exc).__name__}"

    checks["process"] = "ok"
    return checks


def launch_target(package_name: str) -> tuple[bool, str]:
    """Hand control to the base application's main activity."""
    try:
        context = autoclass("org.kivy.android.PythonService").mService
        package_manager = context.getPackageManager()
        intent = package_manager.getLaunchIntentForPackage(package_name)

        if intent is None:
            return False, "target_package_not_installed"

        Intent = autoclass("android.content.Intent")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        intent.putExtra(SERVICE_LAUNCH_FLAG, True)
        context.startActivity(intent)
        return True, "launched"
    except Exception as exc:
        return False, f"launch_error:{type(exc).__name__}"


def is_target_main_process_running(service: Any, package_name: str) -> bool:
    """Return whether the target's main Android process is currently visible."""
    try:
        activity_manager = service.getSystemService("activity")
        processes = activity_manager.getRunningAppProcesses()
        if processes is None:
            return False

        for process in processes:
            if str(process.processName) == package_name:
                return True
        return False
    except Exception:
        return True


def send_report_or_spool(
    config: dict[str, Any],
    service: Any,
    spool: Path,
    report: dict[str, Any],
) -> bool:
    """Prefer the external bridge and persist the report when the bridge is unavailable."""
    bridge_ok = send_with_retry(config, report)
    if not bridge_ok:
        append_spool(service, spool, report)

    endpoint = str(config["report"].get("https_endpoint", ""))
    if endpoint:
        upload_https(endpoint, report)

    return bridge_ok


def run() -> None:
    PythonService = autoclass("org.kivy.android.PythonService")
    service = PythonService.mService
    service.setAutoRestartService(True)

    config = load_config()
    agent_id = str(config["agent_id"])
    target_package = str(config["target_package"])
    spool = spool_path(service, str(config["report"]["spool_filename"]))

    checks = self_diagnostic(config, service)
    self_report = make_report(
        agent_id,
        "self_diagnostic",
        checks=checks,
        target_package=target_package,
    )
    self_bridge_ok = send_report_or_spool(config, service, spool, self_report)

    startup = make_report(
        agent_id,
        "internal_startup",
        pid=os.getpid(),
        python_version=sys.version.split()[0],
        target_package=target_package,
        cwd=os.getcwd(),
    )
    startup_bridge_ok = send_report_or_spool(config, service, spool, startup)

    launch_ok, launch_reason = launch_target(target_package)
    launch_report = make_report(
        agent_id,
        "target_launch_result",
        target_package=target_package,
        success=launch_ok,
        reason=launch_reason,
        bridge_received_self_diagnostic=self_bridge_ok,
        bridge_received_startup=startup_bridge_ok,
    )
    launch_bridge_ok = send_report_or_spool(
        config, service, spool, launch_report
    )

    write_status(
        service,
        {
            "agent": agent_id,
            "event": "target_launch_result",
            "bridge_status": (
                "connected"
                if self_bridge_ok or startup_bridge_ok or launch_bridge_ok
                else "spooled"
            ),
            "self_diagnostic": (
                "ok"
                if all(value in {"ok", "visible"} for value in checks.values())
                else json.dumps(checks, sort_keys=True)
            ),
            "target_launch": launch_reason,
            "target_success": launch_ok,
            "launch_report_sent": launch_bridge_ok,
        },
    )

    if launch_ok:
        time.sleep(5)
        while True:
            if not is_target_main_process_running(service, target_package):
                crash_report = make_report(
                    agent_id,
                    "target_process_terminated",
                    target_package=target_package,
                    reason="target_main_process_not_running",
                    detection="activity_manager_poll",
                )
                crash_report_sent = send_report_or_spool(
                    config, service, spool, crash_report
                )
                write_status(
                    service,
                    {
                        "agent": agent_id,
                        "event": "target_process_terminated",
                        "bridge_status": (
                            "connected" if crash_report_sent else "spooled"
                        ),
                        "self_diagnostic": "completed",
                        "target_launch": launch_reason,
                        "target_success": True,
                        "crash_report_sent": crash_report_sent,
                    },
                )
                break
            time.sleep(2)

    while True:
        time.sleep(30)


run()
