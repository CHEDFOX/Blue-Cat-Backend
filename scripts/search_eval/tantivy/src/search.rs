//! Query execution: tsquery trees -> Tantivy queries, filters, BM25 ranking, top-k.
//!
//! Matching uses the positional `all` field and reproduces tsquery semantics:
//! `&` = MUST, `|` = SHOULD, `!x` = ALL minus x, `a <N> b` = phrase with explicit offsets
//! (nested phrases are flattened with PostgreSQL's width rule: the right operand starts N
//! positions after the left operand's last lexeme; ORs inside phrases are distributed),
//! `x:*` = prefix (regex), `x:A`/`x:B` = term in the weight-A/weight-B field.
//! Each search_config gets its own query, restricted with a term on `search_config`.
//! Ranking: BM25 over the positive lexemes of the (expanded) query: field `a` x 1.0 + field
//! `b` x 0.4; the matching clause scores 0.
//! Filters reproduce the WHERE clause of search._candidates on fast fields (see `SegFilter`).

use crate::index::{fields, format_uuid, parse_uuid, Fields};
use crate::tsquery::{self, Node, W_A, W_B, W_C, W_D};
use crate::analyzer;
use serde_json::{json, Value};
use std::cmp::Ordering;
use std::collections::{BTreeSet, BinaryHeap, HashMap, HashSet};
use std::sync::Arc;
use std::time::Instant;
use tantivy::collector::{Collector, SegmentCollector};
use tantivy::columnar::{Column, StrColumn};
use tantivy::query::{
    AllQuery, BooleanQuery, BoostQuery, ConstScoreQuery, EmptyQuery, EnableScoring, Occur, PhrasePrefixQuery, PhraseQuery, Query,
    RegexQuery, TermQuery,
};
use tantivy::schema::{Field, IndexRecordOption, Term};
use tantivy::{DocId, DocSet, Index, IndexReader, Score, SegmentOrdinal, SegmentReader, TERMINATED};

pub const WEIGHT_A: f32 = 1.0;
pub const WEIGHT_B: f32 = 0.4;
const MAX_PHRASE_ALTERNATIVES: usize = 256;
/// Slop of the optional proximity clauses (request field "proximity" = their weight, default 0).
const PROXIMITY_SLOP: u32 = 2;

pub struct Engine {
    _index: Index,
    reader: IndexReader,
    f: Fields,
    pub mode: String,
}

enum Compiled {
    Nothing,
    All,
    Q(Box<dyn Query>),
}

