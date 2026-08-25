# Written Answers

## 1. Cardinality

Suppose a teammate adds `user_id` as a label on a Prometheus counter that tracks API requests, on a service with 2 million users. What happens to that metric over the life of the service, and what would you suggest instead if per-user request counts are genuinely needed?

### Answer

You'd end up with roughly 2 million separate time series in Prometheus, one for each unique user_id. That blows up memory on both the service and the server side, makes queries slow (sifting through millions of series), and fills your disk over time. It's a classic cardinality explosion.

If you genuinely need per-user counts, don't store them in Prometheus at all. Use something like a data warehouse or log aggregation system that's built for high cardinality, or sample a subset of users instead of instrumenting all 2 million. Keep Prometheus metrics to low-cardinality dimensions like endpoint or status code.

---

## 2. p99 vs. average

A dashboard shows average request latency holding steady at 80ms, but users are filing tickets about the app "randomly" feeling slow. What might the average be hiding, and what would you look at instead?

### Answer

The average is hiding tail latencies. If 95% of requests are 50ms but 5% are 2 seconds, the average looks fine even though users hit the slow ones regularly and feel random slowness. An average also smooths over bimodal distributions — cache hits at 20ms and cache misses at 500ms average to something that doesn't represent either experience.

Look at percentiles instead: p95, p99, and p99.9 tell you what slower users are actually experiencing. Dive into traces for the slow requests specifically to understand why — is it a missing cache, a database query, a specific endpoint? Percentiles and traces together paint a much clearer picture than an average ever could.

---

## 3. Streaming / backpressure

You're exporting spans from a fleet of services to a Collector gateway over OTLP, and the gateway's downstream exporter (say, a tracing backend) starts throttling requests. Walk through what should happen next in the pipeline, and what could go wrong if buffering and retry aren't handled carefully.

### Answer

When the backend throttles, the Collector's exporter should buffer the spans and retry with exponential backoff instead of hammering the throttled endpoint. The buffer has a bounded size, so once it's full, the SDK explicitly drops or blocks new spans gracefully. Eventually the backend recovers, retries succeed, and the queue drains.

Without careful handling, several things can fail catastrophically. Unbounded buffering causes the Collector to run out of memory if backpressure lasts hours. Aggressive retries with no backoff make the problem worse. If the SDK blocks on export, upstream request handlers block too, and users see timeouts even though the application logic is fine. Lost spans can break traces, and silent drops mean you lose observability without knowing it. The key safeguards are a bounded queue, exponential backoff with a retry limit, and failing fast on unrecoverable errors like 4xx responses.

---

## 4. Instrumenting agent workflows

`/agent/analyze` runs a plan -> tool_call -> model_inference pipeline — similar in shape to real agentic systems that call external tools and LLMs. If you were designing the instrumentation approach for a whole codebase full of workflows like this (not just one endpoint), what would you standardize so that every workflow's cost and latency show up consistently, without every engineer hand-rolling their own spans?

### Answer

I'd provide a central metrics module with pre-defined counters for token counts and histograms for step durations, so engineers aren't creating new metrics every time. Then a reusable decorator that wraps workflow steps and handles timing, span creation, and recording metrics automatically — engineers just apply it to their step functions and it's done. This is the kind of detail that's easy to get subtly wrong if everyone does it themselves, like the thread-context propagation I had to manually handle when running `tool_call` in the executor — abstracting that away means fewer bugs.

For naming, use a consistent convention like `workflow.{workflow_name}.{step_name}` for spans and standardize attributes so every workflow span gets `workflow.name` and every step gets `step.name` and `step.index`. Export token counts as counters, not histograms, so they're additive across workflows. This way every workflow produces cost and latency data in the same shape without engineers thinking about it, and dashboards can query "all model_inference steps" and get consistent results across the entire codebase.

---

## 5. DaemonSet Bugs

The `node-telemetry-agent` DaemonSet manifest had three bugs: one that prevents pods from running at all, and two silent failures that look correct but are fundamentally broken.

### Bug #1: Selector/Label Mismatch (CRITICAL)

