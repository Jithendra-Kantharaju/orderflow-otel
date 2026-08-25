# Written answers

Copy this file to `ANSWERS.md`, fill it in, and include it in your submission.
A few sentences per question is plenty — we're reading for how you think,
not length.

## 1. Cardinality

Suppose a teammate adds `user_id` as a label on a Prometheus counter that
tracks API requests, on a service with 2 million users. What happens to
that metric over the life of the service, and what would you suggest
instead if per-user request counts are genuinely needed?

## 2. p99 vs. average

A dashboard shows average request latency holding steady at 80ms, but
users are filing tickets about the app "randomly" feeling slow. What
might the average be hiding, and what would you look at instead?

## 3. Streaming / backpressure

You're exporting spans from a fleet of services to a Collector gateway
over OTLP, and the gateway's downstream exporter (say, a tracing backend)
starts throttling requests. Walk through what should happen next in the
pipeline, and what could go wrong if buffering and retry aren't handled
carefully.

## 4. Instrumenting agent workflows

`/agent/analyze` runs a plan -> tool_call -> model_inference pipeline —
similar in shape to real agentic systems that call external tools and
LLMs. If you were designing the instrumentation approach for a whole
codebase full of workflows like this (not just one endpoint), what would
you standardize so that every workflow's cost and latency show up
consistently, without every engineer hand-rolling their own spans?

## 5. Kubernetes DaemonSet

`k8s/daemonset-broken.yaml` deploys a node-level telemetry agent as a
DaemonSet. It has real problems — at least one that stops it from
scheduling at all, and at least two more that are silent (it runs, it
looks healthy, and it's still wrong). Fix the manifest, then list what
you found and why each one matters. You don't need a running cluster
for this.

## 6. Bare-metal / BMC

`bmc/bmc_thermal_task.py` and `bmc/redfish_thermal_sample.json` are a
small Redfish `Thermal` payload and a stub to parse it. Implement
`parse_redfish_thermal()`, then answer: why is server hardware telemetry
like this typically pulled out-of-band via Redfish/IPMI rather than from
an agent running on the host OS, and when would you expect to fall back
from Redfish to IPMI on real hardware?

## 7. Linux fundamentals across a mixed fleet

Say the same exporter needs to run as a native systemd service (not a
container) across a fleet that's a mix of CentOS and Ubuntu hosts —
same unit file, same binary. Name two concrete differences between the
two distros that could make that exact setup behave differently, or
fail silently, on one but not the other.

## 8. Diagnosing a slow node

You SSH into a bare-metal node that's reportedly "slow," and there's no
monitoring stack installed yet — no Prometheus, no exporters, nothing.
What are the first 3-4 native Linux commands you'd reach for, and what
would each one tell you?
