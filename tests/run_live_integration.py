from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime
import json
import os
import pathlib
import sys
import threading
import time
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from wsgiref.simple_server import make_server

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from todoist_proxy.app import create_app
from todoist_proxy.models import token_fingerprint


@dataclass
class CaseResult:
    name: str
    ok: bool
    status_code: int | None
    details: str


class LiveIntegrationRunner:
    def __init__(self, token: str, report_file: str) -> None:
        self.token = token.strip()
        self.report_file = report_file
        self.results: list[CaseResult] = []
        self.server = None
        self.server_thread = None
        self.base_url = ""
        self.created_projects: list[str] = []
        self.created_sections: list[str] = []
        self.created_tasks: list[str] = []
        self.main_project_id: str | None = None
        self.main_section_id: str | None = None
        self.main_task_id: str | None = None
        self.move_source_project_id: str | None = None
        self.move_destination_project_id: str | None = None
        self.move_destination_section_id: str | None = None
        self.today = date.today().isoformat()
        self.run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        self.rate_state_file = f"/tmp/todoist_proxy_rate_limit_{self.run_id}.json"

    def run(self) -> int:
        if not self.token:
            raise RuntimeError("TODOIST token is empty")

        self._start_server()
        try:
            self._run_suite()
        finally:
            self._cleanup()
            self._stop_server()
            self._write_report()

        return 0 if all(item.ok for item in self.results) else 1

    def _run_suite(self) -> None:
        methods_payload = self._assert_request("GET /methods", "GET", "/methods", expected_status=200)
        self._assert_methods_catalog(methods_payload)
        self._seed_resources()
        self._test_token_isolation_and_auth_headers()
        self._test_read_methods_all_modes()
        self._test_regression_cases()
        self._test_write_methods_all_modes()

    def _test_regression_cases(self) -> None:
        # Regression: local TOON pagination must return [] for out-of-range page.
        paged = self._assert_request(
            "REGRESSION TOON task.list_by_project page overflow",
            "POST",
            "/toon/task.list_by_project",
            payload={
                "project_id": self.main_project_id,
                "page": 2,
                "size": 50,
            },
            expected_status=200,
        )
        if isinstance(paged, dict) and isinstance(paged.get("d"), list) and len(paged["d"]) == 0:
            self._record(
                "ASSERT regression page overflow returns empty list",
                True,
                200,
                "page=2,size=50 returned empty list",
            )
        else:
            self._record(
                "ASSERT regression page overflow returns empty list",
                False,
                None,
                f"expected empty toon list, got payload={paged!r}",
            )

        due_task_name = f"IT-PROXY {self.run_id} regression due include"
        no_due_task_name = f"IT-PROXY {self.run_id} regression no due exclude"

        due_task_payload = self._assert_request(
            "REGRESSION SEED task.create with date alias",
            "POST",
            "/raw/task.create",
            payload={
                "name": due_task_name,
                "description": "regression due task",
                "date": self.today,
                "projectId": self.main_project_id,
            },
            expected_status=200,
        )
        due_task_id = self._extract_id("REGRESSION SEED task.create with date alias", due_task_payload)
        self.created_tasks.append(due_task_id)

        no_due_task_payload = self._assert_request(
            "REGRESSION SEED task.create without due date",
            "POST",
            "/raw/task.create",
            payload={
                "name": no_due_task_name,
                "description": "regression no due task",
                "projectId": self.main_project_id,
            },
            expected_status=200,
        )
        no_due_task_id = self._extract_id("REGRESSION SEED task.create without due date", no_due_task_payload)
        self.created_tasks.append(no_due_task_id)

        due_task_get = self._assert_request(
            "REGRESSION VERIFY task.get date alias",
            "POST",
            "/raw/task.get",
            payload={"task_id": due_task_id},
            expected_status=200,
        )

        description_value = due_task_get.get("description") if isinstance(due_task_get, dict) else None
        if description_value == "regression due task":
            self._record(
                "ASSERT regression task.create keeps description",
                True,
                200,
                "raw task.get returned expected description",
            )
        else:
            self._record(
                "ASSERT regression task.create keeps description",
                False,
                None,
                f"unexpected description value: {description_value!r}",
            )

        due_field = due_task_get.get("due") if isinstance(due_task_get, dict) else None
        due_date_value = due_field.get("date") if isinstance(due_field, dict) else None
        if due_date_value == self.today:
            self._record(
                "ASSERT regression task.create maps date to due date",
                True,
                200,
                f"due.date matches today ({self.today})",
            )
        else:
            self._record(
                "ASSERT regression task.create maps date to due date",
                False,
                None,
                f"expected due.date={self.today!r}, got {due_date_value!r}",
            )

        toon_by_date = self._assert_request(
            "REGRESSION TOON task.list_by_date filter check",
            "POST",
            "/toon/task.list_by_date",
            payload={"date": self.today},
            expected_status=200,
        )
        toon_names = self._extract_toon_task_names(toon_by_date)
        if due_task_name in toon_names:
            self._record(
                "ASSERT regression list_by_date includes due task",
                True,
                200,
                "due task name found in TOON list_by_date response",
            )
        else:
            self._record(
                "ASSERT regression list_by_date includes due task",
                False,
                None,
                f"due task name not found; names={sorted(toon_names)}",
            )
        if no_due_task_name not in toon_names:
            self._record(
                "ASSERT regression list_by_date excludes no-due task",
                True,
                200,
                "task without due date is absent in TOON list_by_date response",
            )
        else:
            self._record(
                "ASSERT regression list_by_date excludes no-due task",
                False,
                None,
                f"task without due date leaked into list_by_date; names={sorted(toon_names)}",
            )

        mass_due_date = "2099-12-31"
        mass_prefix = f"IT-PROXY {self.run_id} regression 50plus"
        expected_mass_names = {f"{mass_prefix} #{index:02d}" for index in range(1, 52)}
        mass_seed_error: str | None = None
        for index in range(1, 52):
            task_name = f"{mass_prefix} #{index:02d}"
            status_code, response_payload, response_text = self._http_request(
                method="POST",
                path="/raw/task.create",
                payload={
                    "name": task_name,
                    "description": "regression pagination >50",
                    "projectId": self.main_project_id,
                    "date": mass_due_date,
                },
            )
            if status_code != 200:
                mass_seed_error = (
                    f"seed failed for index={index}: status={status_code} "
                    f"payload={response_payload!r} raw={response_text!r}"
                )
                break
            if not isinstance(response_payload, dict) or response_payload.get("id") is None:
                mass_seed_error = f"seed failed for index={index}: no id in payload={response_payload!r}"
                break
            self.created_tasks.append(str(response_payload["id"]))

        if mass_seed_error is None:
            self._record(
                "REGRESSION SEED 50+ tasks for list_by_date pagination",
                True,
                200,
                f"created 51 tasks with date={mass_due_date}",
            )
        else:
            self._record(
                "REGRESSION SEED 50+ tasks for list_by_date pagination",
                False,
                None,
                mass_seed_error,
            )

        found_mass_names: set[str] = set()
        cursor: str | None = None
        pages_seen = 0
        saw_next_cursor = False
        max_pages = 20
        while mass_seed_error is None and pages_seen < max_pages:
            request_payload: dict[str, Any] = {
                "date": mass_due_date,
                "limit": 50,
            }
            if cursor:
                request_payload["cursor"] = cursor

            page_payload = self._assert_request(
                f"REGRESSION TOON task.list_by_date 50+ page {pages_seen + 1}",
                "POST",
                "/toon/task.list_by_date",
                payload=request_payload,
                expected_status=200,
            )
            pages_seen += 1
            for name in self._extract_toon_task_names(page_payload):
                if name in expected_mass_names:
                    found_mass_names.add(name)

            next_cursor = self._extract_next_cursor(page_payload)
            cursor = next_cursor
            if cursor:
                saw_next_cursor = True
                continue
            break

        if mass_seed_error is None:
            if saw_next_cursor:
                self._record(
                    "ASSERT regression list_by_date >50 requires pagination",
                    True,
                    200,
                    f"next_cursor observed; pages_seen={pages_seen}",
                )
            else:
                self._record(
                    "ASSERT regression list_by_date >50 requires pagination",
                    False,
                    None,
                    f"next_cursor not observed for 51-task batch; pages_seen={pages_seen}",
                )

            if found_mass_names == expected_mass_names:
                self._record(
                    "ASSERT regression list_by_date >50 collects all seeded tasks",
                    True,
                    200,
                    "all 51 seeded tasks were found via cursor pagination",
                )
            else:
                missing = sorted(expected_mass_names - found_mass_names)
                self._record(
                    "ASSERT regression list_by_date >50 collects all seeded tasks",
                    False,
                    None,
                    f"found={len(found_mass_names)} expected=51 missing={missing[:5]}",
                )

            if pages_seen >= max_pages and cursor is not None:
                self._record(
                    "ASSERT regression list_by_date >50 page cap",
                    False,
                    None,
                    f"reached max pages ({max_pages}) before cursor exhaustion",
                )

        paged_payload = {
            "project_id": self.main_project_id,
            "limit": 1,
        }
        raw_page_1 = self._assert_request(
            "REGRESSION RAW task.list_by_project pagination baseline",
            "POST",
            "/raw/task.list_by_project",
            payload=paged_payload,
            expected_status=200,
        )
        toon_page_1 = self._assert_request(
            "REGRESSION TOON task.list_by_project pagination baseline",
            "POST",
            "/toon/task.list_by_project",
            payload=paged_payload,
            expected_status=200,
        )

        raw_next_cursor = self._extract_next_cursor(raw_page_1)
        toon_next_cursor = self._extract_next_cursor(toon_page_1)
        if raw_next_cursor:
            if toon_next_cursor == raw_next_cursor:
                self._record(
                    "ASSERT regression next_cursor passthrough to TOON",
                    True,
                    200,
                    "toon next_cursor matches raw next_cursor",
                )
            else:
                self._record(
                    "ASSERT regression next_cursor passthrough to TOON",
                    False,
                    None,
                    f"raw next_cursor={raw_next_cursor!r}, toon next_cursor={toon_next_cursor!r}",
                )

            toon_page_2 = self._assert_request(
                "REGRESSION TOON task.list_by_project pagination cursor page2",
                "POST",
                "/toon/task.list_by_project",
                payload={
                    "project_id": self.main_project_id,
                    "limit": 1,
                    "cursor": toon_next_cursor,
                },
                expected_status=200,
            )
            if isinstance(toon_page_2, dict) and isinstance(toon_page_2.get("d"), list):
                self._record(
                    "ASSERT regression TOON cursor page2 returns list",
                    True,
                    200,
                    "page2 response keeps TOON list shape",
                )
            else:
                self._record(
                    "ASSERT regression TOON cursor page2 returns list",
                    False,
                    None,
                    f"unexpected page2 payload shape: {toon_page_2!r}",
                )
        else:
            self._record(
                "ASSERT regression next_cursor passthrough to TOON",
                True,
                200,
                "skipped strict assertion: upstream raw response has no next_cursor",
            )

        today_payload = self._assert_request(
            "REGRESSION GET /tasks/today",
            "GET",
            "/tasks/today?limit=100",
            expected_status=200,
        )
        self._assert_toon_envelope("REGRESSION GET /tasks/today", today_payload)
        today_names = self._extract_toon_task_names(today_payload)
        if due_task_name in today_names:
            self._record(
                "ASSERT regression /tasks/today includes due task",
                True,
                200,
                "due task name found in /tasks/today response",
            )
        else:
            self._record(
                "ASSERT regression /tasks/today includes due task",
                False,
                None,
                f"due task name not found in /tasks/today; names={sorted(today_names)}",
            )
        if no_due_task_name not in today_names:
            self._record(
                "ASSERT regression /tasks/today excludes no-due task",
                True,
                200,
                "task without due date is absent in /tasks/today response",
            )
        else:
            self._record(
                "ASSERT regression /tasks/today excludes no-due task",
                False,
                None,
                f"task without due date leaked into /tasks/today; names={sorted(today_names)}",
            )

    def _test_token_isolation_and_auth_headers(self) -> None:
        self._assert_request(
            "TOKEN valid list",
            "POST",
            "/raw/task.list",
            payload={},
            expected_status=200,
        )
        invalid_token = f"invalid-{self.run_id}"
        self._assert_request(
            "TOKEN invalid list",
            "POST",
            "/raw/task.list",
            payload={},
            expected_status=401,
            token_override=invalid_token,
        )
        self._assert_request(
            "TOKEN valid list after invalid",
            "POST",
            "/raw/task.list",
            payload={},
            expected_status=200,
        )
        self._assert_request(
            "TOKEN alt header list",
            "POST",
            "/raw/task.list",
            payload={},
            expected_status=200,
            use_alt_header=True,
        )

        scopes = self._read_rate_limit_scopes()
        expected_scopes = {
            token_fingerprint(self.token),
            token_fingerprint(invalid_token),
        }
        if expected_scopes.issubset(scopes):
            self._record(
                "ASSERT rate-limit scopes by token",
                True,
                200,
                f"found scopes: {sorted(scopes)}",
            )
            return
        self._record(
            "ASSERT rate-limit scopes by token",
            False,
            None,
            f"missing scopes: {sorted(expected_scopes - scopes)}; got={sorted(scopes)}",
        )

    def _test_read_methods_all_modes(self) -> None:
        read_cases = [
            ("task.list", {}),
            ("task.list_by_project", {"project_id": self.main_project_id}),
            ("task.list_by_date", {"date": self.today}),
            ("task.get", {"task_id": self.main_task_id}),
            ("project.list", {}),
            ("project.get", {"project_id": self.main_project_id}),
            ("section.list_by_project", {"project_id": self.main_project_id}),
            ("section.get", {"task_group_id": self.main_section_id}),
        ]

        for method_name, payload in read_cases:
            normalized_payload = self._normalize_payload(payload)
            self._assert_request(
                f"RAW {method_name}",
                "POST",
                f"/raw/{method_name}",
                payload=normalized_payload,
                expected_status=200,
            )
            toon_payload = self._assert_request(
                f"TOON {method_name}",
                "POST",
                f"/toon/{method_name}",
                payload=normalized_payload,
                expected_status=200,
            )
            self._assert_toon_envelope(f"TOON {method_name}", toon_payload)
            if method_name == "task.get":
                self._assert_toon_task_section_ref("TOON task.get", toon_payload, self.main_section_id)
            default_payload = self._assert_request(
                f"DEFAULT {method_name}",
                "POST",
                f"/{method_name}",
                payload=normalized_payload,
                expected_status=200,
            )
            self._assert_toon_envelope(f"DEFAULT {method_name}", default_payload)
            if method_name == "task.get":
                self._assert_toon_task_section_ref("DEFAULT task.get", default_payload, self.main_section_id)

    def _test_write_methods_all_modes(self) -> None:
        # Task update on main task
        self._assert_request(
            "RAW task.update",
            "POST",
            "/raw/task.update",
            payload={
                "task_id": self.main_task_id,
                "name": f"IT-PROXY {self.run_id} main task raw",
                "description": "raw update",
            },
            expected_status=200,
        )
        task_get_after_raw = self._assert_request(
            "VERIFY task.get after raw update",
            "POST",
            "/raw/task.get",
            payload={"task_id": self.main_task_id},
            expected_status=200,
        )
        self._assert_field_contains(
            "VERIFY task.get after raw update",
            task_get_after_raw,
            field="content",
            needle="main task raw",
        )

        toon_task_update = self._assert_request(
            "TOON task.update",
            "POST",
            "/toon/task.update",
            payload={
                "task_id": self.main_task_id,
                "name": f"IT-PROXY {self.run_id} main task toon",
            },
            expected_status=200,
        )
        self._assert_toon_envelope("TOON task.update", toon_task_update)

        default_task_update = self._assert_request(
            "DEFAULT task.update",
            "POST",
            "/task.update",
            payload={
                "task_id": self.main_task_id,
                "name": f"IT-PROXY {self.run_id} main task default",
            },
            expected_status=200,
        )
        self._assert_toon_envelope("DEFAULT task.update", default_task_update)

        # Task move (project + section) across all modes with factual verification.
        self._test_task_move_mode(mode_name="RAW", move_path="/raw/task.move", expect_toon=False)
        self._test_task_move_mode(mode_name="TOON", move_path="/toon/task.move", expect_toon=True)
        self._test_task_move_mode(mode_name="DEFAULT", move_path="/task.move", expect_toon=True)
        self._cleanup_task_move_targets()

        # Project update on main project
        self._assert_request(
            "RAW project.update",
            "POST",
            "/raw/project.update",
            payload={
                "project_id": self.main_project_id,
                "name": f"IT-PROXY {self.run_id} main project raw",
            },
            expected_status=200,
        )
        toon_project_update = self._assert_request(
            "TOON project.update",
            "POST",
            "/toon/project.update",
            payload={
                "project_id": self.main_project_id,
                "name": f"IT-PROXY {self.run_id} main project toon",
            },
            expected_status=200,
        )
        self._assert_toon_envelope("TOON project.update", toon_project_update)
        default_project_update = self._assert_request(
            "DEFAULT project.update",
            "POST",
            "/project.update",
            payload={
                "project_id": self.main_project_id,
                "name": f"IT-PROXY {self.run_id} main project default",
            },
            expected_status=200,
        )
        self._assert_toon_envelope("DEFAULT project.update", default_project_update)

        # Section update on main section
        self._assert_request(
            "RAW section.update",
            "POST",
            "/raw/section.update",
            payload={
                "task_group_id": self.main_section_id,
                "name": f"IT-PROXY {self.run_id} main section raw",
            },
            expected_status=200,
        )
        toon_section_update = self._assert_request(
            "TOON section.update",
            "POST",
            "/toon/section.update",
            payload={
                "task_group_id": self.main_section_id,
                "name": f"IT-PROXY {self.run_id} main section toon",
            },
            expected_status=200,
        )
        self._assert_toon_envelope("TOON section.update", toon_section_update)
        default_section_update = self._assert_request(
            "DEFAULT section.update",
            "POST",
            "/section.update",
            payload={
                "task_group_id": self.main_section_id,
                "name": f"IT-PROXY {self.run_id} main section default",
            },
            expected_status=200,
        )
        self._assert_toon_envelope("DEFAULT section.update", default_section_update)

        # Checklist create/update/delete coverage.
        check_raw_seed = self._assert_request(
            "RAW checklist.create",
            "POST",
            "/raw/checklist.create",
            payload={
                "task_id": self.main_task_id,
                "name": f"IT-PROXY {self.run_id} checklist raw",
                "isCompleted": False,
            },
            expected_status=200,
        )
        checklist_raw_id = self._extract_id("RAW checklist.create", check_raw_seed)
        self.created_tasks.append(checklist_raw_id)

        check_toon_seed = self._assert_request(
            "SEED checklist.create for TOON flow",
            "POST",
            "/raw/checklist.create",
            payload={
                "task_id": self.main_task_id,
                "name": f"IT-PROXY {self.run_id} checklist toon seed",
                "isCompleted": False,
            },
            expected_status=200,
        )
        checklist_toon_id = self._extract_id("SEED checklist.create for TOON flow", check_toon_seed)
        self.created_tasks.append(checklist_toon_id)

        check_default_seed = self._assert_request(
            "SEED checklist.create for DEFAULT flow",
            "POST",
            "/raw/checklist.create",
            payload={
                "task_id": self.main_task_id,
                "name": f"IT-PROXY {self.run_id} checklist default seed",
                "isCompleted": False,
            },
            expected_status=200,
        )
        checklist_default_id = self._extract_id("SEED checklist.create for DEFAULT flow", check_default_seed)
        self.created_tasks.append(checklist_default_id)

        toon_check_create_smoke = self._assert_request(
            "TOON checklist.create (smoke)",
            "POST",
            "/toon/checklist.create",
            payload={
                "task_id": self.main_task_id,
                "name": f"IT-PROXY {self.run_id} checklist toon smoke",
                "isCompleted": False,
            },
            expected_status=200,
        )
        self._assert_toon_envelope("TOON checklist.create (smoke)", toon_check_create_smoke)
        toon_check_smoke_id = self._find_task_id_by_content(
            content=f"IT-PROXY {self.run_id} checklist toon smoke",
            project_id=self.main_project_id,
            parent_id=self.main_task_id,
        )
        if toon_check_smoke_id:
            self.created_tasks.append(toon_check_smoke_id)

        default_check_create_smoke = self._assert_request(
            "DEFAULT checklist.create (smoke)",
            "POST",
            "/checklist.create",
            payload={
                "task_id": self.main_task_id,
                "name": f"IT-PROXY {self.run_id} checklist default smoke",
                "isCompleted": False,
            },
            expected_status=200,
        )
        self._assert_toon_envelope("DEFAULT checklist.create (smoke)", default_check_create_smoke)
        default_check_smoke_id = self._find_task_id_by_content(
            content=f"IT-PROXY {self.run_id} checklist default smoke",
            project_id=self.main_project_id,
            parent_id=self.main_task_id,
        )
        if default_check_smoke_id:
            self.created_tasks.append(default_check_smoke_id)

        self._assert_request(
            "RAW checklist.update",
            "POST",
            "/raw/checklist.update",
            payload={
                "task_id": self.main_task_id,
                "checklist_item_id": checklist_raw_id,
                "name": f"IT-PROXY {self.run_id} checklist raw updated",
                "isCompleted": True,
            },
            expected_status=200,
        )
        toon_check_update = self._assert_request(
            "TOON checklist.update",
            "POST",
            "/toon/checklist.update",
            payload={
                "task_id": self.main_task_id,
                "checklist_item_id": checklist_toon_id,
                "name": f"IT-PROXY {self.run_id} checklist toon updated",
                "isCompleted": True,
            },
            expected_status=200,
        )
        self._assert_toon_envelope("TOON checklist.update", toon_check_update)
        default_check_update = self._assert_request(
            "DEFAULT checklist.update",
            "POST",
            "/checklist.update",
            payload={
                "task_id": self.main_task_id,
                "checklist_item_id": checklist_default_id,
                "name": f"IT-PROXY {self.run_id} checklist default updated",
                "isCompleted": True,
            },
            expected_status=200,
        )
        self._assert_toon_envelope("DEFAULT checklist.update", default_check_update)

        self._assert_request(
            "RAW checklist.delete",
            "POST",
            "/raw/checklist.delete",
            payload={
                "task_id": self.main_task_id,
                "checklist_item_id": checklist_raw_id,
            },
            expected_status=200,
        )
        self.created_tasks = [item for item in self.created_tasks if item != checklist_raw_id]
        toon_check_delete = self._assert_request(
            "TOON checklist.delete",
            "POST",
            "/toon/checklist.delete",
            payload={
                "task_id": self.main_task_id,
                "checklist_item_id": checklist_toon_id,
            },
            expected_status=200,
        )
        self._assert_equal("TOON checklist.delete", toon_check_delete, {"d": {"ok": 1}})
        self.created_tasks = [item for item in self.created_tasks if item != checklist_toon_id]
        default_check_delete = self._assert_request(
            "DEFAULT checklist.delete",
            "POST",
            "/checklist.delete",
            payload={
                "task_id": self.main_task_id,
                "checklist_item_id": checklist_default_id,
            },
            expected_status=200,
        )
        self._assert_equal("DEFAULT checklist.delete", default_check_delete, {"d": {"ok": 1}})
        self.created_tasks = [item for item in self.created_tasks if item != checklist_default_id]

        # Dedicated create/delete matrix for task/project/section methods.
        self._create_update_delete_entity_matrix(
            entity="task",
            create_payload={"name": f"IT-PROXY {self.run_id} matrix task", "projectId": self.main_project_id},
            update_payload_builder=lambda entity_id: {"task_id": entity_id, "name": f"IT-PROXY {self.run_id} matrix task updated"},
            delete_payload_builder=lambda entity_id: {"task_id": entity_id},
            id_field_hint="id",
            track_collection=self.created_tasks,
        )
        try:
            self._create_update_delete_entity_matrix(
                entity="project",
                create_payload={"name": f"IT-PROXY {self.run_id} matrix project"},
                update_payload_builder=lambda entity_id: {"project_id": entity_id, "name": f"IT-PROXY {self.run_id} matrix project updated"},
                delete_payload_builder=lambda entity_id: {"project_id": entity_id},
                id_field_hint="id",
                track_collection=self.created_projects,
            )
        except RuntimeError as exc:
            if "status 403" in str(exc):
                self._record(
                    "SKIP project matrix (quota)",
                    True,
                    403,
                    "project.create returned 403 (likely account project quota); project matrix skipped",
                )
            else:
                raise
        self._create_update_delete_entity_matrix(
            entity="section",
            create_payload={"name": f"IT-PROXY {self.run_id} matrix section", "projectId": self.main_project_id},
            update_payload_builder=lambda entity_id: {"task_group_id": entity_id, "name": f"IT-PROXY {self.run_id} matrix section updated"},
            delete_payload_builder=lambda entity_id: {"task_group_id": entity_id},
            id_field_hint="id",
            track_collection=self.created_sections,
        )

        # Task close coverage.
        close_raw_seed = self._assert_request(
            "SEED task.create for RAW task.close",
            "POST",
            "/raw/task.create",
            payload={"name": f"IT-PROXY {self.run_id} close raw", "projectId": self.main_project_id},
            expected_status=200,
        )
        close_raw_id = self._extract_id("SEED task.create for RAW task.close", close_raw_seed)
        self.created_tasks.append(close_raw_id)
        self._assert_request(
            "RAW task.close",
            "POST",
            "/raw/task.close",
            payload={"task_id": close_raw_id},
            expected_status=200,
        )

        close_toon_seed = self._assert_request(
            "SEED task.create for TOON task.close",
            "POST",
            "/raw/task.create",
            payload={"name": f"IT-PROXY {self.run_id} close toon", "projectId": self.main_project_id},
            expected_status=200,
        )
        close_toon_id = self._extract_id("SEED task.create for TOON task.close", close_toon_seed)
        self.created_tasks.append(close_toon_id)
        toon_task_close = self._assert_request(
            "TOON task.close",
            "POST",
            "/toon/task.close",
            payload={"task_id": close_toon_id},
            expected_status=200,
        )
        self._assert_equal("TOON task.close", toon_task_close, {"d": {"ok": 1}})

        close_default_seed = self._assert_request(
            "SEED task.create for DEFAULT task.close",
            "POST",
            "/raw/task.create",
            payload={"name": f"IT-PROXY {self.run_id} close default", "projectId": self.main_project_id},
            expected_status=200,
        )
        close_default_id = self._extract_id("SEED task.create for DEFAULT task.close", close_default_seed)
        self.created_tasks.append(close_default_id)
        default_task_close = self._assert_request(
            "DEFAULT task.close",
            "POST",
            "/task.close",
            payload={"task_id": close_default_id},
            expected_status=200,
        )
        self._assert_equal("DEFAULT task.close", default_task_close, {"d": {"ok": 1}})

    def _create_update_delete_entity_matrix(
        self,
        *,
        entity: str,
        create_payload: dict[str, Any],
        update_payload_builder: Any,
        delete_payload_builder: Any,
        id_field_hint: str,
        track_collection: list[str],
    ) -> None:
        # raw mode
        raw_created = self._assert_request(
            f"RAW {entity}.create",
            "POST",
            f"/raw/{entity}.create",
            payload=self._payload_with_name_suffix(create_payload, "raw"),
            expected_status=200,
        )
        raw_id = self._extract_id(f"RAW {entity}.create", raw_created, id_field=id_field_hint)
        track_collection.append(raw_id)
        self._assert_request(
            f"RAW {entity}.update",
            "POST",
            f"/raw/{entity}.update",
            payload=update_payload_builder(raw_id),
            expected_status=200,
        )
        self._assert_request(
            f"RAW {entity}.delete",
            "POST",
            f"/raw/{entity}.delete",
            payload=delete_payload_builder(raw_id),
            expected_status=200,
        )
        track_collection[:] = [item for item in track_collection if item != raw_id]

        # toon mode
        toon_seed = self._assert_request(
            f"SEED RAW {entity}.create for TOON flow",
            "POST",
            f"/raw/{entity}.create",
            payload=self._payload_with_name_suffix(create_payload, "toon-seed"),
            expected_status=200,
        )
        toon_create_smoke = self._assert_request(
            f"TOON {entity}.create (smoke)",
            "POST",
            f"/toon/{entity}.create",
            payload=self._payload_with_name_suffix(create_payload, "toon-smoke"),
            expected_status=200,
        )
        self._assert_toon_envelope(f"TOON {entity}.create (smoke)", toon_create_smoke)
        toon_smoke_id = self._extract_toon_entity_id(toon_create_smoke)
        if toon_smoke_id:
            track_collection.append(toon_smoke_id)
        else:
            toon_smoke_name = self._payload_with_name_suffix(create_payload, "toon-smoke").get("name")
            if isinstance(toon_smoke_name, str):
                toon_smoke_id = self._find_entity_id_by_name(entity=entity, name=toon_smoke_name, create_payload=create_payload)
                if toon_smoke_id:
                    track_collection.append(toon_smoke_id)
        if entity == "project" and toon_smoke_id:
            self._assert_request(
                "CLEANUP TOON project.create (smoke)",
                "POST",
                "/raw/project.delete",
                payload={"project_id": toon_smoke_id},
                expected_status=200,
            )
            track_collection[:] = [item for item in track_collection if item != toon_smoke_id]
        toon_id = self._extract_id(f"SEED RAW {entity}.create for TOON flow", toon_seed, id_field=id_field_hint)
        track_collection.append(toon_id)
        toon_updated = self._assert_request(
            f"TOON {entity}.update",
            "POST",
            f"/toon/{entity}.update",
            payload=update_payload_builder(toon_id),
            expected_status=200,
        )
        self._assert_toon_envelope(f"TOON {entity}.update", toon_updated)
        toon_deleted = self._assert_request(
            f"TOON {entity}.delete",
            "POST",
            f"/toon/{entity}.delete",
            payload=delete_payload_builder(toon_id),
            expected_status=200,
        )
        self._assert_equal(f"TOON {entity}.delete", toon_deleted, {"d": {"ok": 1}})
        track_collection[:] = [item for item in track_collection if item != toon_id]

        # default mode
        default_seed = self._assert_request(
            f"SEED RAW {entity}.create for DEFAULT flow",
            "POST",
            f"/raw/{entity}.create",
            payload=self._payload_with_name_suffix(create_payload, "default-seed"),
            expected_status=200,
        )
        default_create_smoke = self._assert_request(
            f"DEFAULT {entity}.create (smoke)",
            "POST",
            f"/{entity}.create",
            payload=self._payload_with_name_suffix(create_payload, "default-smoke"),
            expected_status=200,
        )
        self._assert_toon_envelope(f"DEFAULT {entity}.create (smoke)", default_create_smoke)
        default_smoke_id = self._extract_toon_entity_id(default_create_smoke)
        if default_smoke_id:
            track_collection.append(default_smoke_id)
        else:
            default_smoke_name = self._payload_with_name_suffix(create_payload, "default-smoke").get("name")
            if isinstance(default_smoke_name, str):
                default_smoke_id = self._find_entity_id_by_name(
                    entity=entity,
                    name=default_smoke_name,
                    create_payload=create_payload,
                )
                if default_smoke_id:
                    track_collection.append(default_smoke_id)
        if entity == "project" and default_smoke_id:
            self._assert_request(
                "CLEANUP DEFAULT project.create (smoke)",
                "POST",
                "/raw/project.delete",
                payload={"project_id": default_smoke_id},
                expected_status=200,
            )
            track_collection[:] = [item for item in track_collection if item != default_smoke_id]
        default_id = self._extract_id(
            f"SEED RAW {entity}.create for DEFAULT flow",
            default_seed,
            id_field=id_field_hint,
        )
        track_collection.append(default_id)
        default_updated = self._assert_request(
            f"DEFAULT {entity}.update",
            "POST",
            f"/{entity}.update",
            payload=update_payload_builder(default_id),
            expected_status=200,
        )
        self._assert_toon_envelope(f"DEFAULT {entity}.update", default_updated)
        default_deleted = self._assert_request(
            f"DEFAULT {entity}.delete",
            "POST",
            f"/{entity}.delete",
            payload=delete_payload_builder(default_id),
            expected_status=200,
        )
        self._assert_equal(f"DEFAULT {entity}.delete", default_deleted, {"d": {"ok": 1}})
        track_collection[:] = [item for item in track_collection if item != default_id]

    def _ensure_task_move_targets(self) -> tuple[str, str, str]:
        if (
            self.move_destination_project_id is not None
            and self.move_destination_section_id is not None
            and self.main_project_id is not None
        ):
            return (
                self.main_project_id,
                self.move_destination_project_id,
                self.move_destination_section_id,
            )

        if self.main_project_id is None:
            raise RuntimeError("task.move shared source project is not initialized")
        src_project_id = self.main_project_id

        dst_project_payload = self._assert_request(
            "SEED task.move shared destination project",
            "POST",
            "/raw/project.create",
            payload={"name": f"IT-PROXY {self.run_id} move shared dst project"},
            expected_status=200,
        )
        dst_project_id = self._extract_id("SEED task.move shared destination project", dst_project_payload)
        self.created_projects.append(dst_project_id)

        dst_section_payload = self._assert_request(
            "SEED task.move shared destination section",
            "POST",
            "/raw/section.create",
            payload={
                "name": f"IT-PROXY {self.run_id} move shared dst section",
                "projectId": dst_project_id,
            },
            expected_status=200,
        )
        dst_section_id = self._extract_id("SEED task.move shared destination section", dst_section_payload)
        self.created_sections.append(dst_section_id)

        self.move_source_project_id = src_project_id
        self.move_destination_project_id = dst_project_id
        self.move_destination_section_id = dst_section_id
        return src_project_id, dst_project_id, dst_section_id

    def _cleanup_task_move_targets(self) -> None:
        if self.move_destination_section_id is not None:
            section_id = self.move_destination_section_id
            self._best_effort_request(
                "POST",
                "/raw/section.delete",
                payload={"task_group_id": section_id},
            )
            self.created_sections = [item for item in self.created_sections if item != section_id]
            self.move_destination_section_id = None

        if self.move_destination_project_id is not None:
            project_id = self.move_destination_project_id
            self._best_effort_request(
                "POST",
                "/raw/project.delete",
                payload={"project_id": project_id},
            )
            self.created_projects = [item for item in self.created_projects if item != project_id]
            self.move_destination_project_id = None

        if (
            self.move_source_project_id is not None
            and self.move_source_project_id != self.main_project_id
        ):
            project_id = self.move_source_project_id
            self._best_effort_request(
                "POST",
                "/raw/project.delete",
                payload={"project_id": project_id},
            )
            self.created_projects = [item for item in self.created_projects if item != project_id]
        self.move_source_project_id = None

    def _test_task_move_mode(self, *, mode_name: str, move_path: str, expect_toon: bool) -> None:
        mode_slug = mode_name.lower()
        src_project_id, dst_project_id, dst_section_id = self._ensure_task_move_targets()

        source_task_name = f"IT-PROXY {self.run_id} move {mode_slug} source task"
        source_task_payload = self._assert_request(
            f"SEED {mode_name} task.move source task",
            "POST",
            "/raw/task.create",
            payload={
                "name": source_task_name,
                "projectId": src_project_id,
            },
            expected_status=200,
        )
        source_task_id = self._extract_id(f"SEED {mode_name} task.move source task", source_task_payload)
        self.created_tasks.append(source_task_id)

        moved_payload = self._assert_request(
            f"{mode_name} task.move",
            "POST",
            move_path,
            payload={
                "task_id": source_task_id,
                "projectId": dst_project_id,
                "sectionId": dst_section_id,
            },
            expected_status=200,
        )
        if expect_toon:
            self._assert_toon_envelope(f"{mode_name} task.move", moved_payload)
            moved_task_id = self._extract_toon_entity_id(moved_payload)
            if moved_task_id is None:
                self._record(
                    f"ASSERT {mode_name} task.move id extraction",
                    False,
                    None,
                    f"cannot extract moved task id from payload={moved_payload!r}",
                )
                raise RuntimeError(f"{mode_name} task.move returned no task id")
        else:
            moved_task_id = self._extract_id(f"{mode_name} task.move", moved_payload)

        if moved_task_id == source_task_id:
            self._record(
                f"ASSERT {mode_name} task.move id stays same",
                True,
                200,
                f"move kept original id ({source_task_id})",
            )
        else:
            self._record(
                f"ASSERT {mode_name} task.move id stays same",
                False,
                None,
                f"expected moved task id={source_task_id!r}, got {moved_task_id!r}",
            )

        self.created_tasks = [item for item in self.created_tasks if item != source_task_id]
        self.created_tasks.append(moved_task_id)

        moved_task_get = self._assert_request(
            f"VERIFY {mode_name} task.move destination task get",
            "POST",
            "/raw/task.get",
            payload={"task_id": moved_task_id},
            expected_status=200,
        )
        moved_project = moved_task_get.get("project_id") if isinstance(moved_task_get, dict) else None
        moved_section = moved_task_get.get("section_id") if isinstance(moved_task_get, dict) else None
        if str(moved_project) == str(dst_project_id):
            self._record(
                f"ASSERT {mode_name} task.move project ref",
                True,
                200,
                f"project_id matches destination ({dst_project_id})",
            )
        else:
            self._record(
                f"ASSERT {mode_name} task.move project ref",
                False,
                None,
                f"expected project_id={dst_project_id!r}, got {moved_project!r}",
            )
        if str(moved_section) == str(dst_section_id):
            self._record(
                f"ASSERT {mode_name} task.move section ref",
                True,
                200,
                f"section_id matches destination ({dst_section_id})",
            )
        else:
            self._record(
                f"ASSERT {mode_name} task.move section ref",
                False,
                None,
                f"expected section_id={dst_section_id!r}, got {moved_section!r}",
            )

        src_project_list = self._assert_request(
            f"VERIFY {mode_name} task.move source project list",
            "POST",
            "/raw/task.list_by_project",
            payload={"project_id": src_project_id},
            expected_status=200,
        )
        if source_task_id not in self._extract_raw_task_ids(src_project_list):
            self._record(
                f"ASSERT {mode_name} task.move source project list",
                True,
                200,
                f"source task id {source_task_id} is absent in source project",
            )
        else:
            self._record(
                f"ASSERT {mode_name} task.move source project list",
                False,
                None,
                f"source task id {source_task_id} still present in source project",
            )

        dst_project_list = self._assert_request(
            f"VERIFY {mode_name} task.move destination project list",
            "POST",
            "/raw/task.list_by_project",
            payload={"project_id": dst_project_id},
            expected_status=200,
        )
        if moved_task_id in self._extract_raw_task_ids(dst_project_list):
            self._record(
                f"ASSERT {mode_name} task.move destination project list",
                True,
                200,
                f"moved task id {moved_task_id} found in destination project",
            )
        else:
            self._record(
                f"ASSERT {mode_name} task.move destination project list",
                False,
                None,
                f"moved task id {moved_task_id} absent in destination project",
            )

    def _extract_raw_task_ids(self, payload: Any) -> set[str]:
        rows: Any = payload
        if isinstance(payload, dict):
            for key in ("results", "tasks", "items", "data", "content", "list"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    rows = candidate
                    break

        ids: set[str] = set()
        if not isinstance(rows, list):
            return ids
        for item in rows:
            if isinstance(item, dict) and item.get("id") is not None:
                ids.add(str(item["id"]))
        return ids

    def _payload_with_name_suffix(self, payload: dict[str, Any], suffix: str) -> dict[str, Any]:
        cloned = dict(payload)
        name = cloned.get("name")
        if isinstance(name, str):
            cloned["name"] = f"{name} {suffix}"
        return cloned

    def _find_entity_id_by_name(
        self,
        *,
        entity: str,
        name: str,
        create_payload: dict[str, Any],
    ) -> str | None:
        if entity == "project":
            return self._find_project_id_by_name(name)
        if entity == "section":
            project_id = create_payload.get("projectId") or self.main_project_id
            if project_id is None:
                return None
            return self._find_section_id_by_name(str(project_id), name)
        if entity == "task":
            project_id = create_payload.get("projectId") or self.main_project_id
            return self._find_task_id_by_content(content=name, project_id=str(project_id) if project_id else None)
        return None

    def _find_project_id_by_name(self, name: str) -> str | None:
        response = self._assert_request(
            f"LOOKUP project id by name ({name})",
            "POST",
            "/raw/project.list",
            payload={},
            expected_status=200,
        )
        if not isinstance(response, dict):
            return None
        rows = response.get("results", [])
        if not isinstance(rows, list):
            return None
        for item in rows:
            if isinstance(item, dict) and item.get("name") == name and item.get("id") is not None:
                return str(item["id"])
        return None

    def _find_section_id_by_name(self, project_id: str, name: str) -> str | None:
        response = self._assert_request(
            f"LOOKUP section id by name ({name})",
            "POST",
            "/raw/section.list_by_project",
            payload={"project_id": project_id},
            expected_status=200,
        )
        if not isinstance(response, dict):
            return None
        rows = response.get("results", [])
        if not isinstance(rows, list):
            return None
        for item in rows:
            if isinstance(item, dict) and item.get("name") == name and item.get("id") is not None:
                return str(item["id"])
        return None

    def _find_task_id_by_content(
        self,
        *,
        content: str,
        project_id: str | None = None,
        parent_id: str | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {}
        path = "/raw/task.list"
        if project_id:
            path = "/raw/task.list_by_project"
            payload = {"project_id": project_id}
        response = self._assert_request(
            f"LOOKUP task id by content ({content})",
            "POST",
            path,
            payload=payload,
            expected_status=200,
        )
        if not isinstance(response, dict):
            return None
        rows = response.get("results", [])
        if not isinstance(rows, list):
            return None
        for item in rows:
            if not isinstance(item, dict):
                continue
            if item.get("content") != content:
                continue
            if parent_id is not None and str(item.get("parent_id")) != str(parent_id):
                continue
            if item.get("id") is not None:
                return str(item["id"])
        return None

    def _seed_resources(self) -> None:
        project_payload = self._assert_request(
            "SEED project.create",
            "POST",
            "/raw/project.create",
            payload={"name": f"IT-PROXY {self.run_id} main project"},
            expected_status=200,
        )
        self.main_project_id = self._extract_id("SEED project.create", project_payload)
        self.created_projects.append(self.main_project_id)

        section_payload = self._assert_request(
            "SEED section.create",
            "POST",
            "/raw/section.create",
            payload={
                "name": f"IT-PROXY {self.run_id} main section",
                "projectId": self.main_project_id,
            },
            expected_status=200,
        )
        self.main_section_id = self._extract_id("SEED section.create", section_payload)
        self.created_sections.append(self.main_section_id)

        task_payload = self._assert_request(
            "SEED task.create",
            "POST",
            "/raw/task.create",
            payload={
                "name": f"IT-PROXY {self.run_id} main task",
                "description": "integration test seed task",
                "startDate": self.today,
                "projectId": self.main_project_id,
                "taskGroupId": self.main_section_id,
            },
            expected_status=200,
        )
        self.main_task_id = self._extract_id("SEED task.create", task_payload)
        self.created_tasks.append(self.main_task_id)

    def _cleanup(self) -> None:
        # Best-effort cleanup; no assertions here.
        for task_id in list(reversed(self.created_tasks)):
            self._best_effort_request(
                "POST",
                "/raw/task.delete",
                payload={"task_id": task_id},
            )
        self.created_tasks.clear()

        for section_id in list(reversed(self.created_sections)):
            self._best_effort_request(
                "POST",
                "/raw/section.delete",
                payload={"task_group_id": section_id},
            )
        self.created_sections.clear()

        for project_id in list(reversed(self.created_projects)):
            self._best_effort_request(
                "POST",
                "/raw/project.delete",
                payload={"project_id": project_id},
            )
        self.created_projects.clear()

    def _start_server(self) -> None:
        os.environ["TODOIST_RATE_LIMIT_RPS"] = "20"
        os.environ["TODOIST_RATE_LIMIT_BURST"] = "20"
        os.environ["TODOIST_CACHE_TTL_SECONDS"] = "15"
        os.environ["TODOIST_CACHE_MAX_SIZE"] = "2048"
        os.environ["TODOIST_RATE_LIMIT_STATE_FILE"] = self.rate_state_file

        app = create_app()
        self.server = make_server("127.0.0.1", 0, app)
        port = int(self.server.server_port)
        self.base_url = f"http://127.0.0.1:{port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

        deadline = time.time() + 8
        last_error = ""
        while time.time() < deadline:
            try:
                status_code, payload, raw = self._http_request(method="GET", path="/methods")
                if status_code == 200:
                    return
                last_error = f"status={status_code} payload={payload!r} raw={raw!r}"
            except Exception as exc:
                last_error = f"request failed with exception: {exc!r}"
                time.sleep(0.2)
        raise RuntimeError(f"failed to start local proxy server: {last_error}")

    def _stop_server(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        if self.server_thread is not None:
            self.server_thread.join(timeout=3)

    def _assert_methods_catalog(self, payload: Any) -> None:
        expected_methods = {
            "task.list",
            "task.list_by_project",
            "task.list_by_date",
            "task.get",
            "task.create",
            "task.update",
            "task.move",
            "task.delete",
            "task.close",
            "project.list",
            "project.get",
            "project.create",
            "project.update",
            "project.delete",
            "section.list_by_project",
            "section.get",
            "section.create",
            "section.update",
            "section.delete",
            "checklist.create",
            "checklist.update",
            "checklist.delete",
        }
        if not isinstance(payload, dict) or "methods" not in payload:
            self._record("ASSERT methods catalog shape", False, None, "payload has no 'methods' key")
            return
        names = {item.get("name") for item in payload.get("methods", []) if isinstance(item, dict)}
        missing = sorted(expected_methods - names)
        extra = sorted(names - expected_methods)
        if missing or extra:
            self._record(
                "ASSERT methods catalog content",
                False,
                200,
                f"missing={missing} extra={extra}",
            )
            return
        self._record("ASSERT methods catalog content", True, 200, "method catalog matches expected set")

    def _assert_toon_envelope(self, name: str, payload: Any) -> None:
        if isinstance(payload, dict) and "d" in payload:
            self._record(f"ASSERT {name} toon envelope", True, 200, "has top-level key 'd'")
            return
        self._record(f"ASSERT {name} toon envelope", False, None, "missing top-level key 'd'")

    def _assert_field_contains(self, name: str, payload: Any, field: str, needle: str) -> None:
        value = payload.get(field) if isinstance(payload, dict) else None
        if isinstance(value, str) and needle in value:
            self._record(f"ASSERT {name} field contains", True, 200, f"{field} contains '{needle}'")
            return
        self._record(
            f"ASSERT {name} field contains",
            False,
            None,
            f"field '{field}' does not contain '{needle}', value={value!r}",
        )

    def _assert_equal(self, name: str, actual: Any, expected: Any) -> None:
        if actual == expected:
            self._record(f"ASSERT {name} equality", True, 200, "payload matches expected")
            return
        self._record(
            f"ASSERT {name} equality",
            False,
            None,
            f"expected={expected!r} actual={actual!r}",
        )

    def _assert_toon_task_section_ref(self, name: str, payload: Any, expected_section_id: str | None) -> None:
        task = payload.get("d") if isinstance(payload, dict) else None
        section_ref = task.get("tg") if isinstance(task, dict) else None
        if section_ref == expected_section_id:
            self._record(
                f"ASSERT {name} section ref",
                True,
                200,
                f"tg matches expected section id ({expected_section_id})",
            )
            return
        self._record(
            f"ASSERT {name} section ref",
            False,
            None,
            f"expected tg={expected_section_id!r}, got={section_ref!r}, payload={payload!r}",
        )

    def _extract_toon_task_names(self, payload: Any) -> set[str]:
        names: set[str] = set()
        if not isinstance(payload, dict):
            return names
        rows = payload.get("d")
        if not isinstance(rows, list):
            return names
        for item in rows:
            if not isinstance(item, dict):
                continue
            value = item.get("n")
            if isinstance(value, str):
                names.add(value)
        return names

    def _extract_next_cursor(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get("next_cursor")
        if isinstance(value, str) and value.strip():
            return value
        return None

    def _normalize_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        return {
            key: value
            for key, value in payload.items()
            if value is not None
        }

    def _extract_id(self, name: str, payload: Any, id_field: str = "id") -> str:
        if isinstance(payload, dict) and payload.get(id_field) is not None:
            return str(payload[id_field])
        self._record(name, False, None, f"cannot extract '{id_field}' from payload={payload!r}")
        raise RuntimeError(f"failed to extract id for case: {name}")

    def _extract_toon_entity_id(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("d")
        if isinstance(data, dict) and data.get("i") is not None:
            return str(data["i"])
        return None

    def _assert_request(
        self,
        name: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        expected_status: int = 200,
        token_override: str | None = None,
        use_alt_header: bool = False,
    ) -> Any:
        status_code, response_payload, response_text = self._http_request(
            method=method,
            path=path,
            payload=payload,
            token_override=token_override,
            use_alt_header=use_alt_header,
        )
        if status_code == expected_status:
            self._record(name, True, status_code, "request succeeded")
            return response_payload

        self._record(
            name,
            False,
            status_code,
            f"expected {expected_status}, got {status_code}, payload={response_payload!r}, raw={response_text!r}",
        )
        raise RuntimeError(f"{name} failed with status {status_code}")

    def _best_effort_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            self._http_request(method=method, path=path, payload=payload)
        except Exception:
            return

    def _http_request(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        token_override: str | None = None,
        use_alt_header: bool = False,
    ) -> tuple[int, Any, str]:
        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json",
        }
        effective_token = self.token if token_override is None else token_override
        if effective_token:
            if use_alt_header:
                headers["X-TODOIST-ACCESS-TOKEN"] = effective_token
            else:
                headers["Authorization"] = f"Bearer {effective_token}"

        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib_request.Request(url=url, data=data, method=method, headers=headers)

        try:
            with urllib_request.urlopen(req, timeout=40) as resp:
                body = resp.read().decode("utf-8")
                parsed = self._parse_json(body)
                return resp.status, parsed, body
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            parsed = self._parse_json(body)
            return exc.code, parsed, body

    def _read_rate_limit_scopes(self) -> set[str]:
        try:
            with open(self.rate_state_file, "r", encoding="utf-8") as fp:
                parsed = json.load(fp)
        except (OSError, ValueError):
            return set()
        if not isinstance(parsed, dict):
            return set()
        return {str(key) for key in parsed.keys()}

    def _parse_json(self, raw: str) -> Any:
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {"message": raw}

    def _record(self, name: str, ok: bool, status_code: int | None, details: str) -> None:
        self.results.append(CaseResult(name=name, ok=ok, status_code=status_code, details=details))

    def _write_report(self) -> None:
        total = len(self.results)
        passed = len([item for item in self.results if item.ok])
        failed = total - passed
        lines = [
            "# Integration Test Report",
            "",
            f"- Timestamp (UTC): `{datetime.utcnow().isoformat(timespec='seconds')}`",
            f"- Token fingerprint: `{token_fingerprint(self.token)}`",
            f"- Base URL: `{self.base_url or 'n/a'}`",
            f"- Total checks: `{total}`",
            f"- Passed: `{passed}`",
            f"- Failed: `{failed}`",
            "",
            "## Results",
            "",
            "| # | Check | Result | HTTP | Details |",
            "|---|---|---|---|---|",
        ]
        for idx, item in enumerate(self.results, start=1):
            status_emoji = "PASS" if item.ok else "FAIL"
            http_value = str(item.status_code) if item.status_code is not None else "-"
            details = item.details.replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {idx} | {item.name} | {status_emoji} | {http_value} | {details} |")

        report_parent = pathlib.Path(self.report_file).expanduser().resolve().parent
        report_parent.mkdir(parents=True, exist_ok=True)
        with open(self.report_file, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")

def main() -> int:
    parser = argparse.ArgumentParser(prog="run_live_integration")
    parser.add_argument(
        "--token",
        default=os.getenv("TODOIST_ACCESS_TOKEN", ""),
        help="Todoist token. Defaults to TODOIST_ACCESS_TOKEN env.",
    )
    parser.add_argument(
        "--report-file",
        default="reports/INTEGRATION_TEST_REPORT.md",
        help="Path to markdown report file.",
    )
    args = parser.parse_args()

    runner = LiveIntegrationRunner(token=args.token, report_file=args.report_file)
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
