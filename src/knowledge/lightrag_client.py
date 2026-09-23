import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# LLM 自造的参考文献段（LightRAG prompt 要求输出，中文模型可能译成参考文献）
_REF_SECTION_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*)?"
    r"(?:references|参考文献|引用文献|bibliography)"
    r"(?:\*\*)?[ \t:：]*$"
)
_KEGG_HEADER_RE = re.compile(r"^#\s*KEGG\s+通路:\s*(\S+)", re.MULTILINE)
_UNIPROT_HEADER_RE = re.compile(r"^#\s*UniProt\s+蛋白:\s*(\S+)", re.MULTILINE)
_GEO_HEADER_RE = re.compile(r"^#\s*GEO\s+数据集:\s*(\S+)", re.MULTILINE)
_SOURCE_LINE_RE = re.compile(r"^来源：(\S+)", re.MULTILINE)
_LINK_LINE_RE = re.compile(r"^链接：(\S+)", re.MULTILINE)
_PMID_LINE_RE = re.compile(r"^PMID：(\S+)", re.MULTILINE)
_DOI_LINE_RE = re.compile(r"^DOI：(\S+)", re.MULTILINE)
_KEGG_ID_RE = re.compile(r"^(?:map|hsa|ko)\d+$", re.IGNORECASE)
_UNIPROT_ACC_RE = re.compile(
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{9}[0-9]))$"
)
_PMID_BASENAME_RE = re.compile(r"^\d{6,9}$")


def strip_references_section(text: str) -> str:
    """去掉回答末尾 LLM 自由发挥的 References/参考文献 段（切到文末）"""
    if not text:
        return text
    m = _REF_SECTION_RE.search(text)
    if m:
        return text[: m.start()].rstrip() + "\n"
    return text


def derive_file_path(text: str) -> str:
    """从入库文本推导 citation file_path（URL 或空串）。

    LightRAG 只存 basename，故 URL 末段必须全局唯一
    （UniProt 不能以 `/entry` 结尾，否则 10 条会撞成同一文档）。
    """
    m = _SOURCE_LINE_RE.search(text) or _LINK_LINE_RE.search(text)
    if m:
        return m.group(1)
    m = _KEGG_HEADER_RE.search(text)
    if m:
        return f"https://www.kegg.jp/pathway/{m.group(1)}"
    m = _UNIPROT_HEADER_RE.search(text)
    if m:
        return f"https://www.uniprot.org/uniprotkb/{m.group(1)}"
    m = _GEO_HEADER_RE.search(text)
    if m:
        return f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={m.group(1)}"
    m = _DOI_LINE_RE.search(text)
    if m:
        return f"https://doi.org/{m.group(1)}"
    m = _PMID_LINE_RE.search(text)
    if m:
        return f"https://pubmed.ncbi.nlm.nih.gov/{m.group(1)}/"
    return ""


def expand_file_path(fp: str) -> str:
    """把 LightRAG 存的 basename 还原为可点击完整 URL"""
    if not fp or fp.startswith("http") or fp == "unknown_source":
        return fp
    if fp.startswith("acc.cgi"):
        return f"https://www.ncbi.nlm.nih.gov/geo/query/{fp}"
    if _KEGG_ID_RE.match(fp):
        return f"https://www.kegg.jp/pathway/{fp}"
    if _UNIPROT_ACC_RE.match(fp):
        return f"https://www.uniprot.org/uniprotkb/{fp}"
    if _PMID_BASENAME_RE.match(fp):
        return f"https://pubmed.ncbi.nlm.nih.gov/{fp}/"
    return fp


def format_reference(ref: dict[str, Any]) -> str:
    """结构化引用 → Markdown 链接或编号条目"""
    url = expand_file_path(str(ref.get("file_path") or ""))
    rid = str(ref.get("reference_id") or "")
    if url.startswith("http"):
        label = label_from_url(url) or url
        return f"[{label}]({url})"
    if rid:
        return f"[{rid}] {url}" if url else f"[{rid}]"
    return url


def label_from_url(url: str) -> str:
    """从 source URL 提炼可读标签"""
    u = url.rstrip("/")
    if "kegg.jp/pathway/" in u:
        return f"KEGG {u.rsplit('/', 1)[-1]}"
    if "uniprot.org/uniprotkb/" in u:
        return f"UniProt {u.split('/uniprotkb/')[-1].split('/')[0]}"
    if "pubmed.ncbi.nlm.nih.gov/" in u:
        return f"PMID {u.rsplit('/', 1)[-1]}"
    if "acc.cgi?acc=" in u:
        return f"GEO {u.split('acc=')[-1]}"
    if "doi.org/" in u:
        return f"DOI {u.split('doi.org/')[-1]}"
    return ""


