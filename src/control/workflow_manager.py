import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 按需补库：显式 ID 不在库中时触发；context 过短作为 mock/空库回退
# （LightRAG hybrid 在非空图上几乎总是返回 30k+ 松散匹配，长度阈值不可靠）
LAZY_INGEST_MIN_CONTEXT = int(os.environ.get("LAZY_INGEST_MIN_CONTEXT", "300"))
# 单次提问最多自动入库条数（控延迟与体积）
LAZY_INGEST_MAX_ASSETS = int(os.environ.get("LAZY_INGEST_MAX_ASSETS", "3"))
# 试点范围：暂不含 GEO（搜索噪声大）
LAZY_INGEST_SOURCES = ("kegg", "uniprot")

# KEGG: map04115 / hsa04115 / ko04115
_KEGG_ID_RE = re.compile(r"\b(?:map|hsa|ko)\d{5}\b", re.IGNORECASE)
# UniProt accession: P04637 / Q9Y2B4 / A0A0B4J2F0（6 或 10/15 位）
_UNIPROT_ACC_RE = re.compile(
    r"\b(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})\b"
)


class WorkflowManager:
    """流程管理器，协调各个组件"""

    def __init__(
        self,
        intent_parser,
        knowledge_client,
        r_executor,
        visualizer,
        fetcher_registry=None,
        storage=None,
        knowledge_builder=None,
        r_script_generator=None,
        data_loader=None,
        methods_kb=None,
        explainer=None,
        lineage_path: str = "data/lineage.jsonl",
        code_repairer=None,
        max_repair_attempts: int = 2,
        require_script_confirmation: bool = False,
    ):
        self.intent_parser = intent_parser
        self.knowledge_client = knowledge_client
        self.r_executor = r_executor
        self.visualizer = visualizer
        self.fetcher_registry = fetcher_registry
        self.storage = storage
        self.knowledge_builder = knowledge_builder
        self.r_script_generator = r_script_generator
        self.data_loader = data_loader
        self.methods_kb = methods_kb
        self.explainer = explainer
        self.lineage_path = lineage_path
        self.code_repairer = code_repairer
        self.max_repair_attempts = max_repair_attempts
        self.require_script_confirmation = require_script_confirmation

    def execute_workflow(
        self, user_input: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """执行工作流"""
        context = context if context is not None else {}

        # 1. 解析意图
        intent = self.intent_parser.parse(user_input, context)
        params = self.intent_parser.extract_parameters(user_input)

        logger.info(f"Parsed intent: {intent}")

        # 2. 根据意图执行相应工作流
        if intent["type"] == "analysis":
            return self._execute_analysis_workflow(intent, params, context)
        elif intent["type"] == "knowledge_query":
            return self._execute_knowledge_workflow(intent, params, context)
        elif intent["type"] == "fetch_data":
            return self._execute_fetch_data_workflow(intent, params, context)
        else:
            return self._execute_general_workflow(intent, params, context)

    def _resolve_input_file(self, params: dict[str, Any], context: dict[str, Any]) -> str | None:
        """解析输入文件：优先 params.input_files，其次 context.downloaded_assets"""
        if params.get("input_files"):
            candidate = params["input_files"][0]
            if Path(candidate).exists():
                return candidate
        if context.get("downloaded_assets"):
            candidate = context["downloaded_assets"][-1].get("access_path")
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _generate_or_finish(
        self,
        analysis_type: str,
        run_params: dict[str, Any],
        method_context: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """统一生成代码并决定是否需要确认或直接执行"""
        code = self.r_script_generator.generate_code(
            analysis_type, run_params, method_context=method_context,
        )
        if self.require_script_confirmation and not context.get("script_approved"):
            return {
                "status": "needs_script_confirmation",
                "type": "analysis",
                "analysis_type": analysis_type,
                "script": code,
                "params": run_params,
                "method_context": method_context,
                "message": "已生成 R 脚本，请审阅并确认执行",
            }
        return self._finish_analysis(
            analysis_type, run_params, code, method_context, context,
        )

    def _execute_analysis_workflow(
        self, intent: dict[str, Any], params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """执行分析工作流"""
        analysis_type = intent.get("analysis_type")

        if analysis_type == "differential_expression":
            return self._execute_de_analysis(params, context)
        if analysis_type == "single_cell":
            return self._execute_sc_analysis(params, context)
        if analysis_type == "spatial":
            return self._execute_spatial_analysis(params, context)

        return {
            "status": "success",
            "analysis_type": analysis_type,
            "message": f"已开始执行 {analysis_type} 分析",
            "results": {},
        }

    def _method_context_for(
        self, analysis_type: str, params: dict[str, Any]
    ) -> str | None:
        """生成代码前查方法学知识库；无库/查询失败返回 None"""
        if self.methods_kb is None:
            return None
        question = f"{analysis_type} 分析 方法 适用 参数 常见坑"
        extra = params.get("input_file") or ""
        if extra:
            question = f"{question} {extra}"
        ctx = self.methods_kb.query_context(question)
        return ctx or None

    @staticmethod
    def _de_output_stats(output_file: str) -> dict[str, Any]:
        """读取 DE 输出 CSV，统计总基因数与 padj<0.05 显著数；失败返回 0/0"""
        import csv

        total = 0
        sig = 0
        try:
            with open(output_file, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    total += 1
                    raw = row.get("padj") or row.get("pvalue") or ""
                    try:
                        p = float(raw)
                    except ValueError:
                        continue
                    if p < 0.05:
                        sig += 1
        except OSError as e:
            logger.warning("read de output for stats failed: %s", e)
        return {"total_genes": total, "significant_genes": sig}

    def _execute_de_analysis(
        self, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """差异表达分析：优先使用已确认下载的 GEO 数据"""
        input_file = None
        # 优先使用已下载的资产（context 传入），其次才用参数里的文件
        if context.get("downloaded_assets"):
            input_file = context["downloaded_assets"][-1].get("access_path")
        elif params.get("input_files"):
            input_file = params["input_files"][0]

        # 如果没有数据文件，返回占位成功（用于测试/演示）
        if not input_file:
            return {
                "status": "success",
                "analysis_type": "differential_expression",
                "message": "差异表达分析完成（演示模式，无实际数据）",
                "results": {},
            }

        output_file = str(Path(input_file).with_suffix(".de_results.csv"))
        method_context = self._method_context_for("differential_expression", params)
        code = self.r_script_generator.generate_code(
            "differential_expression",
            {"input_file": input_file, "output_file": output_file},
            method_context=method_context,
        )

        # HITL: 需要脚本确认且未通过 context 批准
        if self.require_script_confirmation and not context.get("script_approved"):
            return {
                "status": "needs_script_confirmation",
                "analysis_type": "differential_expression",
                "script": code,
                "params": {"input_file": input_file, "output_file": output_file},
                "method_context": method_context,
            }

        return self._finish_analysis(
            "differential_expression",
            {"input_file": input_file, "output_file": output_file},
            code,
            method_context,
            context,
        )

    def _execute_sc_analysis(self, params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """单细胞分析：聚类、标记基因"""
        input_file = self._resolve_input_file(params, context)
        if not input_file:
            # 无数据：生成示例脚本进入确认流程，让用户审阅或取消（冒烟/演示路径）
            run_params = {
                "input_file": "scrna_matrix.csv",
                "output_file": "sc_clusters.csv",
                "marker_file": "sc_markers.csv",
            }
            method_context = self._method_context_for("single_cell", params)
            code = self.r_script_generator.generate_code(
                "single_cell", run_params, method_context=method_context,
            )
            return {
                "status": "needs_script_confirmation",
                "analysis_type": "single_cell",
                "script": code,
                "params": run_params,
                "method_context": method_context,
                "message": "未检测到数据文件，已生成示例 Seurat 脚本（占位输入），请确认或取消",
            }
        out = str(Path(input_file).with_suffix(".sc_clusters.csv"))
        marker = str(Path(input_file).with_suffix(".sc_markers.csv"))
        run_params = {
            "input_file": input_file,
            "output_file": out,
            "marker_file": marker,
        }
        method_context = self._method_context_for("single_cell", params)
        return self._generate_or_finish("single_cell", run_params, method_context, context)

    def _execute_spatial_analysis(self, params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """空间转录组分析：聚类、空间图"""
        input_file = self._resolve_input_file(params, context)
        if not input_file:
            # 无数据：生成示例脚本进入确认流程，让用户审阅或取消（冒烟/演示路径）
            run_params = {
                "input_file": "visium_data",
                "output_file": "spatial_clusters.csv",
                "plot_file": "spatial_plot.png",
            }
            method_context = self._method_context_for("spatial", params)
            code = self.r_script_generator.generate_code(
                "spatial", run_params, method_context=method_context,
            )
            return {
                "status": "needs_script_confirmation",
                "analysis_type": "spatial",
                "script": code,
                "params": run_params,
                "method_context": method_context,
                "message": "未检测到数据文件，已生成示例 Visium 脚本（占位输入），请确认或取消",
            }
        base = Path(input_file)
        if base.is_dir():
            run_params = {
                "input_file": input_file,
                "output_file": str(base / ".spatial_clusters.csv"),
                "plot_file": str(base / ".spatial_plot.png"),
            }
        else:
            run_params = {
                "input_file": input_file,
                "output_file": str(base.with_suffix(".spatial_clusters.csv")),
                "plot_file": str(base.with_suffix(".spatial_plot.png")),
            }
        method_context = self._method_context_for("spatial", params)
        return self._generate_or_finish("spatial", run_params, method_context, context)

    def _finish_analysis(
        self,
        analysis_type: str,
        params: dict[str, Any],
        code: str,
        method_context: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行分析的核心逻辑：repair 循环、结果处理、知识库记录"""
        from src.analysis.r_executor import RExecutorError

        input_file = params["input_file"]
        output_file = params["output_file"]

        attempt = 0
        current_code = code
        repair_count = 0
        last_err: Exception | None = None
        result = None
        while attempt <= self.max_repair_attempts:
            try:
                result = self.r_executor.execute_code(current_code)
                last_err = None
                break
            except RExecutorError as e:
                last_err = e
                if self.code_repairer is None or attempt >= self.max_repair_attempts:
                    break
                fixed = self.code_repairer.repair(current_code, str(e))
                if not fixed:
                    break
                current_code = fixed
                repair_count += 1
            attempt += 1

        if result is None or last_err is not None:
            return {
                'status': 'error',
                'analysis_type': analysis_type,
                'message': f'{self._analysis_type_zh(analysis_type)}分析失败: {last_err}',
                'results': {'repair_count': repair_count},
            }
        logger.info(
            "%s analysis finished on %s (returncode=%s)", analysis_type, input_file, result.returncode
        )
        if result.returncode == 0:
            self._record_analysis_to_kb(analysis_type, input_file, output_file)
        explanation = None
        if self.explainer is not None:
            try:
                stats = self._de_output_stats(output_file)
                explanation = self.explainer.generate_llm_explanation(
                    {"input_file": input_file, "output_file": output_file,
                     "analysis_type": analysis_type, **stats},
                    question=params.get("question", f"{self._analysis_type_zh(analysis_type)}分析结果说明"),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("explanation failed: %s", e)
        capsule_dir = None
        try:
            from src.analysis.capsule import export_analysis_capsule
            capsule_dir = str(export_analysis_capsule(
                question=params.get("question", ""),
                intent={"type": "analysis", "analysis_type": analysis_type},
                params={"input_file": input_file, "output_file": output_file},
                script_code=current_code,
                results={"returncode": result.returncode, "output_file": output_file},
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("capsule export failed: %s", e)
        return {
            "status": "success",
            "analysis_type": analysis_type,
            "message": f"{self._analysis_type_zh(analysis_type)}分析完成",
            "method_context": method_context,
            "results": {"returncode": result.returncode, "output_file": output_file,
                        "repair_count": repair_count},
            "repair_count": repair_count,
            "explanation": explanation,
            "capsule_dir": capsule_dir,
        }

    def _analysis_type_zh(self, analysis_type: str) -> str:
        """分析类型中文映射"""
        return {
            "differential_expression": "差异表达",
            "single_cell": "单细胞",
            "spatial": "空间转录组",
        }.get(analysis_type, analysis_type)

    def execute_confirmed_script(
        self,
        analysis_type: str,
        params: dict[str, Any],
        script: str,
        method_context: str | None = None,
    ) -> dict[str, Any]:
        """用户确认脚本后执行（含 repair 循环）"""
        if analysis_type not in ("differential_expression", "single_cell", "spatial"):
            return {
                "status": "error",
                "analysis_type": analysis_type,
                "message": f"暂不支持确认执行的分析类型: {analysis_type}",
            }
        return self._finish_analysis(
            analysis_type, params, script, method_context, {"script_approved": True},
        )

    def _record_analysis_to_kb(self, analysis_type: str, input_file: str,
                               output_file: str, user_question: str = "") -> None:
        """分析成功后写回主知识库（闭环记忆）；失败仅告警"""
        if self.knowledge_builder is None:
            return
        zh = {
            "differential_expression": "差异表达",
            "single_cell": "单细胞聚类",
            "spatial": "空间转录组",
        }.get(analysis_type, analysis_type)
        text = (
            "# 分析实验记录\n"
            f"类型：{zh}（{analysis_type}）\n"
            f"输入：{input_file}\n"
            f"输出：{output_file}\n"
            f"触发问题：{user_question}\n"
            "状态：执行成功（returncode=0）\n"
        )
        try:
            self.knowledge_builder.build_from_text(text)
        except Exception as e:  # noqa: BLE001 - 写回失败不影响分析结果返回
            logger.warning("analysis summary writeback failed: %s", e)

    def _execute_knowledge_workflow(
        self, intent: dict[str, Any], params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """执行知识查询工作流；本地库缺目标 ID 时按需从 KEGG/UniProt 补库再答"""
        query = intent.get("original_input", "")

        lazy_ingested: list[dict[str, Any]] = []
        if self._should_lazy_ingest(query, params):
            lazy_ingested = self._lazy_ingest_missing(query, params)
            if lazy_ingested:
                context["lazy_ingested"] = lazy_ingested

        # 结构化引用优先；旧 mock 只有 query() 时回退纯文本
        references: list[dict[str, Any]] = []
        if hasattr(self.knowledge_client, "query_with_references"):
            payload = self.knowledge_client.query_with_references(query)
            knowledge_result = str(payload.get("response") or "")
            references = list(payload.get("references") or [])
        else:
            from src.knowledge.lightrag_client import strip_references_section

            knowledge_result = strip_references_section(
                str(self.knowledge_client.query(query) or "")
            )

        return {
            "status": "success",
            "type": "knowledge_response",
            "query": query,
            "response": knowledge_result,
            "references": references,
            "lazy_ingested": lazy_ingested,
        }

    def _should_lazy_ingest(self, query: str, params: dict[str, Any]) -> bool:
        """是否触发按需补库。

        能读到知识库时：
        - 显式 KEGG/UniProt ID：按「专属文档头」判断（`# KEGG 通路: id` / `# UniProt 蛋白: id`）；
          实体表里的 map 号常是其它通路的交叉引用，不能当作已入库。
        - genes：按实体名判断（基因符号通常以实体出现）。
        - 显式标识均已命中 → 不补（避免重复入库）。
        读不到库路径（mock）或无显式标识：回退 context 长度阈值。
        """
        explicit = self._extract_explicit_ids(query)
        genes = [str(g) for g in (params.get("genes") or [])]

        asset_ids = self._kb_asset_ids()
        entities = self._kb_entity_names()
        if asset_ids is not None and entities is not None:
            if any(aid.lower() not in asset_ids for _src, aid in explicit):
                return True
            if any(g.lower() not in entities for g in genes):
                return True
            # 显式 ID / genes 均已命中，不因 context 短而重复入库
            if explicit or genes:
                return False

        ctx = self._context_for_miss_check(query)
        if ctx is None:
            return False
        return len(ctx.strip()) < LAZY_INGEST_MIN_CONTEXT

    def _extract_explicit_ids(self, query: str) -> list[tuple[str, str]]:
        """从问题抽显式 KEGG/UniProt 标识（source, asset_id）"""
        found: list[tuple[str, str]] = []
        if self._registry_has("kegg"):
            for raw in _KEGG_ID_RE.findall(query):
                found.append(("kegg", raw.lower()))
        if self._registry_has("uniprot"):
            for acc in _UNIPROT_ACC_RE.findall(query):
                found.append(("uniprot", acc))
        return found

    def _kb_working_dir(self) -> Path | None:
        wd = getattr(self.knowledge_client, "working_dir", None)
        if not wd:
            return None
        try:
            return Path(wd)
        except TypeError:
            return None

    def _kb_asset_ids(self) -> set[str] | None:
        """专属文档头里的资产 ID（小写）；无法定位 working_dir 时返回 None。

        仅认 `# KEGG 通路: map…` / `# UniProt 蛋白: P…` 这类入库头，
        不把实体交叉引用当作「已有该通路/蛋白文档」。
        """
        base = self._kb_working_dir()
        if base is None:
            return None
        docs_path = base / "kv_store_full_docs.json"
        if not docs_path.exists():
            return set()
        try:
            docs = json.loads(docs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("read full_docs for miss-check failed: %s", e)
            return set()

        asset_ids: set[str] = set()
        header_re = re.compile(
            r"^#\s*(?:KEGG\s+通路|UniProt\s+蛋白):\s*(\S+)",
            re.IGNORECASE | re.MULTILINE,
        )
        for item in docs.values():
            content = (
                item.get("content", "") if isinstance(item, dict) else str(item)
            )
            for mid in header_re.findall(content):
                asset_ids.add(mid.lower())
        return asset_ids

    def _kb_entity_names(self) -> set[str] | None:
        """实体表实体名（小写）；用于 genes 是否已在图中"""
        base = self._kb_working_dir()
        if base is None:
            return None
        entities_path = base / "kv_store_full_entities.json"
        if not entities_path.exists():
            return set()
        try:
            data = json.loads(entities_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("read full_entities for miss-check failed: %s", e)
            return set()

        known: set[str] = set()
        for item in data.values():
            if not isinstance(item, dict):
                continue
            for name in item.get("entity_names") or []:
                known.add(str(name).lower())
        return known

    def _kb_known_ids(self) -> set[str] | None:
        """兼容旧调用：文档头资产 ID ∪ 实体名（小写）"""
        assets = self._kb_asset_ids()
        entities = self._kb_entity_names()
        if assets is None or entities is None:
            return None
        return assets | entities

    def _context_for_miss_check(self, query: str) -> str | None:
        """取 only_need_context 文本；无法检查时返回 None（跳过补库）"""
        if self.knowledge_client is None:
            return None
        qctx = getattr(self.knowledge_client, "query_context", None)
        if not callable(qctx):
            return None
        try:
            return qctx(query) or ""
        except Exception as e:  # noqa: BLE001 - 检查失败不阻断正常问答
            logger.warning("query_context for miss-check failed: %s", e)
            return None

    def _lazy_ingest_missing(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """从问题抽 KEGG/UniProt 标识并按需入库；失败逐条降级，返回成功列表"""
        if self.knowledge_builder is None or self.fetcher_registry is None:
            return []

        candidates = self._collect_lazy_candidates(query, params or {})
        ingested: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for source, asset_id in candidates:
            key = (source, asset_id.upper() if source == "uniprot" else asset_id.lower())
            if key in seen:
                continue
            seen.add(key)
            if len(ingested) >= LAZY_INGEST_MAX_ASSETS:
                break
            try:
                result = self.ingest_asset_to_kb(source, asset_id)
            except Exception as e:  # noqa: BLE001 - 单条失败不阻断后续与回答
                logger.warning("lazy ingest %s/%s failed: %s", source, asset_id, e)
                continue
            if result.get("status") == "success":
                ingested.append(
                    {"source": source, "asset_id": asset_id, "title": asset_id}
                )
                logger.info("lazy ingested %s/%s", source, asset_id)
            else:
                logger.warning(
                    "lazy ingest %s/%s: %s", source, asset_id, result.get("message")
                )
        return ingested

    def _collect_lazy_candidates(
        self, query: str, params: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """显式 ID 优先，不足再 search 兜底；仅 kegg/uniprot，上限 MAX"""
        candidates: list[tuple[str, str]] = []
        max_n = LAZY_INGEST_MAX_ASSETS

        if self._registry_has("kegg"):
            for raw in _KEGG_ID_RE.findall(query):
                candidates.append(("kegg", raw.lower()))
                if len(candidates) >= max_n:
                    return candidates

        if self._registry_has("uniprot"):
            for acc in _UNIPROT_ACC_RE.findall(query):
                candidates.append(("uniprot", acc))
                if len(candidates) >= max_n:
                    return candidates

        # 自由文本 / 基因符号：search 兜底（仍不含 GEO）
        for source in LAZY_INGEST_SOURCES:
            if len(candidates) >= max_n:
                break
            if not self._registry_has(source):
                continue
            fetcher = self.fetcher_registry.get(source)
            search_q = self._search_term_for(source, query, params)
            if not search_q:
                continue
            try:
                metas = fetcher.search(search_q, max_results=max_n)
            except Exception as e:  # noqa: BLE001
                logger.warning("lazy search %s failed: %s", source, e)
                continue
            for meta in metas:
                candidates.append((source, meta.asset_id))
                if len(candidates) >= max_n:
                    break
        return candidates[:max_n]

    def _registry_has(self, source: str) -> bool:
        if self.fetcher_registry is None:
            return False
        has = getattr(self.fetcher_registry, "has", None)
        if callable(has):
            try:
                return bool(has(source))
            except Exception:  # noqa: BLE001, S110 - 兼容无 has 的 mock
                pass
        sources = getattr(self.fetcher_registry, "sources", None)
        if callable(sources):
            try:
                return source in sources()
            except Exception:  # noqa: BLE001
                return False
        try:
            self.fetcher_registry.get(source)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _search_term_for(
        self, source: str, query: str, params: dict[str, Any]
    ) -> str:
        """search 用词：UniProt 优先 gene:，KEGG 用原问题"""
        genes = params.get("genes") or []
        if source == "uniprot" and genes:
            return " OR ".join(f"gene:{g}" for g in genes[:3])
        # 去掉明显不像检索词的空白
        return query.strip()

    def _execute_fetch_data_workflow(
        self, intent: dict[str, Any], params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """阶段 1：检索 → 列出候选 → 等待用户确认下载"""
        if self.fetcher_registry is None:
            return {
                "status": "error",
                "type": "fetch_data",
                "message": "数据获取组件未配置",
            }
        query = intent.get("original_input", "")
        sources = params.get("sources") or self.fetcher_registry.sources()

        candidates = []
        for source in sources:
            fetcher = self.fetcher_registry.get(source)
            try:
                metas = fetcher.search(query, max_results=5)
            except Exception as e:  # noqa: BLE001 - 单个来源失败不应中断整体检索
                logger.warning("source %s search failed: %s", source, e)
                continue
            for meta in metas:
                detail = None
                try:
                    detail = fetcher.confirm(meta.asset_id)
                except Exception as e:  # noqa: BLE001 - 详情失败不阻断候选列表
                    logger.warning("confirm %s failed: %s", meta.asset_id, e)

                reason_bits = [q.strip() for q in query.split() if q.strip()]
                reason = (
                    f"检索词命中：{', '.join(reason_bits[:5]) or '（无分词）'}；"
                    f"标题含相关关键词"
                    if any(b.lower() in meta.title.lower() for b in reason_bits)
                    else f"来自 {meta.source} 的 {meta.asset_type} 资产"
                )
                item = {
                    "source": meta.source,
                    "asset_id": meta.asset_id,
                    "title": meta.title,
                    "asset_type": meta.asset_type,
                    "reason": reason,
                    "description": getattr(detail, "description", "") if detail else "",
                    "metadata": dict(getattr(detail, "metadata", {}) or {}) if detail else {},
                }
                candidates.append(item)

        if not candidates:
            return {
                "status": "no_results",
                "type": "fetch_data",
                "query": query,
                "message": "未找到匹配的数据集，请换关键词重试",
            }

        context["fetch_candidates"] = candidates
        return {
            "status": "needs_confirmation",
            "type": "fetch_data",
            "query": query,
            "candidates": candidates,
            "message": f"找到 {len(candidates)} 个候选数据集，请选择要下载的项",
        }

    def _execute_general_workflow(
        self, intent: dict[str, Any], params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """执行通用工作流"""
        return {
            "status": "success",
            "type": "general_response",
            "message": "这是一个通用响应。请询问具体的数据分析或知识问题。",
        }

    def confirm_and_download(
        self, source: str, asset_id: str, query: str = ""
    ) -> dict[str, Any]:
        """阶段 2：确认详情 → 下载落盘（分析流入口）"""
        if self.fetcher_registry is None:
            return {"status": "error", "message": "数据获取组件未配置"}
        fetcher = self.fetcher_registry.get(source)
        info = fetcher.confirm(asset_id)
        access_path = fetcher.download(asset_id)
        logger.info(
            "Asset confirmed and downloaded: %s/%s -> %s", source, asset_id, access_path
        )
        from src.data.lineage import append_lineage

        try:
            append_lineage(
                self.lineage_path,
                {
                    "source": source,
                    "asset_id": asset_id,
                    "title": info.title,
                    "access_path": str(access_path),
                    "query": query,  # UI 可在后续传入；保持字段存在
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("lineage append failed: %s", e)
        return {
            "status": "success",
            "type": "fetch_result",
            "asset": {
                "source": source,
                "asset_id": asset_id,
                "title": info.title,
                "access_path": str(access_path),
                "metadata": info.metadata,
            },
        }

    def ingest_asset_to_kb(self, source: str, asset_id: str) -> dict[str, Any]:
        """知识流：将资产文本写入知识库（LightRAG）"""
        if self.fetcher_registry is None or self.knowledge_builder is None:
            return {"status": "error", "message": "知识构建组件未配置"}
        fetcher = self.fetcher_registry.get(source)
        text = fetcher.ingest_text(asset_id)
        result = self.knowledge_builder.build_from_text(text)
        return {
            "status": "success",
            "type": "ingest_result",
            "asset_id": asset_id,
            "inserted": result.get("inserted"),
        }
