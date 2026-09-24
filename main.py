"""YJ-64 base application used for deliberate crash diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label


SERVICE_CLASS = "org.blackmirror.blackmirror.ServiceInternal"
SERVICE_LAUNCH_FLAG = "yj64.internal_agent_launch"


class YJ64FaultInjectionApp(App):
    """Minimal target application with an intentional startup crash."""

    def build(self):
        self.status = Label(
            text=(
                "YJ-64 Fault Injection\n"
                "Diagnostic agent starts first.\n"
                "Intentional crash is scheduled..."
            ),
            halign="left",
            valign="top",
        )
        self.status.bind(
            size=lambda instance, value: setattr(instance, "text_size", value)
        )

        root = BoxLayout(orientation="vertical", padding=24, spacing=16)
        root.add_widget(
            Label(
                text="YJ-64 Fault Injection",
                size_hint_y=None,
                height=80,
            )
        )
        root.add_widget(self.status)

        Clock.schedule_once(self._ensure_service_started, 0.5)
        Clock.schedule_once(self._inject_fault, 5.0)
        Clock.schedule_interval(self._refresh_status, 1.0)
        return root

    def _launched_by_internal_agent(self) -> bool:
        try:
            from jnius import autoclass

            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            intent = activity.getIntent()
            return bool(intent.getBooleanExtra(SERVICE_LAUNCH_FLAG, False))
        except Exception:
            return False

    def _ensure_service_started(self, *_: Any) -> None:
        if self._launched_by_internal_agent():
            self.status.text = (
                "Base application launched by the internal diagnostic agent.\n"
                "Intentional crash scheduled in 5 seconds."
            )
            return

        try:
            from jnius import autoclass

            service_class = autoclass(SERVICE_CLASS)
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            service_class.start(activity, "")
            self.status.text = (
                "Diagnostic agent starting first; waiting for self-diagnostic "
                "and bridge handshake..."
            )
        except Exception as exc:
            self.status.text = (
                "Diagnostic agent start failed: "
                f"{type(exc).__name__}: {exc}"
            )

    def _inject_fault(self, *_: Any) -> None:
        """Deliberately terminate the target process for the diagnostic test."""
        raise RuntimeError(
            "FAULT_INJECTION: deliberate YJ-64 base application crash"
        )

    def _refresh_status(self, *_: Any) -> None:
        report_path = Path(self.user_data_dir) / "diagnostic-agent-status.json"
        if not report_path.exists():
            return

        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        self.status.text = (
            "Embedded diagnostic agent\n"
            f"Bridge: {data.get('bridge_status', 'unknown')}\n"
            f"Self-diagnostic: {data.get('self_diagnostic', 'unknown')}\n"
            f"Base app: {data.get('target_launch', 'unknown')}\n"
            f"Last event: {data.get('event', 'unknown')}"
        )


YJ64FaultInjectionApp().run()