fn regex_escape(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        if "\\.+*?()|[]{}^$#&-~\"<>@".contains(c) {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

fn all_minus(q: Box<dyn Query>) -> Box<dyn Query> {
    Box::new(BooleanQuery::new(vec![(Occur::Must, Box::new(AllQuery) as Box<dyn Query>), (Occur::MustNot, q)]))
}

/// One leaf of a flattened phrase: (offset, lexeme, prefix).
type PhraseAlt = Vec<(usize, String, bool)>;

fn flatten_phrase(n: &Node) -> Result<Vec<PhraseAlt>, String> {
    match n {
        Node::Val { lex, prefix, weights } => {
            if *weights != 0 {
                return Err("weight-restricted lexeme inside a phrase is not supported".into());
            }
            Ok(vec![vec![(0, lex.clone(), *prefix)]])
        }
        Node::Or(l, r) => {
            let mut v = flatten_phrase(l)?;
            v.extend(flatten_phrase(r)?);
            Ok(v)
        }
        Node::Phrase(l, r, d) => {
            let (ls, rs) = (flatten_phrase(l)?, flatten_phrase(r)?);
            let mut out = Vec::new();
            for la in &ls {
                let shift = la.iter().map(|t| t.0).max().unwrap_or(0) + *d as usize;
                for ra in &rs {
                    let mut alt = la.clone();
                    alt.extend(ra.iter().map(|(o, s, p)| (o + shift, s.clone(), *p)));
                    out.push(alt);
                    if out.len() > MAX_PHRASE_ALTERNATIVES {
                        return Err("too many phrase alternatives".into());
                    }
                }
            }
            Ok(out)
        }
        Node::Not(_) | Node::And(..) => Err("NOT/AND inside a phrase operator is not supported".into()),
        Node::Stop => Err("stopword placeholder in query".into()),
    }
}

impl Engine {
    pub fn open(path: &std::path::Path) -> Result<Engine, String> {
        let index = Index::open_in_dir(path).map_err(|e| e.to_string())?;
        let mode = crate::index::payload(&index).get("mode").and_then(|v| v.as_str()).unwrap_or("unknown").to_string();
        let reader = index.reader().map_err(|e| e.to_string())?;
        let f = fields(&index.schema());
        Ok(Engine { _index: index, reader, f, mode })
    }

    fn term(&self, field: Field, lex: &str) -> Box<dyn Query> {
        Box::new(TermQuery::new(Term::from_field_text(field, lex), IndexRecordOption::Basic))
    }

    fn leaf(&self, lex: &str, prefix: bool, weights: u8) -> Result<Box<dyn Query>, String> {
        // passages carry weights A (text) and B (context) only, so C/D restrict to nothing
        let fields: Vec<Field> = if weights == 0 {
            vec![self.f.all]
        } else {
            [(W_A, self.f.a), (W_B, self.f.b)].into_iter().filter(|(w, _)| weights & w != 0).map(|(_, f)| f).collect()
        };
        let _ = (W_C, W_D);
        let mut qs: Vec<(Occur, Box<dyn Query>)> = Vec::new();
        for field in fields {
            let q: Box<dyn Query> = if prefix {
                Box::new(
                    RegexQuery::from_pattern(&format!("{}.*", regex_escape(lex)), field).map_err(|e| e.to_string())?,
                )
            } else {
                self.term(field, lex)
            };
            qs.push((Occur::Should, q));
        }
        Ok(match qs.len() {
            0 => Box::new(EmptyQuery),
            1 => qs.pop().unwrap().1,
            _ => Box::new(BooleanQuery::new(qs)),
        })
    }

    fn compile(&self, n: &Node) -> Result<Compiled, String> {
        Ok(match n {
            Node::Val { lex, prefix, weights } => Compiled::Q(self.leaf(lex, *prefix, *weights)?),
            Node::Stop => Compiled::Nothing,
            Node::Not(child) => match self.compile(child)? {
                Compiled::Nothing => Compiled::All,
                Compiled::All => Compiled::Nothing,
                Compiled::Q(q) => Compiled::Q(all_minus(q)),
            },
            Node::And(..) => {
                let mut items = Vec::new();
                flatten_and(n, &mut items);
                let mut clauses: Vec<(Occur, Box<dyn Query>)> = Vec::new();
                let mut positive = false;
                for item in items {
                    if let Node::Not(inner) = item {
                        match self.compile(inner)? {
                            Compiled::Nothing => {}
                            Compiled::All => return Ok(Compiled::Nothing),
                            Compiled::Q(q) => clauses.push((Occur::MustNot, q)),
                        }
                        continue;
                    }
                    match self.compile(item)? {
                        Compiled::Nothing => return Ok(Compiled::Nothing),
                        Compiled::All => {}
                        Compiled::Q(q) => {
                            positive = true;
                            clauses.push((Occur::Must, q));
                        }
                    }
                }
                if clauses.is_empty() {
                    Compiled::All
                } else {
                    if !positive {
                        clauses.push((Occur::Must, Box::new(AllQuery)));
                    }
                    Compiled::Q(Box::new(BooleanQuery::new(clauses)))
                }
            }
            Node::Or(..) => {
                let mut items = Vec::new();
                flatten_or(n, &mut items);
                let mut clauses: Vec<(Occur, Box<dyn Query>)> = Vec::new();
                for item in items {
                    match self.compile(item)? {
                        Compiled::Nothing => {}
                        Compiled::All => return Ok(Compiled::All),
                        Compiled::Q(q) => clauses.push((Occur::Should, q)),
                    }
                }
                match clauses.len() {
                    0 => Compiled::Nothing,
                    1 => Compiled::Q(clauses.pop().unwrap().1),
                    _ => Compiled::Q(Box::new(BooleanQuery::new(clauses))),
                }
            }
            Node::Phrase(..) => {
                let alts = flatten_phrase(n)?;
                let mut clauses: Vec<(Occur, Box<dyn Query>)> = Vec::new();
                for alt in alts {
                    clauses.push((Occur::Should, self.phrase(alt)?));
                }
                if clauses.len() == 1 {
                    Compiled::Q(clauses.pop().unwrap().1)
                } else {
                    Compiled::Q(Box::new(BooleanQuery::new(clauses)))
                }
            }
        })
    }

    fn phrase(&self, mut alt: PhraseAlt) -> Result<Box<dyn Query>, String> {
        alt.sort_by_key(|t| t.0);
        let distinct: BTreeSet<usize> = alt.iter().map(|t| t.0).collect();
        if distinct.len() != alt.len() {
            return Err("two lexemes at the same phrase offset (<0>) are not supported".into());
        }
        if alt.len() == 1 {
            let (_, lex, prefix) = &alt[0];
            return self.leaf(lex, *prefix, 0);
        }
        let n = alt.len();
        let terms: Vec<(usize, Term)> = alt.iter().map(|(o, s, _)| (*o, Term::from_field_text(self.f.all, s))).collect();
        if alt[..n - 1].iter().any(|t| t.2) {
            return Err("prefix lexeme before the end of a phrase is not supported".into());
        }
        if alt[n - 1].2 {
            return Ok(Box::new(PhrasePrefixQuery::new_with_offset(terms)));
        }
        Ok(Box::new(PhraseQuery::new_with_offset(terms)))
    }

    fn score_clauses(&self, n: &Node, proximity: f32, out: &mut Vec<(Occur, Box<dyn Query>)>) {
        if proximity > 0.0 {
            // proximity: adjacent query lexemes (within one AND/phrase chain) found within 2 positions
            let mut pairs = BTreeSet::new();
            ordered_lexemes(n, &mut pairs);
            for (x, y) in pairs {
                let terms = vec![(0, Term::from_field_text(self.f.all, &x)), (1, Term::from_field_text(self.f.all, &y))];
                let q = PhraseQuery::new_with_offset_and_slop(terms, PROXIMITY_SLOP);
                out.push((Occur::Should, Box::new(BoostQuery::new(Box::new(q), proximity))));
            }
        }
        let mut lexemes = BTreeSet::new();
        positive_lexemes(n, false, &mut lexemes);
        for lex in lexemes {
            let a = TermQuery::new(Term::from_field_text(self.f.a, &lex), IndexRecordOption::WithFreqs);
            let b = TermQuery::new(Term::from_field_text(self.f.b, &lex), IndexRecordOption::WithFreqs);
            out.push((Occur::Should, Box::new(BoostQuery::new(Box::new(a), WEIGHT_A))));
            out.push((Occur::Should, Box::new(BoostQuery::new(Box::new(b), WEIGHT_B))));
        }
    }

    fn config_restricted(&self, cfg: &str, m: Compiled) -> Option<Box<dyn Query>> {
        let cfg_term = self.term(self.f.search_config, cfg);
        match m {
            Compiled::Nothing => None,
            Compiled::All => Some(cfg_term),
            Compiled::Q(q) => Some(Box::new(BooleanQuery::new(vec![(Occur::Must, cfg_term), (Occur::Must, q)]))),
        }
    }

    /// The passage ids (of `ids`) that are in the index.
    pub fn has(&self, ids: &[String]) -> Result<Value, String> {
        let wanted: HashSet<u128> = ids.iter().map(|s| parse_uuid(s)).collect::<Result<_, _>>()?;
        let searcher = self.reader.searcher();
        let mut found = Vec::new();
        for r in searcher.segment_readers() {
            let (hi, lo) = (col::<u64>(r, "id_hi")?, col::<u64>(r, "id_lo")?);
            for doc in 0..r.max_doc() {
                if r.is_deleted(doc) {
                    continue;
                }
                let id = ((get(&hi, doc).unwrap_or(0) as u128) << 64) | get(&lo, doc).unwrap_or(0) as u128;
                if wanted.contains(&id) {
                    found.push(format_uuid(id));
                }
            }
        }
        Ok(json!({"found": found}))
    }

    pub fn search(&self, req: &Value) -> Result<Value, String> {
        let t0 = Instant::now();
        let searcher = self.reader.searcher();
        let budget = req.get("budget").and_then(|v| v.as_u64()).unwrap_or(200) as usize;
        let filter = Arc::new(Filter::from_json(req.get("filters").unwrap_or(&Value::Null))?);
        let explain = req.get("explain").and_then(|v| v.as_bool()).unwrap_or(false);
        let proximity = req.get("proximity").and_then(|v| v.as_f64()).unwrap_or(0.0) as f32;
        let mut per_config: Vec<(Occur, Box<dyn Query>)> = Vec::new();
        let mut direct_parts: Vec<(Occur, Box<dyn Query>)> = Vec::new();
        let mut with_expansions: HashSet<String> = HashSet::new();
        let mut explained = Vec::new();
        for entry in req.get("configs").and_then(|v| v.as_array()).ok_or("configs missing")? {
            let cfg = entry.get("config").and_then(|v| v.as_str()).ok_or("config missing")?;
            if searcher.doc_freq(&Term::from_field_text(self.f.search_config, cfg)).map_err(|e| e.to_string())? == 0 {
                continue;
            }
            let analyzer = analyzer::config(cfg);
            let m = spec_to_node(analyzer, entry.get("match").ok_or("match missing")?)?;
            let d = match entry.get("direct") {
                Some(spec) if !spec.is_null() => spec_to_node(analyzer, spec)?,
                _ => m.clone(),
            };
            if explain {
                explained.push(json!({"config": cfg, "match": tsquery::to_text(&m), "direct": tsquery::to_text(&d)}));
            }
            let Some(m_node) = m else { continue };
            let Some(matching) = self.config_restricted(cfg, self.compile(&m_node)?) else { continue };
            let mut clauses: Vec<(Occur, Box<dyn Query>)> =
                vec![(Occur::Must, Box::new(ConstScoreQuery::new(matching, 0.0)))];
            self.score_clauses(&m_node, proximity, &mut clauses);
            per_config.push((Occur::Should, Box::new(BooleanQuery::new(clauses))));
            if d.as_ref() != Some(&m_node) {
                with_expansions.insert(cfg.to_string());
                if let Some(dn) = &d {
                    if let Some(q) = self.config_restricted(cfg, self.compile(dn)?) {
                        direct_parts.push((Occur::Should, q));
                    }
                }
            }
        }
        let compile_ms = t0.elapsed().as_secs_f64() * 1000.0;
        let mut out = json!({"hits": [], "matched": 0, "compile_ms": compile_ms});
        if explain {
            out["explain"] = json!(explained);
        }
        if per_config.is_empty() {
            out["search_ms"] = json!(t0.elapsed().as_secs_f64() * 1000.0);
            return Ok(out);
        }
        let query = BooleanQuery::new(per_config);
        let (entries, matched) =
            searcher.search(&query, &TopFiltered { k: budget, filter: filter.clone() }).map_err(|e| e.to_string())?;
        let search_ms = t0.elapsed().as_secs_f64() * 1000.0;

        // direct = the passage also matches its configuration's query without concept expansions
        let mut direct: HashSet<(u32, DocId)> = HashSet::new();
        if !direct_parts.is_empty() {
            let dq = BooleanQuery::new(direct_parts);
            let weight = dq.weight(EnableScoring::disabled_from_searcher(&searcher)).map_err(|e| e.to_string())?;
            let mut by_seg: HashMap<u32, Vec<DocId>> = HashMap::new();
            for e in &entries {
                by_seg.entry(e.seg).or_default().push(e.doc);
            }
            for (seg, mut docs) in by_seg {
                docs.sort_unstable();
                let mut scorer = weight.scorer(searcher.segment_reader(seg), 1.0).map_err(|e| e.to_string())?;
                for d in docs {
                    let mut cur = scorer.doc();
                    if cur < d && cur != TERMINATED {
                        cur = scorer.seek(d);
                    }
                    if cur == d {
                        direct.insert((seg, d));
                    }
                }
            }
        }
        let mut hits = Vec::with_capacity(entries.len());
        let mut readers: HashMap<u32, HitReader> = HashMap::new();
        for e in &entries {
            if !readers.contains_key(&e.seg) {
                readers.insert(e.seg, HitReader::new(searcher.segment_reader(e.seg))?);
            }
            let r = &readers[&e.seg];
            let cfg = r.str(&r.search_config, e.doc);
            let is_direct = !with_expansions.contains(&cfg) || direct.contains(&(e.seg, e.doc));
            hits.push(r.hit(e, is_direct)?);
        }
        out["hits"] = json!(hits);
        out["matched"] = json!(matched);
        out["search_ms"] = json!(search_ms);
        out["total_ms"] = json!(t0.elapsed().as_secs_f64() * 1000.0);
        Ok(out)
    }
}

fn flatten_and<'a>(n: &'a Node, out: &mut Vec<&'a Node>) {
    match n {
        Node::And(l, r) => {
            flatten_and(l, out);
            flatten_and(r, out);
        }
        other => out.push(other),
    }
}

fn flatten_or<'a>(n: &'a Node, out: &mut Vec<&'a Node>) {
    match n {
        Node::Or(l, r) => {
            flatten_or(l, out);
            flatten_or(r, out);
        }
        other => out.push(other),
    }
}

