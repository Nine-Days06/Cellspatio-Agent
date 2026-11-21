import csv
import logging
import math
import uuid
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from src.control.kb_coverage import (
    LAZY_INGEST_MAX_ASSETS,
    LAZY_INGEST_MIN_CONTEXT,
    LAZY_INGEST_SOURCES,
    KnowledgeCoverage,
)
from src.data.tenx import validate_tenx
from src.schemas.workflow import AnalysisType, StepType
from src.ui.analysis_roots import AnalysisRoots
from src.ui.session_store import SessionStore

logger = logging.getLogger(__name__)

__all__ = [
    "WorkflowManager",
    "LAZY_INGEST_MAX_ASSETS",
    "LAZY_INGEST_MIN_CONTEXT",
    "LAZY_INGEST_SOURCES",
]


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
        workflow_recorder=None,
        kg_query=None,
        analysis_roots: AnalysisRoots | None = None,
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
        self.workflow_recorder = workflow_recorder
        self.kg_query = kg_query  # 运行记忆问答（KGQuery），未注入时 query_memory 返回 error
        self.analysis_roots = analysis_roots or AnalysisRoots(
            SessionStore(),
            Path(workflow_recorder.repo_root) if workflow_recorder else Path.cwd(),
        )
        self._coverage = self._build_coverage()

    def execute_workflow(
        self, user_input: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """执行工作流"""
        context = context if context is not None else {}

        # 1. 解析意图（先解析，拿到 intent 再开始记录）
        intent = self.intent_parser.parse(user_input, context)
        params = self.intent_parser.extract_parameters(user_input)

        analysis_type = intent.get("analysis_type")
        try:
            AnalysisType(analysis_type)
        except ValueError:
            # 解析器可返回 visualization/pathway_analysis 等未执行类型；
            # 记录契约只接受受支持子类型，避免在 start_execution 处 500。
            analysis_type = None
        intent_dict = {
            "type": intent.get("type", "general"),
            "analysis_type": analysis_type,
            "original_input": user_input,
            "confidence": intent.get("confidence", 1.0),
        }

        with self._record_run(
            intent_dict,
            params,
            context,
            user_input=user_input,
            run_id=f"run-{uuid.uuid4().hex[:8]}",
        ) as run_id:
            logger.info(f"Parsed intent: {intent}")

            # 2. 根据意图执行相应工作流
            if intent["type"] == "analysis":
                result = self._execute_analysis_workflow(intent, params, context, run_id)
            elif intent["type"] == "knowledge_query":
                result = self._execute_knowledge_workflow(intent, params, context, run_id)
            elif intent["type"] == "fetch_data":
                result = self._execute_fetch_data_workflow(intent, params, context, run_id)
            else:
                result = self._execute_general_workflow(intent, params, context, run_id)

            self._record_outputs(run_id, result)

        return result

    def _record_outputs(self, run_id: str, result: dict[str, Any]) -> None:
        """把分析结果中的输出文件写入本次运行记录。"""
        if self.workflow_recorder is None:
            return
        path = (result.get("results") or {}).get("output_file")
        if not path:
            return
        suffix = Path(path).suffix.lower().lstrip(".")
        file_type = suffix if suffix in {"csv", "json", "txt", "png", "rds"} else "other"
        self.workflow_recorder.record_output(
            run_id, name=Path(path).name, path=str(path), type=file_type
        )

    def _resolve_input_file(self, params: dict[str, Any], context: dict[str, Any]) -> str | None:
        """解析输入文件：显式路径优先，不可用时回退下载资产。"""
        if params.get("input_files"):
            candidate = params["input_files"][0]
            if Path(candidate).exists():
                return candidate
        if context.get("downloaded_assets"):
            candidate = context["downloaded_assets"][-1].get("access_path")
            if candidate and Path(candidate).exists():
                return candidate
        return None

    @staticmethod
    def _validate_sc_input(input_file: str | None) -> dict[str, Any] | None:
        """流式校验 counts CSV 或 10x MTX 目录；合法时返回 None。"""
        if not input_file or not Path(input_file).exists():
            return {
                "status": "needs_input",
                "type": "analysis",
                "analysis_type": "single_cell",
                "message": "未检测到数据文件，请提供 10x MTX 目录或原始 counts CSV（首列为基因，其他列为细胞）",
                "results": {},
            }

        try:
            if Path(input_file).is_dir():
                validate_tenx(Path(input_file))
                return None
            if Path(input_file).suffix.lower() != ".csv":
                raise ValueError(
                    "仅支持 10x MTX 目录或 counts CSV，不支持 H5/H5AD、Seurat 对象或 FASTQ"
                )
            with open(input_file, newline="", encoding="utf-8-sig") as handle:
                reader = csv.reader(handle, strict=True)
                header = next(reader, [])
                cells = [cell.strip() for cell in header[1:]]
                if not cells or not all(cells) or len(set(cells)) != len(cells):
                    raise ValueError("CSV 必须包含基因列及非空、唯一的细胞列名")
                genes: set[str] = set()
                for row in reader:
                    if len(row) != len(header):
                        raise ValueError("CSV 数据行的列数必须与表头一致")
                    gene = row[0].strip()
                    if not gene or gene in genes:
                        raise ValueError("基因标识必须非空且唯一")
                    genes.add(gene)
                    for raw in row[1:]:
                        # 按十进制校验，避免浮点转换把微小小数吞成整数。
                        count = Decimal(raw.strip())
                        if (
                            not count.is_finite()
                            or count < 0
                            or count != count.to_integral_value()
                            or not math.isfinite(float(count))
                        ):
                            raise ValueError("counts 必须是有限的非负整数，不可缺失或四舍五入")
                if not genes:
                    raise ValueError("计数矩阵不能为空")
        except (
            OSError,
            EOFError,
            zlib.error,
            UnicodeError,
            csv.Error,
            InvalidOperation,
            ValueError,
            OverflowError,
        ) as exc:
            logger.warning("Invalid single-cell counts input: %s", exc)
            return {
                "status": "error",
                "type": "analysis",
                "analysis_type": "single_cell",
                "message": (
                    f"无效单细胞输入：{exc}。请提供完整 10x MTX 三件套或 counts CSV；"
                    "计数必须是有限的非负整数，不能缺失或使用标准化表达值"
                ),
                "results": {},
            }
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
            analysis_type,
            run_params,
            method_context=method_context,
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
            analysis_type,
            run_params,
            code,
            method_context,
            context,
        )

    def _execute_analysis_workflow(
        self,
        intent: dict[str, Any],
        params: dict[str, Any],
        context: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """执行分析工作流"""
        analysis_type = intent.get("analysis_type")

        if analysis_type == "differential_expression":
            return self._execute_de_analysis(params, context, run_id)
        if analysis_type == "single_cell":
            return self._execute_sc_analysis(params, context, run_id)
        if analysis_type == "spatial":
            return self._execute_spatial_analysis(params, context, run_id)

        # 不支持的分析类型：返回 error（schema 已白名单挡住，但以防万一）
        from src.control.tools import SUPPORTED_ANALYSIS_TYPES

        return {
            "status": "error",
            "type": "analysis",
            "analysis_type": analysis_type,
            "message": (
                f"不支持的分析类型: {analysis_type}；当前支持 {', '.join(SUPPORTED_ANALYSIS_TYPES)}"
            ),
            "results": {},
        }

    def _method_context_for(self, analysis_type: str, params: dict[str, Any]) -> str | None:
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
        self, params: dict[str, Any], context: dict[str, Any], run_id: str | None = None
    ) -> dict[str, Any]:
        """差异表达分析：优先使用已确认下载的 GEO 数据"""
        # 记录步骤开始
        self._record_step(
            run_id,
            step_id="de_analysis_start",
            step_type="analysis",
            tool="run_analysis",
            params={"analysis_type": "differential_expression"},
        )

        input_file = self._resolve_input_file(params, context)

        # 无数据：返回 needs_input 终态（AgentRuntime 短路上抛 UI），不再伪造成功
        if not input_file:
            result = {
                "status": "needs_input",
                "type": "analysis",
                "analysis_type": "differential_expression",
                "message": ("需提供数据文件，请先下载或指定输入文件"),
                "results": {},
            }
            self._record_step(
                run_id,
                step_id="de_analysis_no_input",
                step_type="analysis",
                tool="run_analysis",
                params={},
                output={"status": "needs_input", "message": "无输入数据"},
            )
            return result

        output_dir = self.analysis_roots.for_session(context.get("session_id")) / "de"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = str(output_dir / f"{run_id or 'de'}.de_results.csv")
        method_context = self._method_context_for("differential_expression", params)
        de_params = {"input_file": input_file, "output_file": output_file}
        if params.get("group_csv"):
            de_params["group_csv"] = params["group_csv"]
        code = self.r_script_generator.generate_code(
            "differential_expression",
            de_params,
            method_context=method_context,
        )

        # HITL: 需要脚本确认且未通过 context 批准
        if self.require_script_confirmation and not context.get("script_approved"):
            result = {
                "status": "needs_script_confirmation",
                "analysis_type": "differential_expression",
                "script": code,
                "params": de_params,
                "method_context": method_context,
            }
            self._record_step(
                run_id,
                step_id="de_analysis_script_generated",
                step_type="analysis",
                tool="run_analysis",
                params={"analysis_type": "differential_expression"},
                output={"status": "needs_script_confirmation", "script": code[:100]},
            )
            return result

        result = self._finish_analysis(
            "differential_expression",
            de_params,
            code,
            method_context,
            context,
        )

        self._record_step(
            run_id,
            step_id="de_analysis_finish",
            step_type="analysis",
            tool="run_analysis",
            params={"analysis_type": "differential_expression"},
            output={"status": result.get("status", "success"), "result": result.get("results")},
        )

        return result

    def _execute_sc_analysis(
        self, params: dict[str, Any], context: dict[str, Any], run_id: str | None = None
    ) -> dict[str, Any]:
        """单细胞固定过滤前 QC；不批准或执行下游分析。"""
        self._record_step(
            run_id,
            step_id="sc_analysis_start",
            step_type="analysis",
            tool="run_analysis",
            params={"analysis_type": "single_cell"},
        )

        input_file = self._resolve_input_file(params, context)
        result = self._validate_sc_input(input_file)
        if result is not None:
            self._record_step(
                run_id,
                step_id="sc_analysis_input_check",
                step_type="analysis",
                tool="run_analysis",
                params={"analysis_type": "single_cell"},
                output={"status": result["status"], "message": result["message"]},
            )
            return result

        from src.analysis.sc_qc import run_sc_qc

        analysis_root = self.analysis_roots.for_session(context.get("session_id"))
        return run_sc_qc(input_file, self.r_executor, analysis_root / "sc_qc", run_id)

    def _execute_spatial_analysis(
        self, params: dict[str, Any], context: dict[str, Any], run_id: str | None = None
    ) -> dict[str, Any]:
        """空间转录组分析：聚类、空间图"""
        self._record_step(
            run_id,
            step_id="spatial_analysis_start",
            step_type="analysis",
            tool="run_analysis",
            params={"analysis_type": "spatial"},
        )

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
                "spatial",
                run_params,
                method_context=method_context,
            )
            result = {
                "status": "needs_script_confirmation",
                "analysis_type": "spatial",
                "script": code,
                "params": run_params,
                "method_context": method_context,
                "message": "未检测到数据文件，已生成示例 Visium 脚本（占位输入），请确认或取消",
            }
            self._record_step(
                run_id,
                step_id="spatial_analysis_no_input",
                step_type="analysis",
                tool="run_analysis",
                params={"analysis_type": "spatial"},
                output={"status": "needs_script_confirmation", "script": code[:100]},
            )
            return result

        output_dir = self.analysis_roots.for_session(context.get("session_id")) / "spatial"
        output_dir.mkdir(parents=True, exist_ok=True)
        run_params = {
            "input_file": input_file,
            "output_file": str(output_dir / f"{run_id or 'spatial'}.spatial_clusters.csv"),
            "plot_file": str(output_dir / f"{run_id or 'spatial'}.spatial_plot.png"),
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

        if analysis_type == "single_cell":
            return {
                "status": "error",
                "type": "analysis",
                "analysis_type": "single_cell",
                "message": "单细胞自由脚本执行已停用，请重新运行固定 QC。",
            }
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
                "status": "error",
                "analysis_type": analysis_type,
                "message": f"{self._analysis_type_zh(analysis_type)}分析失败: {last_err}",
                "results": {"repair_count": repair_count},
            }
        logger.info(
            "%s analysis finished on %s (returncode=%s)",
            analysis_type,
            input_file,
            result.returncode,
        )
        if result.returncode == 0:
            self._record_analysis_to_kb(
                analysis_type, input_file, output_file, params.get("question", "")
            )
        explanation = None
        if self.explainer is not None:
            try:
                stats = self._de_output_stats(output_file)
                explanation = self.explainer.generate_llm_explanation(
                    {
                        "input_file": input_file,
                        "output_file": output_file,
                        "analysis_type": analysis_type,
                        **stats,
                    },
                    question=params.get(
                        "question", f"{self._analysis_type_zh(analysis_type)}分析结果说明"
                    ),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("explanation failed: %s", e)
        capsule_dir = None
        try:
            from src.analysis.capsule import export_analysis_capsule

            capsule_dir = str(
                export_analysis_capsule(
                    question=params.get("question", ""),
                    intent={"type": "analysis", "analysis_type": analysis_type},
                    params={"input_file": input_file, "output_file": output_file},
                    script_code=current_code,
                    results={"returncode": result.returncode, "output_file": output_file},
                    out_root=self.analysis_roots.for_session(context.get("session_id"))
                    / "capsules",
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("capsule export failed: %s", e)
        return {
            "status": "success",
            "analysis_type": analysis_type,
            "message": f"{self._analysis_type_zh(analysis_type)}分析完成",
            "method_context": method_context,
            "results": {
                "returncode": result.returncode,
                "output_file": output_file,
                "repair_count": repair_count,
            },
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
        if analysis_type not in ("differential_expression", "spatial"):
            return {
                "status": "error",
                "analysis_type": analysis_type,
                "message": f"暂不支持确认执行的分析类型: {analysis_type}；单细胞请重新运行固定 QC。",
            }
        return self._finish_analysis(
            analysis_type,
            params,
            script,
            method_context,
            {"script_approved": True},
        )

    def _record_analysis_to_kb(
        self, analysis_type: str, input_file: str, output_file: str, user_question: str = ""
    ) -> None:
        """分析成功后写回主知识库（闭环记忆）；失败仅告警。

        只写文件名，不写绝对路径：共享知识库不应记录本机目录结构。
        """
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
            f"输入：{Path(input_file).name}\n"
            f"输出：{Path(output_file).name}\n"
            f"触发问题：{user_question}\n"
            "状态：执行成功（returncode=0）\n"
        )
        try:
            self.knowledge_builder.build_from_text(text)
        except Exception as e:  # noqa: BLE001 - 写回失败不影响分析结果返回
            logger.warning("analysis summary writeback failed: %s", e)

    def _execute_knowledge_workflow(
        self,
        intent: dict[str, Any],
        params: dict[str, Any],
        context: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """执行知识查询工作流；本地库缺目标 ID 时按需从 KEGG/UniProt 补库再答"""
        self._record_step(
            run_id,
            step_id="knowledge_query_start",
            step_type="knowledge_query",
            tool="query_knowledge",
            params={"query": intent.get("original_input", "")},
        )

        query = intent.get("original_input", "")

        coverage = self._current_coverage()
        lazy_ingested: list[dict[str, Any]] = []
        if coverage.should_ingest(query, params):
            lazy_ingested = coverage.ingest_missing(query, params)
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

        result = {
            "status": "success",
            "type": "knowledge_response",
            "query": query,
            "response": knowledge_result,
            "references": references,
            "lazy_ingested": lazy_ingested,
        }

        self._record_step(
            run_id,
            step_id="knowledge_query_finish",
            step_type="knowledge_query",
            tool="query_knowledge",
            params={"query": query},
            output={"status": "success", "response": knowledge_result[:100]},
        )

        return result

    def _lazy_ingest_missing(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """兼容测试入口：委托当前知识覆盖对象按需补库。"""
        return self._current_coverage().ingest_missing(query, params)

    def _build_coverage(self) -> KnowledgeCoverage:
        """按当前知识库依赖构造覆盖检查对象。"""
        return KnowledgeCoverage(
            self.knowledge_client,
            self.knowledge_builder,
            self.fetcher_registry,
            self.ingest_asset_to_kb,
            logger=logger,
        )

    def _current_coverage(self) -> KnowledgeCoverage:
        """依赖被测试替换时重建覆盖对象，避免旧绑定。"""
        coverage = self._coverage
        if (
            coverage.knowledge_client is not self.knowledge_client
            or coverage.builder is not self.knowledge_builder
            or coverage.registry is not self.fetcher_registry
            or coverage.ingest_asset != self.ingest_asset_to_kb
        ):
            coverage = self._build_coverage()
            self._coverage = coverage
        return coverage

    def _execute_fetch_data_workflow(
        self,
        intent: dict[str, Any],
        params: dict[str, Any],
        context: dict[str, Any],
        run_id: str | None = None,
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
                    f"检索词命中：{', '.join(reason_bits[:5]) or '（无分词）'}；标题含相关关键词"
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
        self,
        intent: dict[str, Any],
        params: dict[str, Any],
        context: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """执行通用工作流"""
        self._record_step(
            run_id,
            step_id="general_workflow",
            step_type=StepType.GENERAL,
            tool=None,
            params={},
            output={"status": "success", "message": "通用响应"},
        )
        return {
            "status": "success",
            "type": "general_response",
            "message": "这是一个通用响应。请询问具体的数据分析或知识问题。",
        }

    def confirm_and_download(self, source: str, asset_id: str, query: str = "") -> dict[str, Any]:
        """阶段 2：确认详情 → 下载落盘（分析流入口）"""
        if self.fetcher_registry is None:
            return {"status": "error", "message": "数据获取组件未配置"}
        fetcher = self.fetcher_registry.get(source)
        info = fetcher.confirm(asset_id)
        access_path = fetcher.download(asset_id)
        logger.info("Asset confirmed and downloaded: %s/%s -> %s", source, asset_id, access_path)
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

    # ── AgentRuntime 执行面（tool-calling）────────────────────
    @contextmanager
    def _record_run(
        self,
        intent: dict[str, Any],
        parameters: dict[str, Any],
        context: dict[str, Any],
        user_input: str | None = None,
        run_id: str | None = None,
    ) -> Iterator[str | None]:
        """执行面记录闭环：start → yield run_id → finish_run。

        无 recorder 时透传传入的 run_id（legacy 路径仍生成文件名）；不用 try/finally——
        异常时不 finish，保持既有行为。
        """
        if not self.workflow_recorder:
            yield run_id
            return
        run_id = self.workflow_recorder.start_execution(
            intent=intent,
            parameters=parameters,
            user_input=user_input
            or context.get("last_user_input")
            or intent.get("original_input", ""),
            context=context,
            run_id=run_id,
        )
        yield run_id
        try:
            self.workflow_recorder.finish_run(run_id)
        except Exception as exc:  # noqa: BLE001 - 记录失败不能触发整轮分析重跑
            logger.error("workflow record finalize failed: %s", exc)

    def _record_step(
        self,
        run_id: str | None,
        *,
        step_id: str,
        step_type: StepType | str,
        tool: str | None,
        params: dict[str, Any],
        output: dict[str, Any] | None = None,
    ) -> None:
        """记录执行步骤；无 recorder 或 run_id 为空时静默跳过。"""
        if not self.workflow_recorder or not run_id:
            return
        self.workflow_recorder.record_step(
            run_id,
            step_id=step_id,
            step_type=step_type,
            tool=tool,
            params=params,
            output=output,
        )

    def search_datasets_for_agent(
        self, query: str, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Agent 面向的数据集检索入口：构建 intent 并在记录闭环内调用内部工作流。"""
        context = context or {}
        intent = {"type": "fetch_data", "original_input": query}
        with self._record_run(intent, params, context) as run_id:
            return self._execute_fetch_data_workflow(intent, params, context, run_id)

    def query_knowledge_for_agent(
        self, query: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Agent 面向的知识查询入口：构建 intent 并在记录闭环内调用内部工作流。"""
        context = context or {}
        intent = {"type": "knowledge_query", "original_input": query}
        params = self.intent_parser.extract_parameters(query) if self.intent_parser else {}
        with self._record_run(intent, params, context) as run_id:
            return self._execute_knowledge_workflow(intent, params, context, run_id)

    def query_memory_for_agent(
        self, query: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Agent 面向的运行记忆问答入口：KGQuery → .wrroc/kg_memory 生成式查询。

        只读操作，刻意不纳入 run 记录闭环——记录会产生「查记忆→写记忆」噪音 run。
        """
        if self.kg_query is None:
            return {
                "status": "error",
                "type": "memory_response",
                "message": "运行记忆库未启用（kg_query 未注入）。",
            }
        try:
            answer = self.kg_query.query(query, context=context)
            return {"status": "success", "type": "memory_response", "response": answer}
        except Exception as e:  # noqa: BLE001 - 记忆查询失败降级为 error 终态
            return {
                "status": "error",
                "type": "memory_response",
                "message": f"运行记忆查询失败: {e}",
            }

    def run_analysis_for_agent(
        self, analysis_type: str, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Agent 面向的分析执行入口：校验类型白名单 → 构建 intent → 记录闭环内调用内部工作流。"""
        context = context or {}
        from src.control.tools import SUPPORTED_ANALYSIS_TYPES

        if analysis_type not in SUPPORTED_ANALYSIS_TYPES:
            return {
                "status": "error",
                "analysis_type": analysis_type,
                "message": f"不支持的分析类型: {analysis_type}，支持的类型: {', '.join(SUPPORTED_ANALYSIS_TYPES)}",
            }
        intent = {
            "type": "analysis",
            "analysis_type": analysis_type,
            "original_input": context.get("last_user_input", "") or params.get("question", ""),
        }
        with self._record_run(intent, params, context) as run_id:
            result = self._execute_analysis_workflow(intent, params, context, run_id)
            self._record_outputs(run_id, result)
        return result
