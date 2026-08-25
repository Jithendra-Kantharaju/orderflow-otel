"""
orderflow — a tiny service with two kinds of workload:

  1. A traditional REST endpoint (`/orders`) that calls a simulated
     downstream payment service.
  2. A small "agentic" endpoint (`/agent/analyze`) that runs a
     plan -> tool_call -> model_inference pipeline, similar in shape
     to how Canyon Code instruments agent workflows and tool calls.

Your job (see the assignment doc) is to instrument this service with
OpenTelemetry — traces AND metrics — following the TODOs below.
Do not change the business logic; only add instrumentation.

Run it with:
    uvicorn app:app --reload --port 8000
"""

import random
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="orderflow")

executor = ThreadPoolExecutor(max_workers=4)

# ---------------------------------------------------------------------------
# TODO(1): Set up the OpenTelemetry SDK here.
#   - Create a TracerProvider and a MeterProvider.
#   - Export both via OTLP gRPC to the Collector (default localhost:4317).
#   - Create one counter (e.g. requests processed) and one histogram
#     (e.g. request duration or downstream-call duration).
#   - Keep metric attributes LOW cardinality — see the assignment doc
#     for what counts as a red flag here.
# ---------------------------------------------------------------------------
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

from opentelemetry.sdk.resources import Resource

# Optional but good practice: tag every span/metric with a service name
resource = Resource.create({"service.name": "SOME_SERVICE_NAME"})

# --- traces ---
span_exporter = OTLPSpanExporter(endpoint="localhost:4317", insecure=True)
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer(__name__)

# --- metrics ---
metric_exporter = OTLPMetricExporter(endpoint="localhost:4317", insecure=True)
reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=5000)
meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter(__name__)

# --- one counter, one histogram ---
some_counter = meter.create_counter("SOME_NAME", description="...")
some_histogram = meter.create_histogram("SOME_NAME", description="...", unit="ms")

class OrderRequest(BaseModel):
    item: str
    qty: int


class AgentRequest(BaseModel):
    query: str


def call_payment_service(order_id: str, amount: float) -> dict:
    """Simulated downstream call. ~10% chance of failure."""
    time.sleep(random.uniform(0.05, 0.25))
    if random.random() < 0.10:
        raise RuntimeError("payment_service: timeout")
    return {"order_id": order_id, "amount": amount, "status": "charged"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orders")
def create_order(req: OrderRequest):
    # TODO(2): Wrap this handler in a span. Add a CHILD span around the
    # call to call_payment_service that captures it as a distinct step
    # in the trace. Record order attributes as SPAN attributes (not
    # metric labels) — item name and qty are fine here.
    order_id = f"ord_{random.randint(1000, 9999)}"
    amount = round(req.qty * random.uniform(5, 50), 2)

    try:
        result = call_payment_service(order_id, amount)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"order_id": order_id, "item": req.item, "qty": req.qty, "payment": result}


def run_tool_call(query: str) -> dict:
    """Simulated external tool invocation (e.g. a lookup/search tool)."""
    time.sleep(random.uniform(0.1, 0.4))
    return {"tool": "lookup", "query": query, "result": f"data-for-{query}"}


def run_model_inference(context: dict) -> dict:
    """Simulated LLM call. Reports fake token usage, like a real
    provider response would."""
    time.sleep(random.uniform(0.3, 1.0))
    input_tokens = random.randint(200, 800)
    output_tokens = random.randint(50, 300)
    return {
        "answer": f"synthesized answer using {context['result']}",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": "sim-model-large",
    }


@app.post("/agent/analyze")
def agent_analyze(req: AgentRequest):
    # TODO(3): This is the interesting one. Instrument this as a
    # WORKFLOW made of distinct steps (plan -> tool_call -> model_inference),
    # each as its own span/sub-span, with the tool_call step running on a
    # background thread via `executor` (already wired up below).
    #
    # Make sure trace context survives the hop onto the thread pool —
    # by default it will NOT propagate automatically. Record token counts
    # from run_model_inference as span attributes (and/or a metric) —
    # this is the same kind of signal Canyon Code uses to attribute
    # cost to individual agent runs.
    plan = {"steps": ["tool_call", "model_inference"], "query": req.query}

    future = executor.submit(run_tool_call, req.query)
    tool_result = future.result()

    model_result = run_model_inference(tool_result)

    return {
        "query": req.query,
        "plan": plan,
        "tool_result": tool_result,
        "answer": model_result["answer"],
        "usage": {
            "input_tokens": model_result["input_tokens"],
            "output_tokens": model_result["output_tokens"],
        },
    }