/// Positive lexemes of `n` in query order; collects pairs of neighbours joined by AND or a phrase
/// operator (not across OR, not under NOT).
fn ordered_lexemes(n: &Node, pairs: &mut BTreeSet<(String, String)>) -> Vec<String> {
    match n {
        Node::Val { lex, prefix: false, .. } => vec![lex.clone()],
        Node::Val { .. } | Node::Stop | Node::Not(_) => Vec::new(),
        Node::Or(l, r) => {
            ordered_lexemes(l, pairs);
            ordered_lexemes(r, pairs);
            Vec::new()
        }
        Node::And(l, r) | Node::Phrase(l, r, _) => {
            let mut left = ordered_lexemes(l, pairs);
            let right = ordered_lexemes(r, pairs);
            if let (Some(x), Some(y)) = (left.last(), right.first()) {
                if x != y {
                    pairs.insert((x.clone(), y.clone()));
                }
            }
            left.extend(right);
            left
        }
    }
}

fn positive_lexemes(n: &Node, negated: bool, out: &mut BTreeSet<String>) {
    match n {
        Node::Val { lex, prefix, .. } => {
            if !negated && !prefix {
                out.insert(lex.clone());
            }
        }
        Node::Stop => {}
        Node::Not(c) => positive_lexemes(c, !negated, out),
        Node::And(l, r) | Node::Or(l, r) | Node::Phrase(l, r, _) => {
            positive_lexemes(l, negated, out);
            positive_lexemes(r, negated, out);
        }
    }
}

