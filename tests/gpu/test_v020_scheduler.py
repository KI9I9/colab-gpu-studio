from __future__ import annotations

from app.db import Store
from app.gpu.compute import MockColabProvider, MockGPUExecutor
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


class FakeInstanceService:
    """Offline v0.2 instance boundary used to prove scheduler coordination."""

    def __init__(self, *, lifecycle="retain", clock=lambda: 1_000.0):
        self.clock = clock
        self.lifecycle = lifecycle
        self.instances = {}
        self.calls = []
        self._next = 0

    def list(self, owner):
        return [dict(row) for row in self.instances.values()]

    def get(self, owner, instance_id):
        row = self.instances.get(instance_id)
        return dict(row) if row else None

    def allocate(self, job):
        self.calls.append(("allocate", job["id"]))
        self._next += 1
        instance = {
            "instance_id": f"instance-{self._next}",
            "account_id": "account-1",
            "state": "allocating",
            "retention_mode": self.lifecycle,
            "hard_deadline_at": self.clock() + 600,
            "idle_deadline_at": None,
            "compatibility_signature": job.get("compatibility_signature"),
        }
        self.instances[instance["instance_id"]] = instance
        return dict(instance)

    def bind_lease(self, job, lease):
        self.calls.append(("bind_lease", job["id"], lease["id"]))
        instance = self.instances[job["instance_id"]]
        instance["lease_id"] = lease["id"]
        instance["state"] = "allocating"
        return dict(instance)

    def mark_ready(self, job):
        self.calls.append(("mark_ready", job["id"]))
        instance = self.instances[job["instance_id"]]
        instance["state"] = "ready"
        return dict(instance)

    def job_busy(self, job):
        self.calls.append(("job_busy", job["id"]))
        instance = self.instances[job["instance_id"]]
        instance["state"] = "busy"
        instance["current_job_id"] = job["id"]
        return dict(instance)

    def complete(self, job, success=True):
        self.calls.append(("complete", job["id"], success))
        instance = self.instances[job["instance_id"]]
        instance["current_job_id"] = None
        if success and self.lifecycle == "retain":
            instance["state"] = "retained_idle"
            instance["idle_deadline_at"] = self.clock() + 120
        else:
            instance["state"] = "releasing"
        return dict(instance)

    def mark_deleted(self, owner, instance_id, expected_version=None, remote_deleted=False):
        assert owner == "owner-1"
        assert remote_deleted is True
        self.calls.append(("mark_deleted", instance_id, remote_deleted))
        self.instances[instance_id]["state"] = "deleted"
        return dict(self.instances[instance_id])

    def record_usage(self, owner, payload):
        self.calls.append(("record_usage", payload["instance_id"]))

    def public(self, instance):
        return {k: v for k, v in instance.items() if k != "runtime_name"}

    def retention(self, instance, retain=True, **kwargs):
        self.calls.append(("retention", instance["instance_id"], retain))
        return dict(instance)

    def stop(self, instance):
        self.calls.append(("stop", instance["instance_id"]))
        return dict(instance)


def make_job(repo, *, key="job", lifecycle="retain"):
    return repo.create_job(
        "owner-1",
        {
            "provider": "mock",
            "project_id": "project-1",
            "snapshot": {"manifest": {"mock": True}},
            "execution_policy": {"provider": "mock", "lifecycle": lifecycle},
            "estimated_units": 0,
        },
        key,
    )[0]


def run_to_terminal(scheduler, repo, job_id, limit=40):
    for _ in range(limit):
        scheduler.tick()
        job = repo.get("jobs", job_id, "owner-1")
        if job["state"] in {"completed", "failed", "cancelled"}:
            return job
    return repo.get("jobs", job_id, "owner-1")


