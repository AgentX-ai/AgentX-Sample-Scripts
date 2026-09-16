"""
NVIDIA NIM demo: trace an OpenAI-compatible NIM endpoint with framework="nvidia-nim".

NIM serves models behind the OpenAI chat-completions API, so the patched client is the
ordinary `openai` client pointed at a NIM base URL. `patch_nim_client` stamps every call
`framework="nvidia-nim"`, so after running, open Observe > Live Traces and the trace's
framework chip reads "nvidia-nim" (and Monitor's Platforms chart gets its own NIM row).

Endpoint selection (first match wins):
  1. NIM_BASE_URL + NIM_API_KEY   - your NIM: a local container (http://localhost:8000/v1)
                                    or NVIDIA's hosted API (https://integrate.api.nvidia.com/v1
                                    with an NVIDIA_API_KEY / NGC key).
  2. OPENAI_API_KEY               - stand-in so the demo runs without a GPU box: the wire
                                    protocol is identical, only the base URL differs. The
                                    trace still demonstrates the "nvidia-nim" labeling.

Runs in the API key's own project (no throwaway project).

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 nvidia_nim_demo.py
"""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

from agentx import AgentX
from agentx.integrations.nvidia_nim import patch_nim_client

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
client = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
client.ping()

nim_base = os.getenv("NIM_BASE_URL")
if nim_base:
    llm = OpenAI(base_url=nim_base, api_key=os.getenv("NIM_API_KEY", "not-needed-for-local-nim"))
    model = os.getenv("NIM_MODEL", "meta/llama-3.1-8b-instruct")
    print(f"Using NIM endpoint {nim_base} with model {model}")
elif os.getenv("OPENAI_API_KEY"):
    llm = OpenAI()
    model = "gpt-4o-mini"
    print("NIM_BASE_URL not set - using OpenAI as an OpenAI-compatible stand-in endpoint")
else:
    sys.exit("Set NIM_BASE_URL (+ NIM_API_KEY) or OPENAI_API_KEY to run this demo")

patch_nim_client(llm, client.tracer, name="nim-demo-agent")

# 1. A standalone call: becomes its own root trace, span kind "llm", framework "nvidia-nim".
reply = llm.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "In one sentence: what is an inference microservice?"}],
)
print("standalone reply:", reply.choices[0].message.content)

# 2. The same patched client inside an agent span: the call folds into the trace as a real
#    child LLM span and stamps its framework/model/tokens onto the parent.
with client.tracer.trace(
    "nim-support-agent",
    input={"query": "Summarize why we self-host inference."},
    sync=True,
    span_kind="agent",
) as span:
    reply = llm.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "One sentence on why a team self-hosts inference."}],
    )
    span.output = reply.choices[0].message.content

# trace_id is populated on span exit (sync=True sends inside __exit__), so read it after.
trace_id = span.trace_id

client.tracer.flush(timeout=10)
print("agent reply:", reply.choices[0].message.content)
print(f"trace id: {trace_id} - check Observe > Live Traces for framework 'nvidia-nim'")