/// A query specification sent by Python:
/// `{"tsquery": text}` (PostgreSQL's own output, mode pg), `{"websearch": q}`, `{"phrase": text}`
/// (websearch_to_tsquery / phraseto_tsquery copied natively), `{"or": [spec, ...]}` (tsquery `||`).
pub fn spec_to_node(cfg: &analyzer::Config, spec: &Value) -> Result<Option<Node>, String> {
    if let Some(t) = spec.get("tsquery").and_then(|v| v.as_str()) {
        return tsquery::parse_text(t);
    }
    if let Some(q) = spec.get("websearch").and_then(|v| v.as_str()) {
        return Ok(tsquery::websearch(cfg, q));
    }
    if let Some(q) = spec.get("phrase").and_then(|v| v.as_str()) {
        return Ok(tsquery::phraseto(cfg, q));
    }
    if let Some(items) = spec.get("or").and_then(|v| v.as_array()) {
        let mut acc = None;
        for item in items {
            acc = tsquery::or(acc, spec_to_node(cfg, item)?);
        }
        return Ok(acc);
    }
    Err(format!("bad query spec {spec}"))
}

// ------------------------------------------------------------------ filters

/// The WHERE clause of search._candidates (besides the text match).
pub struct Filter {
    as_of: i64,
    hist: bool,
    prosp: bool,
    proposed: bool,
    case_law: bool,
    langs: Option<Vec<String>>,
    scope: Option<Scope>,
}