def test_v020_allocates_and_binds_instance_before_execution():
    repo = Repository(Store("sqlite:///:memory:"))
    job = make_job(repo)
    service = FakeInstanceService()
    provider, executor = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(
        repo,
        provider_factory=lambda _: provider,
        executor_factory=lambda _: executor,
        instance_service=service,
        clock=service.clock,
    )

    result = run_to_terminal(scheduler, repo, job["id"])

    assert result["state"] == "completed"
    assert result["instance_id"] == "instance-1"
    assert result["account_id"] == "account-1"
    execution = repo.get("executions", result["execution_id"], "owner-1")
    assert execution["instance_id"] == "instance-1"
    lease = repo.get("leases", result["lease_id"], "owner-1")
    assert lease["instance_id"] == "instance-1"
    assert [call[0] for call in service.calls[:3]] == ["allocate", "bind_lease", "mark_ready"]
    assert ("job_busy", job["id"]) in service.calls


def test_v020_retained_instance_survives_completed_job_and_watchdog_until_deadline():
    now = [1_000.0]
    repo = Repository(Store("sqlite:///:memory:"))
    job = make_job(repo, lifecycle="retain")
    service = FakeInstanceService(clock=lambda: now[0])
    provider, executor = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(
        repo,
        provider_factory=lambda _: provider,
        executor_factory=lambda _: executor,
        instance_service=service,
        clock=lambda: now[0],
    )

    result = run_to_terminal(scheduler, repo, job["id"])
    assert result["state"] == "completed"
    instance = service.instances["instance-1"]
    assert instance["state"] == "retained_idle"
    assert provider.delete_calls == 0

    scheduler.watchdog()
    assert instance["state"] == "retained_idle"
    assert provider.delete_calls == 0

    now[0] = instance["idle_deadline_at"] + 1
    scheduler.watchdog()
    assert instance["state"] == "deleted"
    assert provider.delete_calls == 1
    assert repo.get("leases", result["lease_id"], "owner-1")["state"] == "deleted"


def test_v020_save_requires_remote_delete_confirmation_before_marking_deleted():
    class AcceptedButNotGoneProvider(MockColabProvider):
        def delete(self, handle):
            self.delete_calls += 1
            return {"status": 202, "accepted": True}

    repo = Repository(Store("sqlite:///:memory:"))
    job = make_job(repo, lifecycle="save")
    service = FakeInstanceService(lifecycle="save")
    provider, executor = AcceptedButNotGoneProvider(), MockGPUExecutor()
    scheduler = Scheduler(
        repo,
        provider_factory=lambda _: provider,
        executor_factory=lambda _: executor,
        instance_service=service,
        clock=service.clock,
    )

    for _ in range(20):
        scheduler.tick()
        if repo.get("jobs", job["id"], "owner-1")["state"] == "cleanup_pending":
            break

    lease = repo.get("leases", repo.get("jobs", job["id"], "owner-1")["lease_id"], "owner-1")
    assert repo.get("jobs", job["id"], "owner-1")["state"] == "cleanup_pending"
    assert lease["state"] == "cleanup_pending"
    assert service.instances["instance-1"]["state"] != "deleted"
    assert not any(call[0] == "mark_deleted" for call in service.calls)


