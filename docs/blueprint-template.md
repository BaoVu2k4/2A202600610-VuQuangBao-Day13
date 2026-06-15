# Day 13 Observability Lab Report

> **Instruction**: Fill in all sections below. This report is designed to be parsed by an automated grading assistant. Ensure all tags (e.g., `[GROUP_NAME]`) are preserved.

## 1. Team Metadata
- [GROUP_NAME]: Vũ Quang Bảo (làm cá nhân toàn bộ)
- [REPO_URL]: <điền link repo của bạn>
- [MEMBERS]:
  - Member A: Vũ Quang Bảo | Role: Logging & PII
  - Member B: Vũ Quang Bảo | Role: Tracing & Enrichment
  - Member C: Vũ Quang Bảo | Role: SLO & Alerts
  - Member D: Vũ Quang Bảo | Role: Load Test & Dashboard
  - Member E: Vũ Quang Bảo | Role: Demo & Report

> Ghi chú: Sinh viên thực hiện **toàn bộ các phần** một mình (không chia nhóm). Mã sinh viên: 2A202600610.

---

## 2. Group Performance (Auto-Verified)
- [VALIDATE_LOGS_FINAL_SCORE]: 100/100
- [TOTAL_TRACES_COUNT]: 20 (≥10 yêu cầu)
- [PII_LEAKS_FOUND]: 0

> Kết quả `python scripts/validate_logs.py`: PASSED cả 4 hạng mục (Basic JSON schema, Correlation ID propagation, Log enrichment, PII scrubbing). 0 PII leak, 11+ correlation ID duy nhất.

---

## 3. Technical Evidence (Group)

### 3.1 Logging & Tracing
- [EVIDENCE_CORRELATION_ID_SCREENSHOT]: docs/evidence-correlation-id.png
- [EVIDENCE_PII_REDACTION_SCREENSHOT]: docs/evidence-pii-redaction.png
- [EVIDENCE_TRACE_WATERFALL_SCREENSHOT]: docs/evidence-trace-waterfall.png
- [EVIDENCE_TRACES_LIST_SCREENSHOT]: docs/evidence-traces-list.png
- [TRACE_WATERFALL_EXPLANATION]: Trace gốc là span `run` (decorator `@observe()` trên `LabAgent.run`). Trong span này, agent gọi `retrieve()` (RAG) rồi `FakeLLM.generate()` (LLM). Mỗi trace được gắn `user_id` (đã hash SHA-256, 12 ký tự), `session_id`, và tags `["lab", <feature>, <model>]`. Metadata observation chứa `doc_count`, `query_preview` (đã scrub PII) và `usage_details` (input/output tokens). Khi bật incident `rag_slow`, span `run` kéo dài tới ~2.6s do `time.sleep(2.5)` trong bước retrieval — đây là cách dùng trace để khoanh vùng (localize) thành phần chậm.

**Cách hoạt động của pipeline logging (giải thích cho phần demo):**
1. `CorrelationIdMiddleware` chạy đầu tiên cho mỗi request: `clear_contextvars()` (chống rò rỉ giữa request) → sinh/đọc `correlation_id` dạng `req-<8 hex>` → `bind_contextvars(correlation_id=...)` → gắn header `x-request-id` + `x-response-time-ms` vào response.
2. Endpoint `/chat` gọi `bind_contextvars(...)` để enrich mọi log của request với `user_id_hash`, `session_id`, `feature`, `model`, `env`.
3. structlog chạy chuỗi processor: `merge_contextvars` → `add_log_level` → `TimeStamper(ts)` → **`scrub_event` (redact PII)** → ghi JSONL ra `data/logs.jsonl` → `JSONRenderer`.

### 3.2 Dashboard & SLOs
- [DASHBOARD_6_PANELS_SCREENSHOT]: docs/evidence-dashboard.png
- Dashboard tự xây bằng Chart.js, app phục vụ tại **http://127.0.0.1:8000/dashboard** (cùng origin với `/metrics`, không lỗi CORS). Auto-refresh 15s, có đường SLO (nét đứt đỏ), đơn vị rõ ràng. 6 panel: (1) Latency P50/P95/P99, (2) Traffic + QPS, (3) Error rate + breakdown, (4) Cost over time, (5) Tokens in/out, (6) Quality proxy.
- [SLO_TABLE]:
| SLI | Target | Window | Current Value (đo thật) |
|---|---:|---|---:|
| Latency P95 | < 3000ms | 28d | 150ms (bình thường) / 2651ms (khi rag_slow) |
| Error Rate | < 2% | 28d | 0% (bình thường) / 100% (khi tool_fail) |
| Cost Budget | < $2.5/day | 1d | ~$0.002/req (bình thường) / ~$0.004/req (khi cost_spike) |
| Quality avg | ≥ 0.75 | 28d | 0.88 |

### 3.3 Alerts & Runbook
- [ALERT_RULES_SCREENSHOT]: docs/evidence-alerts.png
- [SAMPLE_RUNBOOK_LINK]: docs/alerts.md#1-high-latency-p95
- 3 alert rules đã cấu hình trong `config/alert_rules.yaml`, mỗi rule có severity, condition, owner và link runbook tới `docs/alerts.md`:
  - `high_latency_p95` (P2): `latency_p95_ms > 5000 for 30m`
  - `high_error_rate` (P1): `error_rate_pct > 5 for 5m`
  - `cost_budget_spike` (P2): `hourly_cost_usd > 2x_baseline for 15m`