struct Scope {
    chain: Vec<String>,
    treaty_ids: HashSet<u128>,
    system: Vec<String>,
    regional: Vec<String>,
}

fn strings(v: &Value, key: &str) -> Vec<String> {
    v.get(key).and_then(|x| x.as_array()).map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).collect()).unwrap_or_default()
}

impl Filter {
    pub fn from_json(v: &Value) -> Result<Filter, String> {
        let b = |k: &str| v.get(k).and_then(|x| x.as_bool()).unwrap_or(false);
        let scope = match v.get("scope") {
            Some(s) if !s.is_null() => Some(Scope {
                chain: strings(s, "chain"),
                treaty_ids: strings(s, "treaty_ids").iter().map(|u| parse_uuid(u)).collect::<Result<_, _>>()?,
                system: strings(s, "system"),
                regional: strings(s, "regional"),
            }),
            _ => None,
        };
        Ok(Filter {
            as_of: v.get("as_of").and_then(|x| x.as_i64()).ok_or("filters.as_of (days since 1970-01-01) missing")?,
            hist: b("hist"),
            prosp: b("prosp"),
            proposed: b("proposed"),
            case_law: v.get("case_law").and_then(|x| x.as_bool()).unwrap_or(true),
            langs: v.get("langs").filter(|x| !x.is_null()).map(|_| strings(v, "langs")),
            scope,
        })
    }
}

fn ord_of(col: &Option<StrColumn>, s: &str) -> Option<u64> {
    col.as_ref().and_then(|c| c.dictionary().term_ord(s.as_bytes()).ok().flatten())
}

fn ords_of(col: &Option<StrColumn>, items: &[String]) -> HashSet<u64> {
    items.iter().filter_map(|s| ord_of(col, s)).collect()
}

fn first_ord(col: &Option<StrColumn>, doc: DocId) -> Option<u64> {
    col.as_ref().and_then(|c| c.ords().first(doc))
}