def test_v020_retained_instance_renews_per_job_deadline_across_four_jobs():
    from app.gpu.compute import GpuConfig
    from app.gpu.instances import InstanceService

    now = [1_000.0]
    cfg = GpuConfig(max_runtime_minutes=120)
    repo = Repository(Store("sqlite:///:memory:"))
    service = InstanceService(repo, clock=lambda: now[0], config=cfg)
    provider, executor = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(
        repo,
        provider_factory=lambda _: provider,
        executor_factory=lambda _: executor,
        config=cfg,
        instance_service=service,
        clock=lambda: now[0],
    )

    def create_retained_job(key: str, runtime_minutes: int = 30):
        return repo.create_job(
            "owner-1",
            {
                "provider": "mock",
                "project_id": "project-1",
                "compatibility_signature": "sig-krea",
                "max_runtime_minutes": runtime_minutes,
                "snapshot": {"manifest": {"mock": True}},
                "execution_policy": {
                    "provider": "mock",
                    "lifecycle": "retain",
                    "max_runtime_minutes": runtime_minutes,
                    "retain_idle_minutes": 15,
                },
                "estimated_units": 0,
            },
            key,
        )[0]

    j1 = create_retained_job("img-1")
    r1 = run_to_terminal(scheduler, repo, j1["id"])
    assert r1["state"] == "completed"
    inst = service.get("owner-1", r1["instance_id"])
    assert inst["hard_deadline_at"] == 1_000.0 + 120 * 60
    assert r1["deadline_epoch"] == 1_000.0 + 30 * 60
    assert provider.create_calls == 1

    now[0] = 1_600.0
    j2 = create_retained_job("img-2")
    r2 = run_to_terminal(scheduler, repo, j2["id"])
    assert r2["state"] == "completed"
    assert r2["instance_id"] == inst["id"]
    assert r2["deadline_epoch"] == 1_600.0 + 30 * 60
    assert provider.create_calls == 1

    now[0] = 2_200.0
    j3 = create_retained_job("img-3")
    r3 = run_to_terminal(scheduler, repo, j3["id"])
    assert r3["state"] == "completed"
    assert r3["instance_id"] == inst["id"]
    assert provider.create_calls == 1

    now[0] = 2_780.0
    slow_executor = MockGPUExecutor(polls_before_complete=3)
    scheduler.executor_factory = lambda _: slow_executor
    j4 = create_retained_job("img-4")
    for idx in range(25):
        if idx == 6:
            now[0] = 2_900.0
        scheduler.tick()
        cur4 = repo.get("jobs", j4["id"], "owner-1")
        if cur4["state"] in {"completed", "failed", "cancelled"}:
            break
    r4 = repo.get("jobs", j4["id"], "owner-1")
    assert r4["state"] == "completed"
    assert r4["instance_id"] == inst["id"]
    assert r4["deadline_epoch"] == 2_780.0 + 30 * 60
    assert provider.create_calls == 1
    assert provider.delete_calls == 0

    now[0] = 3_200.0
    scheduler.executor_factory = lambda _: MockGPUExecutor()
    j5 = create_retained_job("img-5", runtime_minutes=100)
    r5 = run_to_terminal(scheduler, repo, j5["id"])
    assert r5["state"] == "completed"
    assert r5["instance_id"] == inst["id"]
    assert r5["deadline_epoch"] == 1_000.0 + 120 * 60
    assert provider.create_calls == 1