class LightRAGClient:
    """LightRAG 客户端封装"""

    def __init__(self, working_dir: str, config: dict[str, Any] | None = None):
        self.working_dir = Path(working_dir)
        self.working_dir.mkdir(exist_ok=True)
        self.config = config or {}

        # 延迟导入 LightRAG，避免立即依赖
        self._rag = None

    @staticmethod
    def _normalize_ollama_host(host: str) -> str:
        """把 0.0.0.0 / 裸 host:port 规范成客户端可连的 URL"""
        h = (host or "").strip().rstrip("/")
        if not h:
            return "http://127.0.0.1:11434"
        if "://" not in h:
            h = f"http://{h}"
        # 0.0.0.0 是服务端监听地址，客户端连不上
        if "://0.0.0.0" in h or "://[::]" in h:
            h = h.replace("://0.0.0.0", "://127.0.0.1").replace("://[::]", "://127.0.0.1")
        return h

    def _initialize_rag(self):
        """使用真实 LLM（云端 DeepSeek 等）与 embedding（本地 Ollama bge-m3）初始化"""
        if self._rag is None:
            try:
                from lightrag import LightRAG

                ollama_url = (
                    self.config.get("ollama_url")
                    or os.getenv("OLLAMA_URL")
                    or os.getenv("OLLAMA_HOST")
                    or "http://127.0.0.1:11434"
                )
                os.environ["OLLAMA_HOST"] = self._normalize_ollama_host(ollama_url)

                from src.knowledge.llm_factory import (
                    build_embedding_func,
                    build_llm_func,
                )

                llm_func, llm_model = build_llm_func(self.config.get("provider"))
                embedding_func = build_embedding_func(self.config.get("embedding_func"))
                self._validate_embedding_model(embedding_func)

                self._rag = LightRAG(
                    working_dir=str(self.working_dir),
                    llm_model_func=llm_func,
                    llm_model_name=llm_model,
                    embedding_func=embedding_func,
                    enable_llm_cache=True,
                    llm_model_max_async=2,  # 并发限制，受 API 限流影响
                    addon_params={
                        "language": "Chinese",
                        "entity_types_guidance": (
                            "- CellType: Cell types and clusters "
                            "(e.g. CD8 T cell, hepatocyte, cluster 3)\n"
                            "- CellState: Cell states or programs "
                            "(e.g. exhausted, cycling, EMT)\n"
                            "- SpatialSite: Spatial spots, regions, or anatomical sites "
                            "(e.g. tumor edge, Visium spot, cortex layer)\n"
                            "- Dataset: Single-cell or spatial datasets and accessions "
                            "(e.g. GSE subsets, 10x Visium runs)\n"
                            "- Gene: Gene symbols and official gene names "
                            "(e.g. TP53, INS, BRCA1)\n"
                            "- Protein: Proteins and UniProt accessions "
                            "(e.g. p53, P04637, cellular tumor antigen p53)\n"
                            "- Pathway: KEGG pathways and map IDs "
                            "(e.g. p53 signaling pathway, map04115, apoptosis)\n"
                            "- Disease: Diseases, phenotypes, and disorders "
                            "(e.g. type 2 diabetes mellitus, cancer)\n"
                            "- Organism: Species and strains "
                            "(e.g. Homo sapiens, Drosophila)\n"
                            "- Chemical: Compounds, drugs, and metabolites "
                            "(e.g. insulin, cisplatin)\n"
                            "- Variant: Mutations, SNPs, and protein variants\n"
                            "- Experiment: Assays and omics experiments "
                            "(e.g. scRNA-seq, spatial transcriptomics, GSE studies)\n"
                            "- Sample: Biological samples, cell lines, tissues "
                            "(e.g. SKOV3, pancreatic beta cell)\n"
                            "- Organization: Labs, databases, and institutions "
                            "(e.g. UniProt, KEGG, GEO)\n"
                            "- Person: Authors and researchers named in the text\n"
                            "- Publication: Papers and journals "
                            "(e.g. Cell Res, FEBS Lett)\n"
                            "- Concept: Other scientific concepts not covered above\n"
                            "- Other: Fallback when no type fits"
                        ).rstrip(),
                    },
                )
                # LightRAG 1.5.7+ 需要显式初始化存储
                asyncio.run(self._rag.initialize_storages())
                logger.info(
                    "LightRAG initialized: llm=%s embedding=%s",
                    llm_model,
                    getattr(embedding_func, "model_name", "custom"),
                )
            except ImportError:
                logger.error(
                    "LightRAG not installed. Install with: pip install lightrag-hku"
                )
                raise

    def insert_document(self, document: str, metadata: dict[str, Any] | None = None):
        """增量插入单篇文档；自动推导 file_path 供引用"""
        self._initialize_rag()
        if self._rag:
            fp = derive_file_path(document)
            if fp:
                self._rag.insert(document, file_paths=fp)
            else:
                self._rag.insert(document)
            logger.info(f"Document inserted, length: {len(document)}")

    def insert_documents(self, documents: list[str], file_paths: list[str] | None = None) -> int:
        """批量插入；file_paths 缺省时按文档头/来源行推导（citation 用）"""
        self._initialize_rag()
        if not documents or self._rag is None:
            return 0
        if file_paths is None:
            file_paths = [derive_file_path(d) for d in documents]
        # 全空则不传，兼容不支持 file_paths 的 mock
        if any(file_paths):
            self._rag.insert(documents, file_paths=file_paths)
        else:
            self._rag.insert(documents)
        logger.info(f"Batch inserted {len(documents)} documents")
        return len(documents)

    def query(self, question: str, mode: str = "hybrid") -> str:
        """查询知识库（纯文本；背景/兼容调用方）"""
        self._initialize_rag()
        if self._rag:
            from lightrag import QueryParam

            return self._rag.query(question, param=QueryParam(mode=mode))
        return "LightRAG 未初始化"

    def query_with_references(
        self, question: str, mode: str = "hybrid"
    ) -> dict[str, Any]:
        """查询并返回结构化引用。

        SDK 的 query() 只返回 str；引用在 query_llm 的 data.references。
        响应中的 LLM 自造 References 段已剥离。
        """
        self._initialize_rag()
        if not self._rag:
            return {"response": "LightRAG 未初始化", "references": []}
        from lightrag import QueryParam

        raw = self._rag.query_llm(
            question,
            param=QueryParam(mode=mode, include_references=True),
        )
        llm = raw.get("llm_response") or {}
        content = llm.get("content")
        if not isinstance(content, str):
            content = ""
        refs = (raw.get("data") or {}).get("references") or []
        expanded = []
        for ref in refs:
            if isinstance(ref, dict):
                r = dict(ref)
                r["file_path"] = expand_file_path(str(r.get("file_path") or ""))
                expanded.append(r)
            else:
                expanded.append(ref)
        return {
            "response": strip_references_section(content),
            "references": expanded,
        }

    def query_context(self, question: str, mode: str = "hybrid") -> str:
        """只返回检索到的上下文片段（不生成回答），供注入 codegen prompt"""
        self._initialize_rag()
        if not self._rag:
            return ""
        from lightrag import QueryParam

        return self._rag.query(
            question,
            param=QueryParam(mode=mode, only_need_context=True),
        )

    def insert_knowledge_graph(self, kg_data: dict[str, Any]):
        """插入知识图谱数据"""
        self._initialize_rag()
        if self._rag:
            # LightRAG 支持自定义知识图谱插入
            self._rag.insert_custom_kg(kg_data)

    def get_statistics(self) -> dict[str, Any]:
        """获取知识库统计信息"""
        return {
            "working_dir": str(self.working_dir),
            "initialized": self._rag is not None,
        }

    def _validate_embedding_model(self, embedding_func):
        """embedding 锁定校验：一旦建库写入模型标识，后续必须一致"""
        model_name = getattr(embedding_func, "model_name", None) or "custom"
        lock_file = self.working_dir / "EMBEDDING_MODEL.json"
        if lock_file.exists():
            saved = json.loads(lock_file.read_text(encoding="utf-8")).get("model")
            if saved != model_name:
                raise RuntimeError(
                    f"embedding 模型已锁定为 {saved}，不能改为 {model_name}；如需更换需清空知识库重建"
                )
        else:
            lock_file.write_text(
                json.dumps({"model": model_name}, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("embedding 模型已锁定: %s", model_name)