fn str_col(r: &SegmentReader, name: &str) -> Result<Option<StrColumn>, String> {
    r.fast_fields().str(name).map_err(|e| e.to_string())
}

fn col<T>(r: &SegmentReader, name: &str) -> Result<Option<Column<T>>, String>
where
    T: tantivy::columnar::HasAssociatedColumnType,
    tantivy::columnar::DynamicColumn: Into<Option<Column<T>>>,
{
    r.fast_fields().column_opt::<T>(name).map_err(|e| e.to_string())
}

fn get<T: PartialOrd + Copy + std::fmt::Debug + Send + Sync + 'static>(c: &Option<Column<T>>, doc: DocId) -> Option<T> {
    c.as_ref().and_then(|c| c.first(doc))
}

/// The filter bound to one segment: string predicates become term-ordinal comparisons.
struct SegFilter {
    p: Arc<Filter>,
    kind: Option<StrColumn>,
    status: Option<StrColumn>,
    lang: Option<StrColumn>,
    jur: Option<StrColumn>,
    doc_type: Option<StrColumn>,
    extent: Option<StrColumn>,
    extent_empty: Option<Column<bool>>,
    vf: Option<Column<i64>>,
    vt: Option<Column<i64>>,
    has_vf: Option<Column<bool>>,
    has_vt: Option<Column<bool>>,
    inst_hi: Option<Column<u64>>,
    inst_lo: Option<Column<u64>>,
    o_judgment: Option<u64>,
    o_provision: Option<u64>,
    o_in_force: Option<u64>,
    o_prospective: Option<u64>,
    o_proposed: Option<u64>,
    o_treaty: Option<u64>,
    langs: Option<HashSet<u64>>,
    chain: HashSet<u64>,
    chain_extent: HashSet<u64>,
    system: HashSet<u64>,
    regional: HashSet<u64>,
}

fn is(o: Option<u64>, target: Option<u64>) -> bool {
    o.is_some() && o == target
}

impl SegFilter {
    fn new(p: Arc<Filter>, r: &SegmentReader) -> Result<SegFilter, String> {
        let kind = str_col(r, "kind")?;
        let status = str_col(r, "status")?;
        let lang = str_col(r, "language")?;
        let jur = str_col(r, "jurisdiction_id")?;
        let doc_type = str_col(r, "doc_type")?;
        let extent = str_col(r, "extent")?;
        let (chain, chain_extent, system, regional) = match &p.scope {
            Some(s) => (ords_of(&jur, &s.chain), ords_of(&extent, &s.chain), ords_of(&jur, &s.system), ords_of(&jur, &s.regional)),
            None => Default::default(),
        };
        Ok(SegFilter {
            o_judgment: ord_of(&kind, "judgment_paragraph"),
            o_provision: ord_of(&kind, "provision"),
            o_in_force: ord_of(&status, "in_force"),
            o_prospective: ord_of(&status, "prospective"),
            o_proposed: ord_of(&status, "proposed"),
            o_treaty: ord_of(&doc_type, "treaty"),
            langs: p.langs.as_ref().map(|l| ords_of(&lang, l)),
            chain,
            chain_extent,
            system,
            regional,
            extent_empty: col(r, "extent_empty")?,
            vf: col(r, "valid_from")?,
            vt: col(r, "valid_to")?,
            has_vf: col(r, "has_valid_from")?,
            has_vt: col(r, "has_valid_to")?,
            inst_hi: col(r, "inst_hi")?,
            inst_lo: col(r, "inst_lo")?,
            kind,
            status,
            lang,
            jur,
            doc_type,
            extent,
            p,
        })
    }