def test_v020_orphan_cleanup_passes_account_id_and_recovers_failed_idle_or_deleted_instance():
    import uuid
    from app.gpu.compute import ComputeError, GpuConfig
    from app.gpu.instances import InstanceService

    now = [1_000.0]
    cfg = GpuConfig(
        max_runtime_minutes=120,
        cleanup_retry_base_seconds=30.0,
        cleanup_retry_max_seconds=300.0,
    )
    repo = Repository(Store("sqlite:///:memory:"))
    repo.create(
        "accounts",
        "owner-1",
        {
            "account_id": "acct-1",
            "alias": "acct-1",
            "provider": "mock",
            "connected": True,
            "enabled": True,
            "allow_allocation": True,
        },
        record_id="acct-1",
    )
    service = InstanceService(repo, clock=lambda: now[0], config=cfg)
    provider = MockColabProvider(fail_delete_times=1)
    seen_accounts = []

    class AccountCheckedProvider:
        def __init__(self, inner, account_id, job_id):
            self._inner = inner
            self._account_id = account_id
            self._job_id = job_id

        def _require_account(self, op: str):
            seen_accounts.append((op, self._job_id, self._account_id))
            if self._account_id != "acct-1":
                raise ComputeError("Google OAuth未连接，真实GPU能力已关闭")

        def create(self, spec, request_id, job_id, runtime_id):
            self._require_account("create")
            return self._inner.create(spec, request_id, job_id, runtime_id)

        def inspect(self, handle):
            self._require_account("inspect")
            return self._inner.inspect(handle)

        def delete(self, handle):
            self._require_account("delete")
            return self._inner.delete(handle)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def provider_factory(job):
        return AccountCheckedProvider(provider, job.get("account_id"), job.get("id"))

    scheduler = Scheduler(
        repo,
        provider_factory=provider_factory,
        executor_factory=lambda _: MockGPUExecutor(),
        config=cfg,
        instance_service=service,
        clock=lambda: now[0],
    )

    def submit_job(key: str, lifecycle: str = "retain"):
        return repo.create_job(
            "owner-1",
            {
                "provider": "mock",
                "project_id": "project-1",
                "compatibility_signature": "sig-l4",
                "max_runtime_minutes": 30,
                "snapshot": {"manifest": {"mock": True}},
                "execution_policy": {
                    "provider": "mock",
                    "lifecycle": lifecycle,
                    "max_runtime_minutes": 30,
                    "retain_idle_minutes": 15,
                },
                "estimated_units": 0,
            },
            key,
        )[0]

    j1 = submit_job("evening-job", lifecycle="retain")
    r1 = run_to_terminal(scheduler, repo, j1["id"])
    assert r1["state"] == "completed"
    old_instance_id = r1["instance_id"]
    old_lease_id = r1["lease_id"]
    old_inst = service.get("owner-1", old_instance_id)
    assert old_inst["state"] == "retained_idle"
    assert old_inst["hard_deadline_at"] == 1_000.0 + 120 * 60

    # Idle expires: first cleanup fails delete -> lease moves to cleanup_pending
    # while instance stays retained_idle, and account_id is passed.
    now[0] = old_inst["idle_deadline_at"] + 1.0
    scheduler.watchdog()
    failed_lease = repo.get("leases", old_lease_id, "owner-1")
    assert failed_lease["state"] == "cleanup_pending"
    assert ("delete", j1["id"], "acct-1") in seen_accounts

    # Next morning (well past yesterday's hard_deadline_at): a new job must not
    # reuse the expired/cleanup_pending instance or inherit its stale deadline.
    now[0] = old_inst["hard_deadline_at"] + 6 * 3600.0
    j2 = submit_job("morning-job", lifecycle="save")
    r2 = run_to_terminal(scheduler, repo, j2["id"])
    assert r2["state"] == "completed"
    assert r2["instance_id"] != old_instance_id
    assert r2["deadline_epoch"] == now[0] + 30 * 60

    # Watchdog retries the still-stuck retained_idle + cleanup_pending lease
    # without getting wedged by claim_orphan_lease and converges both to deleted.
    scheduler.watchdog()
    assert service.get("owner-1", old_instance_id)["state"] == "deleted"
    assert repo.get("leases", old_lease_id, "owner-1")["state"] == "deleted"

    # Also verify an orphan cleanup_pending lease whose instance is already deleted
    # converges to deleted instead of looping forever in claim_orphan_lease.
    req_id = str(uuid.uuid4())
    rt_id = "rt-already-deleted-inst"
    created_handle = provider.create({"provider": "mock"}, req_id, "job-dead", rt_id)
    created_handle = provider.bind_owner(created_handle, "owner-1")
    dead_inst = repo.create(
        "instances",
        "owner-1",
        {
            "account_id": "acct-1",
            "provider": "mock",
            "state": "deleted",
            "lease_id": "lease-successor",
        },
        record_id="inst-already-deleted",
    )
    orphan_lease = repo.create(
        "leases",
        "owner-1",
        {
            "owner_job_id": "job-dead",
            "provider": "mock",
            "account_id": "acct-1",
            "instance_id": dead_inst["id"],
            "state": "cleanup_pending",
            "request_id": req_id,
            "runtime_id": rt_id,
            "cleanup_next_epoch": 0.0,
            "handle": {
                **created_handle,
                "job_id": "job-dead",
                "owner_job_id": "job-dead",
            },
        },
    )
    scheduler.watchdog()
    done_orphan = repo.get("leases", orphan_lease["id"], "owner-1")
    assert done_orphan["state"] == "deleted"
    assert done_orphan["remote_delete_source"] == "orphan_cleanup_provider_confirmation"
    assert ("delete", "job-dead", "acct-1") in seen_accounts