The selector said `app: node-telemetry` but the pod template had `app: node-telemetry-agent`. A DaemonSet uses its selector to find and manage pods, so when they don't match, Kubernetes can't associate them. Result: `kubectl get daemonsets` shows 0 desired, 0 ready — not a single pod runs. This is immediately visible because nothing happens.

**The fix:** Change `spec.selector.matchLabels.app` to `node-telemetry-agent` to match the pod label.

---

### Bug #2: Missing Tolerations (SILENT)

The manifest comment says "run on EVERY node in the cluster -- control-plane nodes included," but there were no tolerations for the control-plane taint. Control-plane nodes have `node-role.kubernetes.io/control-plane:NoSchedule` by default, which blocks regular pods. Without a toleration, the DaemonSet silently skips all control-plane nodes entirely. Worker nodes get pods just fine, so `kubectl get pods` looks correct, but you're missing control-plane instrumentation.

**The fix:** Add a toleration:
```yaml
tolerations:
  - key: node-role.kubernetes.io/control-plane
    operator: Exists
    effect: NoSchedule
```

---

### Bug #3: Missing `hostPID: true` (SILENT)

The container had `hostPath` mounts to `/proc` and `/sys`, but no `hostPID: true`. This means the container ran in its own PID namespace, so when it read `/proc`, it saw only its own container's processes, not the host's entire process table. The agent ran without errors, logs looked normal, but the data was useless — it was collecting metrics for a single container, not the entire node.

**The fix:** Add `hostPID: true` to the pod spec so the container sees the host's full process table.

---

## 6. Redfish vs. IPMI

### Why out-of-band monitoring instead of a host-OS agent

A host-OS agent reading `/sys` thermal zones only works if the OS is running and healthy. When the kernel hangs, fails to boot, or the main board loses power, the OS agent goes silent and you lose visibility exactly when you need it. A BMC is a completely separate processor with its own network port and often its own standby power, so it monitors the host even when it's completely down — you get thermal data, power state, and hardware health when the operating system is the problem, not just when it's running smoothly.

### When to use IPMI over Redfish

Redfish is newer and cleaner (JSON, REST, structured schemas), but IPMI is older and universally supported across hardware generations. If you're managing a fleet with mixed hardware ages — old servers from a decade ago alongside newer stuff — IPMI will work everywhere while Redfish might only exist on recent models. IPMI is primitive (raw byte codes), but universal support often wins in heterogeneous infrastructure.

---

## 7. CentOS vs. Ubuntu, same systemd unit file

### Package manager and library paths

CentOS/RHEL use `dnf`/`yum` and RPM, while Ubuntu uses `apt` and `.deb`. A systemd service that depends on a library might work fine on Ubuntu because the `.deb` installs it in a standard path, but fail on CentOS if the RPM version is different or installed elsewhere. The same binary and same systemd unit file can't find its dependencies.

### SELinux enforcement

CentOS/RHEL have SELinux enforcing by default, while Ubuntu uses AppArmor or has it disabled. A service that runs fine on Ubuntu might be silently denied file access, network operations, or specific syscalls on CentOS by SELinux policy. The service starts without obvious errors, but syscalls get blocked, causing subtle failures or hangs.

---

## 8. Diagnosing a slow node, no monitoring installed

### CPU load and run-queue: `uptime` or `top`

`uptime` shows 1/5/15-minute load averages. If load is 20 on a 4-core machine, the CPU is saturated and tasks are queuing. `top` breaks it down per-process so you see which process (or few) are burning CPU.

### Memory pressure: `free -h`

Shows total, used, and available memory. If available is near zero and swap is being used heavily, memory pressure is cascading I/O latency across the system. Pages are being evicted to disk, causing slowness everywhere.

### Disk I/O bottleneck: `iostat -x` or `iotop`

`iostat -x` shows I/O utilization per device and latency metrics (`await`). If utilization is near 100% or await is high, disk is the bottleneck. `iotop` shows which processes are doing the I/O, pinpointing the culprit.

### Per-process memory breakdown: `ps aux | sort -k4 -nr`

Shows which processes use the most memory. If one process is using 80% of RAM, it's either the problem or it's causing memory pressure that cascades to everything else.

---
