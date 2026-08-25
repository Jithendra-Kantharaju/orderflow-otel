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

The `node-telemetry-agent` DaemonSet manifest had three bugs: one that prevents pods from running at all, and two silent failures that look correct but are fundamentally broken. Here's the summary:

### Bug Summary

| Bug | Type | Visible? | Impact | Fix |
|-----|------|----------|--------|-----|
| **Selector/label mismatch** | Critical | ✅ Immediately visible | DaemonSet: 0/0 desired/ready, no pods run | Change `spec.selector.matchLabels.app` from `node-telemetry` to `node-telemetry-agent` |
| **Missing tolerations** | Silent | ❌ Pods look fine on workers | Silently skips all control-plane nodes (breaks fleet-wide coverage) | Add `tolerations` for `node-role.kubernetes.io/control-plane:NoSchedule` |
| **Missing `hostPID: true`** | Silent | ❌ Pod runs without errors | Container sees its own PID namespace, not host's (metrics are wrong) | Add `hostPID: true` to pod spec |

---

### Bug #1: Selector/Label Mismatch (CRITICAL)

**The problem:**
```yaml
spec:
  selector:
    matchLabels:
      app: node-telemetry          # doesn't match pod label
  template:
    metadata:
      labels:
        app: node-telemetry-agent  # pod has different label
```

A DaemonSet uses its selector to find and manage pods. If the selector doesn't match the pod labels, Kubernetes can't associate them. The result is immediate and obvious: `kubectl get daemonsets` shows 0 desired, 0 ready, 0 available — not a single pod runs. This is the one bug you catch immediately because nothing happens.

**The fix:**
Change `spec.selector.matchLabels.app` to `node-telemetry-agent` so it matches the pod template's label. Once they match, Kubernetes can associate the pods with the DaemonSet.

---

### Bug #2: Missing Tolerations (SILENT)

**The problem:**
The manifest comment says "run on EVERY node in the cluster -- control-plane nodes included." But control-plane nodes have a built-in taint: `node-role.kubernetes.io/control-plane:NoSchedule`. Without a matching toleration, the DaemonSet will skip all control-plane nodes entirely.

```yaml
spec:
  # No tolerations — this means control-plane taint will block scheduling
  containers:
    - name: node-telemetry-agent
```

This is a silent failure: worker nodes get pods just fine, so `kubectl get pods` looks correct. But you're missing control-plane instrumentation, which defeats the purpose of fleet-wide telemetry. You won't notice until you specifically look for pods on control-plane nodes.

**The fix:**
Add a toleration to override the control-plane taint:
```yaml
spec:
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      operator: Exists
      effect: NoSchedule
  containers:
    - name: node-telemetry-agent
```

This says "tolerate the control-plane:NoSchedule taint," allowing pods to schedule there.

---

### Bug #3: Missing `hostPID: true` (SILENT)

**The problem:**
The container has `hostPath` mounts to `/proc` and `/sys`, which give it filesystem access to the host's state. But Kubernetes also runs each container in its own PID namespace by default. This means when the agent reads `/proc`, it sees **its own container's processes**, not the host's entire process table.

```yaml
spec:
  # No hostPID: true — container is isolated in its own PID namespace
  containers:
    - name: node-telemetry-agent
      volumeMounts:
        - name: proc
          mountPath: /host/proc  # Can read these files...
        - name: sys
          mountPath: /host/sys   # ... but in wrong namespace
  volumes:
    - name: proc
      hostPath:
        path: /proc
```

The agent runs without errors. Logs look normal. But its data is useless — it's collecting CPU, scheduler, and process metrics for a single container, not the entire node. This is exactly the kind of silent failure that's hardest to debug: the pod looks healthy, but the metrics are completely wrong.

**The fix:**
Add `hostPID: true` to the pod spec:
```yaml
spec:
  hostPID: true  # Container now sees host's PID namespace
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      operator: Exists
      effect: NoSchedule
  containers:
    - name: node-telemetry-agent
      volumeMounts:
        - name: proc
          mountPath: /host/proc  # Now sees HOST's processes
```

`hostPID: true` disables namespace isolation, so the container sees the host's full process table. Combined with the `hostPath` mounts, the agent now has true host visibility.

---

## Key Insights

1. **Selector/label mismatches are obvious failures** — DaemonSet 0/0 is immediately visible, so you catch this quickly.
2. **Tolerations and namespacing are silent failures** — pods run fine, everything looks correct, but you're missing data or seeing wrong data. These require careful reasoning to catch.
3. **Namespace isolation is orthogonal to volume mounts** — giving a container access to host files (`hostPath`) doesn't automatically give it host visibility; you need to disable namespace isolation (`hostPID`, `hostNetwork`) depending on what you're measuring.
4. **Test your declared intent** — the comment says "EVERY node including control-plane" — that's your spec to verify against. If the implementation doesn't match the comment, it's a bug.