    fn matches(&self, doc: DocId) -> bool {
        let p = &*self.p;
        let kind = first_ord(&self.kind, doc);
        let is_jp = is(kind, self.o_judgment);
        if !p.case_law && is_jp {
            return false;
        }
        if !is_jp {
            let status = first_ord(&self.status, doc);
            let has_vf = get(&self.has_vf, doc).unwrap_or(false);
            let has_vt = get(&self.has_vt, doc).unwrap_or(false);
            let vf = if has_vf { get(&self.vf, doc) } else { None };
            let vt = if has_vt { get(&self.vt, doc) } else { None };
            // SQL: a comparison with NULL is never TRUE, so (NULL) <= as_of fails
            let started = vf.is_some_and(|v| v <= p.as_of);
            let not_ended = vt.is_none_or(|v| v > p.as_of);
            let valid = (is(status, self.o_in_force) && (!has_vf || p.hist || (started && not_ended)))
                || (is(status, self.o_prospective) && p.prosp)
                || (is(status, self.o_proposed) && p.proposed && started && not_ended);
            if !valid {
                return false;
            }
        }
        if let Some(langs) = &self.langs {
            match first_ord(&self.lang, doc) {
                Some(o) if langs.contains(&o) => {}
                _ => return false,
            }
        }
        if let Some(scope) = &p.scope {
            let is_prov = is(kind, self.o_provision);
            let is_treaty = is(first_ord(&self.doc_type, doc), self.o_treaty);
            let jur = first_ord(&self.jur, doc);
            let jur_in = |set: &HashSet<u64>| jur.is_some_and(|o| set.contains(&o));
            if is_prov && !is_treaty && jur_in(&self.chain) {
                let empty = get(&self.extent_empty, doc).unwrap_or(false);
                if empty
                    || self.extent.as_ref().is_some_and(|c| c.term_ords(doc).any(|o| self.chain_extent.contains(&o)))
                {
                    return true;
                }
            }
            if is_prov && !scope.treaty_ids.is_empty() {
                if let (Some(hi), Some(lo)) = (get(&self.inst_hi, doc), get(&self.inst_lo, doc)) {
                    if scope.treaty_ids.contains(&(((hi as u128) << 64) | lo as u128)) {
                        return true;
                    }
                }
            }
            if is_jp && jur_in(&self.system) {
                return true;
            }
            if !is_treaty && jur_in(&self.regional) {
                return true;
            }
            return false;
        }
        true
    }
}

// ------------------------------------------------------------------ top-k collector

#[derive(Clone, Copy, Debug)]
pub struct Entry {
    pub score: Score,
    pub id: u128,
    pub seg: u32,
    pub doc: DocId,
}

impl PartialEq for Entry {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}
impl Eq for Entry {}
impl PartialOrd for Entry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Entry {
    /// Greater = WORSE (ORDER BY lexical DESC, p.id): the max-heap keeps the worst kept entry on top.
    fn cmp(&self, other: &Self) -> Ordering {
        other.score.partial_cmp(&self.score).unwrap_or(Ordering::Equal).then(self.id.cmp(&other.id))
    }
}

struct TopFiltered {
    k: usize,
    filter: Arc<Filter>,
}

struct SegTop {
    k: usize,
    seg: u32,
    filter: SegFilter,
    id_hi: Option<Column<u64>>,
    id_lo: Option<Column<u64>>,
    heap: BinaryHeap<Entry>,
    matched: u64,
}

impl Collector for TopFiltered {
    type Fruit = (Vec<Entry>, u64);
    type Child = SegTop;

    fn for_segment(&self, seg: SegmentOrdinal, r: &SegmentReader) -> tantivy::Result<SegTop> {
        let filter = SegFilter::new(self.filter.clone(), r).map_err(tantivy::TantivyError::InternalError)?;
        Ok(SegTop {
            k: self.k,
            seg,
            filter,
            id_hi: col(r, "id_hi").map_err(tantivy::TantivyError::InternalError)?,
            id_lo: col(r, "id_lo").map_err(tantivy::TantivyError::InternalError)?,
            heap: BinaryHeap::new(),
            matched: 0,
        })
    }

    fn requires_scoring(&self) -> bool {
        true
    }

    fn merge_fruits(&self, fruits: Vec<(Vec<Entry>, u64)>) -> tantivy::Result<(Vec<Entry>, u64)> {
        let matched = fruits.iter().map(|f| f.1).sum();
        let mut all: Vec<Entry> = fruits.into_iter().flat_map(|f| f.0).collect();
        all.sort();
        all.truncate(self.k);
        Ok((all, matched))
    }
}

impl SegmentCollector for SegTop {
    type Fruit = (Vec<Entry>, u64);

    fn collect(&mut self, doc: DocId, score: Score) {
        if !self.filter.matches(doc) {
            return;
        }
        self.matched += 1;
        if self.k == 0 {
            return;
        }
        if self.heap.len() >= self.k {
            let worst = self.heap.peek().unwrap();
            if score < worst.score {
                return;
            }
        }
        let id = ((get(&self.id_hi, doc).unwrap_or(0) as u128) << 64) | get(&self.id_lo, doc).unwrap_or(0) as u128;
        let e = Entry { score, id, seg: self.seg, doc };
        if self.heap.len() < self.k {
            self.heap.push(e);
        } else if e < *self.heap.peek().unwrap() {
            self.heap.pop();
            self.heap.push(e);
        }
    }

    fn harvest(self) -> (Vec<Entry>, u64) {
        (self.heap.into_vec(), self.matched)
    }
}

