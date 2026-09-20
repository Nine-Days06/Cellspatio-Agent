"""PubMed XML 解析器 - 将 XML 文件解析并写入 SQLite"""
import sqlite3
from pathlib import Path
from lxml import etree

from config.settings import DB_PATH, RAW_XML_DIR
from utils.db import init_db, get_conn

INSERT_SQL = """
INSERT OR IGNORE INTO articles
  (pmid, title, abstract, keywords, mesh_terms,
   pub_year, pub_month, journal, journal_abbr,
   doi, pmc_id, article_types, authors,
   affiliation, language, raw_xml_file)
VALUES
  (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _text(el, xpath: str, default: str = "") -> str:
    """安全取文本"""
    node = el.find(xpath)
    return (node.text or "").strip() if node is not None else default


def _join(items: list[str], sep: str = "|") -> str:
    return sep.join(filter(None, items))


def parse_article(article_el: etree._Element, source_file: str) -> dict | None:
    """解析单个 PubmedArticle 元素"""
    try:
        mc = article_el.find("MedlineCitation")
        if mc is None:
            return None

        pmid = _text(mc, "PMID")
        if not pmid:
            return None

        art = mc.find("Article")
        if art is None:
            return None

        # 标题
        title = "".join(art.find("ArticleTitle").itertext()).strip() \
            if art.find("ArticleTitle") is not None else ""

        # 摘要
        abstract_parts = []
        abstract_el = art.find("Abstract")
        if abstract_el is not None:
            for at in abstract_el.findall("AbstractText"):
                label = at.get("Label", "")
                text  = "".join(at.itertext()).strip()
                if label:
                    abstract_parts.append(f"[{label}] {text}")
                else:
                    abstract_parts.append(text)
        abstract = " ".join(abstract_parts)

        # 关键词
        kw_list = mc.find("KeywordList")
        keywords = _join([
            kw.text.strip() for kw in (kw_list if kw_list is not None else [])
            if kw.text
        ])

        # MeSH 词
        mesh_list = mc.find("MeshHeadingList")
        mesh_terms = _join([
            mh.findtext("DescriptorName", "").strip()
            for mh in (mesh_list if mesh_list is not None else [])
        ])

        # 发表日期
        pub_date = art.find(".//Journal/JournalIssue/PubDate")
        pub_year  = None
        pub_month = ""
        if pub_date is not None:
            year_el = pub_date.find("Year")
            month_el = pub_date.find("Month")
            medline_el = pub_date.find("MedlineDate")
            if year_el is not None:
                try:
                    pub_year = int(year_el.text)
                except ValueError:
                    pass
            elif medline_el is not None:
                try:
                    pub_year = int(medline_el.text[:4])
                except ValueError:
                    pass
            if month_el is not None:
                pub_month = month_el.text or ""

        # 期刊
        journal_el   = art.find(".//Journal/Title")
        journal      = journal_el.text.strip() if journal_el is not None else ""
        jabbr_el     = art.find(".//Journal/ISOAbbreviation")
        journal_abbr = jabbr_el.text.strip() if jabbr_el is not None else ""

        # DOI / PMC
        pd_el  = article_el.find("PubmedData")
        doi    = ""
        pmc_id = ""
        if pd_el is not None:
            aid_list = pd_el.find("ArticleIdList")
            if aid_list is not None:
                for aid in aid_list.findall("ArticleId"):
                    id_type = aid.get("IdType", "")
                    val     = (aid.text or "").strip()
                    if id_type == "doi":
                        doi = val
                    elif id_type == "pmc":
                        pmc_id = val

        # 文章类型
        article_types = _join([
            pt.text.strip()
            for pt in art.findall(".//PublicationType")
            if pt.text
        ])

        # 作者
        author_names = []
        affiliation  = ""
        author_list  = art.find("AuthorList")
        if author_list is not None:
            for i, author in enumerate(author_list.findall("Author")):
                last  = _text(author, "LastName")
                first = _text(author, "ForeName")
                coll  = _text(author, "CollectiveName")
                name  = f"{last} {first}".strip() if last else coll
                if name:
                    author_names.append(name)
                if i == 0:
                    aff_el = author.find(".//Affiliation")
                    if aff_el is not None:
                        affiliation = (aff_el.text or "").strip()

        authors = _join(author_names)

        # 语言
        lang_el  = art.find("Language")
        language = lang_el.text.strip() if lang_el is not None else ""

        return {
            "pmid": pmid, "title": title, "abstract": abstract,
            "keywords": keywords, "mesh_terms": mesh_terms,
            "pub_year": pub_year, "pub_month": pub_month,
            "journal": journal, "journal_abbr": journal_abbr,
            "doi": doi, "pmc_id": pmc_id,
            "article_types": article_types, "authors": authors,
            "affiliation": affiliation, "language": language,
            "raw_xml_file": source_file,
        }

    except Exception as e:
        pmid_text = ""
        try:
            pmid_text = article_el.findtext(".//PMID", "UNKNOWN")
        except Exception:
            pass
        print(f"  解析 PMID {pmid_text} 失败: {e}")
        return None


def _parse_xml_content(xml_path: Path, source_name: str) -> tuple[list[tuple], int]:
    """解析一个 XML 文件内容，返回 (rows, skipped)"""
    try:
        tree = etree.parse(str(xml_path), parser=etree.XMLParser(recover=True))
    except etree.XMLSyntaxError as e:
        print(f"  XML 语法错误，跳过 {source_name}: {e}")
        return [], 0

    articles = tree.findall(".//PubmedArticle")
    if not articles:
        print(f"  {source_name} 中未找到 PubmedArticle 元素")
        return [], 0

    rows = []
    skipped = 0
    for art_el in articles:
        rec = parse_article(art_el, source_name)
        if rec:
            rows.append((
                rec["pmid"], rec["title"], rec["abstract"],
                rec["keywords"], rec["mesh_terms"],
                rec["pub_year"], rec["pub_month"],
                rec["journal"], rec["journal_abbr"],
                rec["doi"], rec["pmc_id"],
                rec["article_types"], rec["authors"],
                rec["affiliation"], rec["language"],
                rec["raw_xml_file"],
            ))
        else:
            skipped += 1
    return rows, skipped


def parse_xml_file(xml_path: Path, db_path: Path = DB_PATH, conn: sqlite3.Connection = None) -> tuple[int, int]:
    """解析一个 XML 批次文件，写入数据库"""
    xml_path = Path(xml_path)
    source_name = xml_path.name
    rows, skipped = _parse_xml_content(xml_path, source_name)

    if conn is not None:
        conn.executemany(INSERT_SQL, rows)
    else:
        with get_conn(db_path) as c:
            c.executemany(INSERT_SQL, rows)

    return len(rows), skipped


def run_parse(xml_dir: Path = None, db_path: Path = DB_PATH) -> None:
    """遍历 xml_dir 下所有 batch_*.xml，全部解析写入 SQLite"""
    xml_dir  = Path(xml_dir or RAW_XML_DIR)
    db_path  = Path(db_path)

    print("=" * 60)
    print("阶段二：XML 解析 → SQLite")
    print(f"XML 目录: {xml_dir}")
    print(f"数据库: {db_path}")
    print("=" * 60)

    init_db(db_path)

    xml_files = sorted(xml_dir.glob("batch_*.xml"))
    if not xml_files:
        print(f"  未在 {xml_dir} 中找到 batch_*.xml 文件")
        return

    total_written = 0
    total_skipped = 0
    with get_conn(db_path) as conn:
        for i, xf in enumerate(xml_files, 1):
            w, s = parse_xml_file(xf, db_path, conn=conn)
            total_written += w
            total_skipped += s
            if i % 10 == 0 or i == len(xml_files):
                print(f"  进度: {i}/{len(xml_files)} 个文件，累计写入 {total_written} 篇")

    print(f"  解析完成：写入 {total_written} 篇，解析失败 {total_skipped} 篇")
