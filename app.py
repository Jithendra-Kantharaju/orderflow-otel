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

from opentelemetry import context as otel_context

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
resource = Resource.create({"service.name": "orderflow"})

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
requests_counter = meter.create_counter("orderflow.requests.total", description="Total requests processed")
call_duration_histogram = meter.create_histogram("orderflow.call.duration", description="Duration of downstream/step calls", unit="ms")

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
    with tracer.start_as_current_span("create_order") as span:
        span.set_attribute("order.item", req.item)
        span.set_attribute("order.qty", req.qty)

        order_id = f"ord_{random.randint(1000, 9999)}"
        amount = round(req.qty * random.uniform(5, 50), 2)

        try:
            start_time = time.time()
            with tracer.start_as_current_span("call_payment_service") as payment_span:
                payment_span.set_attribute("order.id", order_id)
                result = call_payment_service(order_id, amount)
            elapsed_ms = (time.time() - start_time) * 1000
            call_duration_histogram.record(elapsed_ms, {"endpoint": "orders"})
        except RuntimeError as e:
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            requests_counter.add(1, {"endpoint": "orders", "status": "error"})
            raise HTTPException(status_code=502, detail=str(e))

        requests_counter.add(1, {"endpoint": "orders", "status": "success"})
        return {"order_id": order_id, "item": req.item, "qty": req.qty, "payment": result}


def run_tool_call_with_context(query: str, ctx):
    token = otel_context.attach(ctx)
    try:
        with tracer.start_as_current_span("tool_call") as span:
            span.set_attribute("tool.query", query)
            return run_tool_call(query)
    finally:
        otel_context.detach(token)


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
    with tracer.start_as_current_span("agent_analyze") as workflow_span:
        workflow_span.set_attribute("agent.workflow", "plan_tool_model")
        
        with tracer.start_as_current_span("plan") as plan_span:
            plan = {"steps": ["tool_call", "model_inference"], "query": req.query}
            plan_span.set_attribute("agent.query", req.query)

        current_ctx = otel_context.get_current()
        start_time = time.time()
        future = executor.submit(run_tool_call_with_context, req.query, current_ctx)
        tool_result = future.result()
        tool_elapsed_ms = (time.time() - start_time) * 1000
        call_duration_histogram.record(tool_elapsed_ms, {"endpoint": "agent_analyze"})

        start_time = time.time()
        with tracer.start_as_current_span("model_inference") as inference_span:
            model_result = run_model_inference(tool_result)
            inference_span.set_attribute("llm.input_tokens", model_result["input_tokens"])
            inference_span.set_attribute("llm.output_tokens", model_result["output_tokens"])
            inference_span.set_attribute("llm.model", model_result["model"])
        inference_elapsed_ms = (time.time() - start_time) * 1000
        call_duration_histogram.record(inference_elapsed_ms, {"endpoint": "agent_analyze"})

        requests_counter.add(1, {"endpoint": "agent_analyze", "status": "success"})
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