---

## 4. Incident Response (Group)

> Đã inject thử cả 3 kịch bản (`scripts/inject_incident.py`). Dưới đây phân tích chi tiết kịch bản chính + tóm tắt 2 kịch bản còn lại.

- [SCENARIO_NAME]: tool_fail (kèm cost_spike và rag_slow)
- [SYMPTOMS_OBSERVED]:
  - **tool_fail**: Panel Error rate trên dashboard nhảy lên 100%, badge chuyển `⚠ SLO breach`. `/metrics` hiện `error_breakdown: {"RuntimeError": 10}`. Traffic không tăng (request lỗi không được tính `record_request`).
  - **cost_spike**: Panel Cost & Tokens vọt lên. `tokens_out` ~530/req (so với ~248/req baseline) = **4×**; `avg_cost_usd` tăng `0.002 → 0.004`.
  - **rag_slow**: Panel Latency vọt. `latency_ms` báo về **2651ms** (so với 150ms baseline ≈ **17×**), wall-time ~3011ms.
- [ROOT_CAUSE_PROVED_BY]:
  - **tool_fail** → Log line: `{"event":"request_failed","error_type":"RuntimeError","payload":{"detail":"Vector store timeout"},"correlation_id":"req-17c9f0c6",...}`. Root cause: `mock_rag.retrieve()` ném `RuntimeError("Vector store timeout")` khi cờ `tool_fail` bật → vector store không phản hồi.
  - **rag_slow** → Trace span `run` kéo dài ~2.6s; `reported_latency_ms=2651`. Root cause: `time.sleep(2.5)` trong bước retrieval (`mock_rag.py`) → RAG là thành phần chậm, không phải LLM.
  - **cost_spike** → `usage_details.output` trong trace + metric `tokens_out_total`. Root cause: `mock_llm.generate()` nhân 4× `output_tokens` khi cờ `cost_spike` bật → chi phí tăng theo output tokens (giá $15/1M output vs $3/1M input).
- [FLOW_METRICS_TRACES_LOGS]: Quy trình debug 3 lớp:
  1. **Metrics** (`/metrics` + dashboard) phát hiện *có* sự cố: error rate cao / latency P95 cao / cost cao.
  2. **Traces** (Langfuse) khoanh vùng *ở đâu*: so sánh span RAG vs span LLM để biết thành phần nào chậm/đắt.
  3. **Logs** (`data/logs.jsonl`) giải thích *tại sao*: lọc theo `error_type`, đọc `detail` và dùng `correlation_id` để truy vết đúng request.
- [FIX_ACTION]: Tắt cờ incident (`python scripts/inject_incident.py --scenario <name> --disable`). Trong thực tế: rollback thay đổi gây lỗi, retry với fallback model / fallback retrieval source, hoặc giảm prompt size.
- [PREVENTIVE_MEASURE]:
  - Thêm timeout + circuit breaker cho vector store (tránh `tool_fail` lan rộng).
  - Đặt alert `high_latency_p95` và `cost_budget_spike` để phát hiện sớm.
  - Giới hạn `max_tokens` đầu ra và route câu hỏi dễ sang model rẻ hơn (chống `cost_spike`).

---

## 5. Individual Contributions & Evidence

### Vũ Quang Bảo (2A202600610) — thực hiện toàn bộ
- [TASKS_COMPLETED]:
  - **Logging & PII**: hoàn thành `CorrelationIdMiddleware` (correlation ID `req-<8hex>`, clear/bind contextvars, response headers); enrich log trong `/chat`; đăng ký processor `scrub_event`; thêm regex PII (email, phone VN, CCCD, credit card, **passport**, **địa chỉ VN**).
  - **Tracing & Enrichment**: sửa `tracing.py` tương thích **Langfuse v3** (`from langfuse import observe, get_client`, shim `langfuse_context`), thêm `load_dotenv()` để nạp key; tạo 20 traces có metadata đầy đủ.
  - **SLO & Alerts**: rà soát `config/slo.yaml`, `config/alert_rules.yaml`, `docs/alerts.md` (3 alert + runbook).
  - **Load Test & Dashboard**: chạy `load_test.py --concurrency 5`; tự xây dashboard 6 panel (`app/dashboard.html` + route `/dashboard`).
  - **Incident & Report**: inject cả 3 incident, phân tích root cause Metrics→Traces→Logs; viết báo cáo này.
- [EVIDENCE_LINK]: <link tới commit/PR của bạn — xem mục Git>
- [VALIDATE_SCORE]: 100/100
- [TESTS]: `pytest` → 2 passed

---

## 6. Bonus Items (Optional)
- [BONUS_COST_OPTIMIZATION]: Đã định lượng cost_spike (before/after: tokens_out 248→530/req, avg_cost $0.002→$0.004). Đề xuất tối ưu: giới hạn `max_tokens` + route model rẻ → có thể đo lại sau khi áp dụng.
- [BONUS_AUDIT_LOGS]: Cấu hình sẵn `AUDIT_LOG_PATH=data/audit.jsonl` trong `.env` (có thể tách audit log riêng nếu cần).
- [BONUS_CUSTOM_METRIC]: Dashboard tự động (auto-instrumentation client-side: tự poll `/metrics`, tính QPS từ delta traffic, vẽ đường SLO động) — `app/dashboard.html`.