// ------------------------------------------------------------------ hit output

struct HitReader {
    cols: HashMap<&'static str, Option<Column<u64>>>,
    strs: HashMap<&'static str, Option<StrColumn>>,
    search_config: Option<StrColumn>,
    vf: Option<Column<i64>>,
    vt: Option<Column<i64>>,
    has_vf: Option<Column<bool>>,
    has_vt: Option<Column<bool>>,
}

impl HitReader {
    fn new(r: &SegmentReader) -> Result<HitReader, String> {
        let mut cols = HashMap::new();
        for n in ["pv_hi", "pv_lo", "jp_hi", "jp_lo", "inst_hi", "inst_lo", "judg_hi", "judg_lo", "text_length"] {
            cols.insert(n, col::<u64>(r, n)?);
        }
        let mut strs = HashMap::new();
        for n in ["kind", "status", "language", "jurisdiction_id", "doc_type"] {
            strs.insert(n, str_col(r, n)?);
        }
        Ok(HitReader {
            cols,
            strs,
            search_config: str_col(r, "search_config")?,
            vf: col(r, "valid_from")?,
            vt: col(r, "valid_to")?,
            has_vf: col(r, "has_valid_from")?,
            has_vt: col(r, "has_valid_to")?,
        })
    }

    fn str(&self, c: &Option<StrColumn>, doc: DocId) -> String {
        let mut s = String::new();
        if let (Some(c), Some(o)) = (c, first_ord(c, doc)) {
            let _ = c.ord_to_str(o, &mut s);
        }
        s
    }

    fn uuid(&self, base: &str, doc: DocId) -> Value {
        let hi = get(&self.cols[format!("{base}_hi").as_str()], doc);
        let lo = get(&self.cols[format!("{base}_lo").as_str()], doc);
        match (hi, lo) {
            (Some(h), Some(l)) => json!(format_uuid(((h as u128) << 64) | l as u128)),
            _ => Value::Null,
        }
    }

    fn hit(&self, e: &Entry, direct: bool) -> Result<Value, String> {
        let d = e.doc;
        let s = |n: &str| self.str(&self.strs[n], d);
        let vf = if get(&self.has_vf, d).unwrap_or(false) { get(&self.vf, d) } else { None };
        let vt = if get(&self.has_vt, d).unwrap_or(false) { get(&self.vt, d) } else { None };
        Ok(json!({
            "id": format_uuid(e.id),
            "kind": s("kind"),
            "pv": self.uuid("pv", d),
            "jp": self.uuid("jp", d),
            "inst": self.uuid("inst", d),
            "judg": self.uuid("judg", d),
            "jur": s("jurisdiction_id"),
            "doc_type": s("doc_type"),
            "lang": s("language"),
            "status": s("status"),
            "vf": vf,
            "vt": vt,
            "score": e.score,
            "direct": direct,
            "len": get(&self.cols["text_length"], d).unwrap_or(0),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tsquery::parse_text;

    fn pairs(t: &str) -> Vec<(String, String)> {
        let mut out = BTreeSet::new();
        ordered_lexemes(&parse_text(t).unwrap().unwrap(), &mut out);
        out.into_iter().collect()
    }

    #[test]
    fn proximity_pairs_follow_and_and_phrase_chains_only() {
        let p = |a: &str, b: &str| (a.to_string(), b.to_string());
        assert_eq!(pairs("'a' & 'b' & 'c'"), vec![p("a", "b"), p("b", "c")]);
        assert_eq!(pairs("'x' | 'y' & 'z'"), vec![p("y", "z")]);
        // a negated lexeme is skipped: its neighbours become adjacent
        assert_eq!(pairs("'a' & !'b' & 'c'"), vec![p("a", "c")]);
        assert_eq!(pairs("'right' <2> 'vote' & 'law'"), vec![p("right", "vote"), p("vote", "law")]);
    }

    #[test]
    fn nested_phrases_flatten_with_postgres_widths() {
        let alts = flatten_phrase(&parse_text("'a' <-> ( 'b' <2> 'c' )").unwrap().unwrap()).unwrap();
        assert_eq!(alts, vec![vec![(0, "a".to_string(), false), (1, "b".to_string(), false), (3, "c".to_string(), false)]]);
        let alts = flatten_phrase(&parse_text("( 'a' | 'b' ) <-> 'c'").unwrap().unwrap()).unwrap();
        assert_eq!(alts.len(), 2);
        assert!(flatten_phrase(&parse_text("'a' <-> !'b'").unwrap().unwrap()).is_err());
    }
}
